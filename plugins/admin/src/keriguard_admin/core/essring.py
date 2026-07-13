# -*- encoding: utf-8 -*-
"""
keriguard_admin.core.essring — Encrypt Sender / Sign Receiver (ESSR) client.

Local copy of locksmith.core.essring (not kept.hk.essring). kept's AsyncTCPClient
uses asyncio.open_connection(), which is unreliable under the qasync/Qt event
loop and can raise `OSError: [Errno 22] Invalid argument` on a loopback connect.
Locksmith's own core/essring.py works around this by using blocking sockets
wrapped in asyncio.to_thread() instead; this module mirrors that fix so the
plugin doesn't depend on kept's TCP client for ESSR traffic.
"""

import asyncio
import json as fjson
import math
import random
import socket
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import cbor
import pysodium
import requests
from keri import core, help
from keri.core import parsing, serdering, coring, counting
from keri.help import helping
from keri.kering import Vrsn_1_0
from keri.peer import exchanging

logger = help.ogler.getLogger(__name__)


class AsyncTCPClient:
    """
    Async TCP client using synchronous sockets in a thread executor.

    qasync has compatibility issues with asyncio.open_connection(), so we use
    synchronous sockets wrapped in asyncio.to_thread() for reliable operation
    under the Qt event loop.
    """

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._socket: Optional[socket.socket] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Connect to the TCP server using a thread executor."""
        def _connect():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.host, self.port))
                return sock
            except Exception as e:
                logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
                return None

        try:
            self._socket = await asyncio.to_thread(_connect)
            if self._socket:
                logger.debug(f"Connected to {self.host}:{self.port}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the TCP server."""
        if self._socket:
            def _close():
                try:
                    self._socket.close()
                except Exception:
                    pass
            await asyncio.to_thread(_close)
        self._socket = None

    async def send(self, data: bytes) -> bool:
        """Send data to the server using a thread executor."""
        if not self._socket:
            logger.error("Not connected to server")
            return False

        def _send():
            try:
                self._socket.sendall(data)
                return True
            except Exception as e:
                logger.error(f"Failed to send data: {e}")
                return False

        async with self._lock:
            result = await asyncio.to_thread(_send)
            if result:
                logger.debug(f"Sent {len(data)} bytes")
            return result

    async def receive(self, buffer_size: int = 4096) -> Optional[bytes]:
        """Receive data from the server using a thread executor."""
        if not self._socket:
            logger.error("Not connected to server")
            return None

        def _recv():
            try:
                return self._socket.recv(buffer_size)
            except Exception as e:
                logger.error(f"Failed to receive data: {e}")
                return None

        async with self._lock:
            data = await asyncio.to_thread(_recv)
            if data:
                logger.debug(f"Received {len(data)} bytes")
            return data


CHUNK_SIZE = 65536


