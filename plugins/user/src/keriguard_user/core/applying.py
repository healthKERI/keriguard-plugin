# -*- encoding: utf-8 -*-
"""keriguard_user.core.applying — WireGuardApplier wrapping keriguard CredService."""
from __future__ import annotations

from typing import TYPE_CHECKING

from keri import help

from keriguard.app.sentinel.services.cred_service import CredService
from keriguard.core.systeming import WireGuardNotApprovedError
from keriguard.core.wireguarding import Schema, PeerResolutionPendingError

if TYPE_CHECKING:
    pass

logger = help.ogler.getLogger(__name__)


def _get_all_saids(rgy) -> set:
    """Return set of all interface + connection SAIDs in rgy."""
    saids = set()
    for schema in (Schema.INTERFACE_SCHEMA, Schema.CONNECTION_SCHEMA):
        for saider in (rgy.reger.schms.get(keys=schema) or []):
            saids.add(saider.qb64)
    return saids


class WireGuardApplier:
    """Applies received credentials to the local WireGuard configuration."""

    def __init__(self, hby, rgy, kgb, config_dir: str, watcher_hab,
                 registrar_url: str | None = None, watcher=None,
                 credential_source: str = "registrar", essr=None):
        self.hby = hby
        self.rgy = rgy
        self.registrar_url = registrar_url
        self.credential_source = credential_source
        self._essr = essr
        self._watcher = watcher
        self._registrar_identity_resolved = False
        self.cred_service = CredService(
            hby=hby,
            rgy=rgy,
            kgb=kgb,
            config_dir=config_dir,
            hab=watcher_hab,
            sentinel_aid=watcher_hab.pre,
        )

    def set_essr(self, essr) -> None:
        """Activate ESSR-backed peer OOBI resolution once a client becomes available."""
        self._essr = essr

    async def _fetch_oobi(self, aid: str) -> bytes | None:
        """Fetch OOBI CESR bytes for aid from the registrar.

        Uses the ESSR client against hkweb's /registrar/oobi/{aid} in healthKERI
        mode (those routes are ESSR-protected), or a plain HTTP GET against the
        standalone registrar_url otherwise. Returns None if unavailable.
        """
        if self.credential_source == "healthKERI":
            if self._essr is None:
                logger.debug(
                    f"WireGuardApplier: no ESSR client available yet, cannot "
                    f"resolve AID {aid[:16]}…"
                )
                return None
            try:
                resp = await self._essr.request(
                    path=f"/registrar/oobi/{aid}", method="GET"
                )
            except Exception as exc:
                logger.debug(
                    f"WireGuardApplier: ESSR OOBI fetch failed for "
                    f"{aid[:16]}…: {exc}"
                )
                return None
            if resp is None or resp.status_code != 200:
                status = resp.status_code if resp else "None"
                logger.debug(
                    f"WireGuardApplier: /registrar/oobi/{aid[:16]}… "
                    f"returned {status}"
                )
                return None
            return resp.content

        if not self.registrar_url:
            return None

        import httpx

        oobi_url = self.registrar_url.rstrip("/") + f"/oobi/{aid}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(oobi_url)
        except Exception as exc:
            logger.debug(
                f"WireGuardApplier: OOBI fetch failed for {aid[:16]}…: {exc}"
            )
            return None
        if resp.status_code != 200:
            logger.debug(
                f"WireGuardApplier: /oobi/{aid[:16]}… returned {resp.status_code}"
            )
            return None
        return resp.content

    async def _parse_oobi_content(self, content: bytes) -> None:
        """Parse OOBI CESR bytes, writing resulting KEL/reply data into hby.db.

        Uses the Watcher's parser when available — it writes to hby.db (same
        backend as hby.kevers) via a non-local, lax Kevery that accepts remote
        events — otherwise falls back to a minimal parser built from hby's
        infrastructure.
        """
        psr = self._watcher.psr if self._watcher else None
        if psr:
            psr.parse(content)
            self.hby.kvy.processEscrows()
            if hasattr(self.hby, "rvy") and self.hby.rvy:
                self.hby.rvy.processEscrowReply()
        else:
            from keri.core import parsing, routing
            from keri.vdr.eventing import Tevery
            from keri.vdr import verifying
            rtr = routing.Router()
            rvy = routing.Revery(db=self.hby.db, rtr=rtr, lax=True, local=False)
            tvy = Tevery(db=self.hby.db, reger=self.rgy.reger, lax=True, local=False)
            vry = verifying.Verifier(hby=self.hby, reger=self.rgy.reger)
            fallback_psr = parsing.Parser(
                framed=True, kvy=self.hby.kvy, tvy=tvy, rvy=rvy, vry=vry
            )
            fallback_psr.parse(content)
            self.hby.kvy.processEscrows()
            rvy.processEscrowReply()

    async def _ensure_registrar_identity(self) -> None:
        """One-time bootstrap (healthKERI mode only): resolve hkweb's registrar
        hab's own AID and KEL into hby.kevers.

        get_oobi_cesr's response for *any* AID includes /end/role/add
        attestations freshly signed by hkweb's registrar hab (see
        keri.app.habbing.Hab.replyEndRole -> makeEndRole). Without this AID's
        own KEL resolved locally, Revery can never verify those signatures and
        escrows them forever ("escrowing without key state for signer"),
        regardless of how many times the OOBI is re-fetched.
        """
        if self._registrar_identity_resolved or self.credential_source != "healthKERI":
            return
        if self._essr is None:
            return

        try:
            resp = await self._essr.request(path="/registrar/", method="GET")
        except Exception as exc:
            logger.debug(f"WireGuardApplier: could not fetch registrar identity: {exc}")
            return
        if resp is None or resp.status_code != 200:
            return
        try:
            registrar_aid = resp.json().get("aid", "")
        except Exception as exc:
            logger.debug(f"WireGuardApplier: could not parse registrar identity: {exc}")
            return
        if not registrar_aid:
            return

        if registrar_aid not in self.hby.kevers:
            content = await self._fetch_oobi(registrar_aid)
            if content is None:
                return
            try:
                await self._parse_oobi_content(content)
            except Exception as exc:
                logger.warning(f"WireGuardApplier: could not parse registrar OOBI: {exc}")
                return

        if registrar_aid in self.hby.kevers:
            self._registrar_identity_resolved = True
            logger.info(
                f"WireGuardApplier: registrar identity {registrar_aid[:16]}… "
                f"resolved; attestations it signs can now be verified"
            )
        else:
            logger.warning(
                f"WireGuardApplier: registrar OOBI fetched for {registrar_aid[:16]}… "
                f"but AID not in kevers after parsing"
            )

    async def _resolve_peer_aids(self, creder) -> None:
        """Pre-resolve any peer AIDs from a connection credential into kevers.

        Fetches the peer AID's OOBI from the registrar (via ESSR in healthKERI
        mode, or plain HTTP in standalone registrar mode), parses it through the
        Watcher's parser (which writes to hby.db), and registers the AID with
        the embedded Watcher for ongoing key-state monitoring.

        Must be called before process_connection_credential so that
        PeerAIDMissingError is never raised in CredService.
        """
        if self.credential_source != "healthKERI" and not self.registrar_url:
            return

        if self.credential_source == "healthKERI":
            await self._ensure_registrar_identity()

        edges = creder.sad.get("e", {})
        for peer_key in ("peer1", "peer2"):
            iface_said = edges.get(peer_key, {}).get("n", "")
            if not iface_said:
                continue
            try:
                iface_creder, *_ = self.rgy.reger.cloneCred(said=iface_said)
                peer_aid = iface_creder.attrib.get("i", "")
            except Exception:
                continue

            if not peer_aid or peer_aid in self.hby.kevers:
                continue

            content = await self._fetch_oobi(peer_aid)
            if content is None:
                continue

            try:
                await self._parse_oobi_content(content)

                if peer_aid in self.hby.kevers:
                    logger.info(
                        f"WireGuardApplier: peer AID {peer_aid[:16]}… "
                        f"resolved via registrar OOBI"
                    )
                    if self._watcher:
                        self._watcher.watch(peer_aid)
                        logger.info(
                            f"WireGuardApplier: {peer_aid[:16]}… "
                            f"added to embedded watcher for ongoing monitoring"
                        )
                else:
                    logger.warning(
                        f"WireGuardApplier: OOBI fetched for {peer_aid[:16]}… "
                        f"but AID not in kevers after parsing"
                    )

            except Exception as exc:
                logger.warning(
                    f"WireGuardApplier: could not resolve peer AID "
                    f"{peer_aid[:16]}…: {exc}"
                )

    async def apply(self, said: str) -> str:
        """
        Apply a credential to the WireGuard config.

        Returns one of: "applied" | "pending_ne_approval" | "pending_oobi" | "error"
        """
        try:
            creder, *_ = self.rgy.reger.cloneCred(said=said)
        except Exception as exc:
            logger.warning(f"WireGuardApplier: could not load credential {said}: {exc}")
            return "error"

        schema = creder.sad.get("s", "")
        try:
            if schema == Schema.INTERFACE_SCHEMA:
                logger.info(f"WireGuardApplier: applying interface credential {said[:16]}…")
                await self.cred_service.process_interface_credential(said, creder)
            elif schema == Schema.CONNECTION_SCHEMA:
                logger.info(f"WireGuardApplier: applying connection credential {said[:16]}…")
                await self._resolve_peer_aids(creder)
                await self.cred_service.process_connection_credential(said, creder)
            else:
                logger.debug(f"WireGuardApplier: unknown schema {schema}, skipping {said}")
                return "error"
            return "applied"
        except PeerResolutionPendingError:
            logger.info(
                f"WireGuardApplier: peer AID resolution pending for {said[:16]}…; will retry"
            )
            return "pending_oobi"
        except WireGuardNotApprovedError:
            logger.info(
                f"WireGuardApplier: KERIGuard Helper's network extension not yet "
                f"approved for {said[:16]}…; will retry"
            )
            return "pending_ne_approval"
        except Exception as exc:
            logger.warning(f"WireGuardApplier: error applying {said}: {exc}")
            return "error"
