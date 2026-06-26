# -*- encoding: utf-8 -*-
"""keriguard.core.remoting — Remote API calls to the registrar."""
import httpx
from keri import help
from keri.app.httping import CESR_CONTENT_TYPE

logger = help.ogler.getLogger(__name__)


async def push_credential_to_registrar(grant: bytes, registrar_url: str) -> None:
    """PUT CESR grant bytes to the registrar. Raises httpx.HTTPError on failure."""
    async with httpx.AsyncClient() as client:
        response = await client.put(
            registrar_url,
            content=grant,
            headers={"Content-Type": CESR_CONTENT_TYPE},
            timeout=30.0,
        )
        response.raise_for_status()
    logger.info(f"Credential pushed to registrar at {registrar_url} (HTTP {response.status_code})")


async def push_introduction_to_registrar(
    introduction: bytes, registrar_url: str
) -> None:
    """PUT CESR introduction bytes to the registrar."""
    async with httpx.AsyncClient() as client:
        response = await client.put(
            registrar_url,
            content=introduction,
            headers={"Content-Type": CESR_CONTENT_TYPE},
            timeout=30.0,
        )
        response.raise_for_status()
    logger.info(f"Introduction pushed to registrar at {registrar_url}")


async def push_credential_via_essr(
    grant: bytes,
    essr,
    introduction: bytes | None = None,
) -> None:
    """PUT CESR grant bytes to hkweb's /registrar/ endpoint via ESSR."""
    response = await essr.request(
        path="/registrar/",
        method="PUT",
        data=grant,
        headers={"Content-Type": CESR_CONTENT_TYPE},
        timeout=30,
    )
    if response is None or response.status_code not in (200, 204):
        status = response.status_code if response else "None"
        raise RuntimeError(f"hkweb /registrar/ returned {status}")
    logger.info("Credential pushed to hkweb /registrar/ via ESSR")

    if introduction:
        response = await essr.request(
            path="/registrar/",
            method="PUT",
            data=introduction,
            headers={"Content-Type": CESR_CONTENT_TYPE},
            timeout=30,
        )
        if response is None or response.status_code not in (200, 204):
            status = response.status_code if response else "None"
            raise RuntimeError(f"hkweb /registrar/ introduction returned {status}")
        logger.info("Introduction pushed to hkweb /registrar/ via ESSR")