class APIClient:
    """ESSR API client using native asyncio (thread-executor sockets) for Qt compatibility."""

    def __init__(self, url, root, hby, hab, timeout: int = 10):
        self.hby = hby
        self.hab = hab
        self.url = url
        self.timeout = timeout

        up = urlparse(url)

        self.hostname = up.hostname
        self.port = up.port
        self.root = root

    async def request(self, path="/", method="GET", data: str | bytes | None = None, json=None, files=None,
                      headers=None, timeout: int = 30) -> requests.Response:
        """ Execute request using HTTP tunneled over ESSR/TCP

        Parameters:
            path: (str): request path with optional query string after ?, defaults to "/"
            method: (str): HTTP request method, defaults to "GET"
            data (str | bytes): raw data
            json (dict): dictionary data to convert to JSON
            files (dict): multipart data
            headers (dict): HTTP headers
            timeout (int): timeout in seconds, defaults to 30 seconds, no timeout

        Returns:
            requests.Response: HTTP response

        """
        logger.debug(f"ESSR request starting: {method} {path}")

        if isinstance(data, str):
            data = data.encode("utf-8")

        headers = headers or {}

        req, reqid = self.http(path, method, data, json, files, headers)
        ims = self.essr(req)

        client = AsyncTCPClient(self.hostname, self.port)

        try:
            # Connect to the server
            if await client.connect():
                # Send the request
                if await client.send(ims):
                    logger.debug(f"Request sent successfully to {self.hostname}:{self.port}")

                    # Wait for response with timeout
                    try:
                        rep, dig = await asyncio.wait_for(
                            self._read_and_parse(client, reqid, timeout=timeout),
                            timeout=timeout
                        )
                        logger.debug(f"Response received: rep={rep is not None}, dig={dig}")

                        if rep is None:
                            logger.error(f"_read_and_parse returned None for rep! This should not happen.")
                            raise RuntimeError("Failed to parse ESSR response - rep is None")

                        response = requests.Response()
                        response.reason = rep["reason"]
                        response.status_code = rep["status"]
                        for k, v in rep["headers"].items():
                            response.headers[k] = v

                        # Set both _content and raw for proper response handling
                        body_bytes = rep["body"]
                        response._content = body_bytes
                        response.raw = BytesIO(body_bytes)

                        logger.debug(f"Response built successfully: status={response.status_code}, body_len={len(body_bytes)}")
                        return response

                    except asyncio.TimeoutError:
                        logger.error(
                            f"Timeout after {timeout} seconds waiting for response"
                        )
                        raise TimeoutError(
                            f"Timeout after {timeout} seconds waiting for response"
                        )
                    except Exception as e:
                        logger.exception(f"Unexpected error processing ESSR response: {e}")
                        raise
                else:
                    logger.error(f"Failed to send request to {self.hostname}:{self.port}")
                    raise ConnectionError(
                        f"Failed to send request to {self.hostname}:{self.port}"
                    )

            else:
                logger.error(f"Failed to connect to {self.hostname}:{self.port}")
                raise ConnectionError(f"Failed to connect to {self.hostname}:{self.port}")
        except Exception as e:
            logger.error(f"ESSR request failed with exception: {type(e).__name__}: {e}")
            raise
        finally:
            # Ensure client is disconnected
            await client.disconnect()

    async def _read_and_parse(self, client: AsyncTCPClient, reqid, timeout: int | None = None):
        """ Read and parse response from ESSR/TCP"""
        logger.debug(f"_read_and_parse starting for reqid={reqid}")

        # Create parser with shared buffer
        ims = bytearray()
        parser = parsing.Parser(ims=ims, framed=True)

        ack = AckHandler()
        fwd = ForwardHandler(hby=self.hby, hab=self.hab, parser=parser)
        decoder = DecodeHandler(hby=self.hby, hab=self.hab)

        exc = exchanging.Exchanger(hby=self.hby, handlers=[ack, fwd, decoder])
        parser.exc = exc

        # Create the parser generator
        parsator = parser.onceParsator(ims=ims, framed=True, exc=exc)

        # Prime the generator (advance to first yield)
        try:
            next(parsator)
        except StopIteration:
            pass  # Parser completed immediately (shouldn't happen)

        # Read and parse continuously until we get our response
        chunks_received = 0
        while decoder.dig != reqid:
            try:
                # Receive chunk of data
                buf = await client.receive(4096)
                chunks_received += 1

                if not buf:
                    # Connection closed without receiving response
                    logger.error(f"Connection closed before receiving response {reqid} after {chunks_received} chunks")
                    logger.error(f"decoder.dig={decoder.dig}, decoder.rep={decoder.rep}")
                    break

                logger.debug(f"Received chunk {chunks_received}: {len(buf)} bytes")

                # Append to shared buffer
                ims.extend(buf)

                # Drive the parser forward one step
                try:
                    next(parsator)
                except StopIteration:
                    # Parser finished this parse attempt
                    # Recreate and prime parser for next batch of messages
                    parsator = parser.onceParsator(ims=ims, framed=True, exc=exc)
                    next(parsator)

            except Exception as e:
                logger.exception(f"Error reading/parsing data: {e}")
                logger.error(f"Current state: chunks_received={chunks_received}, decoder.dig={decoder.dig}, reqid={reqid}")
                return None, None

        logger.debug(f"_read_and_parse completed: decoder.dig={decoder.dig}, decoder.rep is None={decoder.rep is None}")
        return (
            decoder.rep,
            decoder.dig
        )

    async def close(self):
        pass

    def http(self, path, method, data: bytes | None = None, json=None, files=None, headers=None):
        headers = headers if headers is not None else {}

        if data is not None:
            raw = data
            headers["CONTENT-LENGTH"] = len(raw)
        elif json is not None:
            raw = fjson.dumps(json).encode("utf-8")
            headers["CONTENT-TYPE"] = "application/json"
            headers["CONTENT-LENGTH"] = len(raw)
        elif files is not None:
            boundary = '____________{0:012x}'.format(random.randint(123456789,
                                                                    0xffffffffffff))

            form_parts = []
            # mime parts always start with --
            for k, (file, data, contentType) in files.items():
                if hasattr(data, "decode"):
                    data = data.decode("utf-8")

                form_parts.append('\r\n--{0}\r\nContent-Disposition: '
                                 'form-data; name="{1}"\r\n'
                                 'Content-Type: {2}; charset=utf-8\r\n'
                                 '\r\n{3}'.format(boundary, k, contentType, data))
            form_parts.append('\r\n--{0}--'.format(boundary))
            form = "".join(form_parts)
            raw = form.encode('utf-8')
            headers["CONTENT-TYPE"] = 'multipart/form-data; boundary={0}'.format(boundary)
            headers["CONTENT-LENGTH"] = len(raw)
        else:
            raw = b''

        headers["ESSR-SENDER"] = self.hab.pre
        reqid = coring.randomNonce()

        pp = urlparse(path)
        path = pp.path
        method = method
        query = pp.query

        # Must create an exn `/http/req` route
        payload = http_request(
            scheme="HTTP",  # Hard code because it doesn't matter
            method=method,
            host=self.hostname,
            port=self.port,
            path=path,
            query_string=query,
            remote_addr="",
            headers=headers,
            content_type=headers['CONTENT-TYPE'] if 'CONTENT-TYPE' in headers else 'text/plain',
            raw=raw,
            reqid=reqid,
        )

        if "CONTENT-LENGTH" in headers:
            payload["contentLength"] = headers['CONTENT-LENGTH']

        return dict(i=self.hab.pre, a=payload), reqid

    def essr(self, payload):
        rkever = self.hab.kevers[self.root]

        # convert signing public key to encryption public key
        pubkey = pysodium.crypto_sign_pk_to_box_pk(rkever.verfers[0].raw)
        raw = pysodium.crypto_box_seal(cbor.dumps(payload), pubkey)
        diger = coring.Diger(ser=raw, code=coring.MtrDex.Blake3_256)

        exn, _ = exchanging.exchange(route="/essr/req",
                                     diger=diger,
                                     sender=self.hab.pre,
                                     recipient=rkever.prefixer.qb64,  # Must sign receiver
                                     date=helping.nowIso8601(),
                                     version=Vrsn_1_0)

        ims = self.hab.endorse(serder=exn, pipelined=False)

        size = len(raw)
        chunks = math.ceil(size / CHUNK_SIZE)
        ims.extend(core.Counter(code=counting.CtrDex_1_0.ESSRPayloadGroup, count=chunks, gvrsn=Vrsn_1_0).qb64b)
        for idx in range(chunks):
            start = idx * CHUNK_SIZE
            end = start + CHUNK_SIZE
            texter = coring.Matter(raw=raw[start:end], code=coring.MtrDex.Bytes_L0)
            ims.extend(texter.qb64b)

        fwd, atc = exchanging.exchange(route='/fwd', modifiers=dict(),
                                       payload=dict(src=exn.ked['i'], dest=exn.ked['rp'], ctx={}),
                                       embeds=dict(evt=ims),
                                       sender=self.hab.pre)

        ims = self.hab.endorse(serder=fwd, last=False, pipelined=False)
        ims.extend(atc)
        self.hab.db.epath.rem(keys=(fwd.said,))

        return ims


