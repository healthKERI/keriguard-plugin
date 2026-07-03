# -*- encoding: utf-8 -*-
"""keriguard_user.core.applying — WireGuardApplier wrapping keriguard CredService."""
from __future__ import annotations

from typing import TYPE_CHECKING

from keri import help

from keriguard.app.sentinel.services.cred_service import CredService
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
                 registrar_url: str | None = None, watcher=None):
        self.hby = hby
        self.rgy = rgy
        self.registrar_url = registrar_url
        self._watcher = watcher
        self.cred_service = CredService(
            hby=hby,
            rgy=rgy,
            kgb=kgb,
            config_dir=config_dir,
            hab=watcher_hab,
            sentinel_aid=watcher_hab.pre,
        )

    async def _resolve_peer_aids(self, creder) -> None:
        """Pre-resolve any peer AIDs from a connection credential into kevers.

        Fetches the peer AID's OOBI from the registrar, parses it through the
        Watcher's parser (which writes to hby.db), and registers the AID with
        the embedded Watcher for ongoing key-state monitoring.

        Must be called before process_connection_credential so that
        PeerAIDMissingError is never raised in CredService.
        """
        if not self.registrar_url:
            return

        import httpx

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

            oobi_url = self.registrar_url.rstrip("/") + f"/oobi/{peer_aid}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(oobi_url)

                if resp.status_code != 200:
                    logger.debug(
                        f"WireGuardApplier: /oobi/{peer_aid[:16]}… "
                        f"returned {resp.status_code}"
                    )
                    continue

                # Use the Watcher's parser — it writes to hby.db (same backend
                # as hby.kevers) via a non-local, lax Kevery that accepts remote events.
                psr = self._watcher.psr if self._watcher else None
                if psr:
                    psr.parse(resp.content)
                    self.hby.kvy.processEscrows()
                    if hasattr(self.hby, "rvy") and self.hby.rvy:
                        self.hby.rvy.processEscrowReply()
                else:
                    # Fallback: build a minimal parser from hby's infrastructure
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
                    fallback_psr.parse(resp.content)
                    self.hby.kvy.processEscrows()
                    rvy.processEscrowReply()

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

        Returns one of: "applied" | "pending_sudo" | "pending_oobi" | "error"
        """
        try:
            creder, *_ = self.rgy.reger.cloneCred(said=said)
        except Exception as exc:
            logger.warning(f"WireGuardApplier: could not load credential {said}: {exc}")
            return "error"

        schema = creder.sad.get("s", "")
        try:
            if schema == Schema.INTERFACE_SCHEMA:
                await self.cred_service.process_interface_credential(said, creder)
            elif schema == Schema.CONNECTION_SCHEMA:
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
        except PermissionError:
            logger.warning(f"WireGuardApplier: permission error applying {said} — sudoers not configured")
            return "pending_sudo"
        except Exception as exc:
            logger.warning(f"WireGuardApplier: error applying {said}: {exc}")
            return "error"