class AckHandler:
    """Handler for acknowledgement `exn` messages."""

    resource = "/ack"

    def __init__(self):
        pass

    @staticmethod
    def handle(serder, attachments=None):
        said = serder.ked['d']
        dig = serder.ked['p']
        logger.debug(f"ack={said} received for exn message {dig}")


class ForwardHandler:
    """Handler for forward `exn` messages."""

    resource = "/fwd"

    def __init__(self, hby, hab, parser, params=None):
        self.hby = hby
        self.hab = hab
        self.parser = parser
        self.said = None
        self.payload = None
        self.params = params or {}

    def handle(self, serder, attachments=None):
        embeds = serder.ked['e']
        if attachments:
            ims = bytearray()
            for pather, atc in attachments:
                sad = pather.resolve(embeds)
                embed = serdering.SerderKERI(sad=sad)
                ims.extend(embed.raw)
                ims.extend(atc)
        else:
            return

        self.parser.parseOne(ims=ims)


class DecodeHandler:
    """Handler for ESSR encoded `exn` messages."""

    resource = "/essr/req"

    def __init__(self, hby, hab, params=None):
        self.hby = hby
        self.hab = hab
        self.rep = None
        self.dig = None
        self.params = params or {}

    def handle(self, serder, attachments=None, essr=None):
        # Get the encrypted payload
        if essr:
            data = essr
        else:
            enc = serder.ked['a']['d']
            data = coring.Texter(qb64=enc).raw

        rp = serder.ked['rp']

        # Ensure the signed receiver is us
        if self.hab.pre != rp:
            logger.error(f"dessr: invalid /essr/req message, rp={rp} not one of us={self.hab.pre}")
            return

        # Decrypt it with our dest hab
        raw = self.hab.decrypt(data)
        req = cbor.loads(raw)

        payload = req['a']

        rep = payload['response']
        atc = bytearray(payload['atc'].encode("utf-8"))

        body = bytearray()

        if atc:
            counter = counting.Counter(qb64b=atc, strip=True)
            for _ in range(counter.count):
                body.extend(core.Texter(qb64b=atc, strip=True).raw)

        rep['body'] = body

        self.rep = rep
        self.dig = payload["reqid"]

        self.hab.db.essrs.rem(keys=(serder.said,))
        self.hab.db.epath.rem(keys=(serder.said,))


def http_request(scheme, method, host, port, path="/", raw=b'', query_string="", remote_addr="",
                 headers=None, content_type="text/html", content_length=None, reqid=""):

    headers = headers if headers is not None else dict()

    if raw:
        dig = core.Diger(ser=raw, code=core.MtrDex.Blake3_256).qb64
    else:
        dig = ""

    payload = dict(
        scheme=scheme,
        method=method,
        host=host,
        port=port,
        path=path,
        query=query_string,
        remote=remote_addr,
        headers=headers,
        contentType=content_type,
        body=dig,
        reqid=reqid,
    )

    if content_length is not None:
        payload["contentLength"] = content_length
    elif raw:
        payload["contentLength"] = len(raw)

    ims = bytearray()
    size = len(raw)
    chunks = math.ceil(size / CHUNK_SIZE)
    if chunks:
        ims.extend(counting.Counter(code=counting.CtrDex_1_0.ESSRPayloadGroup, count=chunks, gvrsn=Vrsn_1_0).qb64b)
        for idx in range(chunks):
            start = idx * CHUNK_SIZE
            end = start + CHUNK_SIZE
            texter = coring.Matter(raw=raw[start:end], code=coring.MtrDex.Bytes_L0)
            ims.extend(texter.qb64b)

    return dict(r="/http/req", request=payload, atc=ims.decode("utf-8"))