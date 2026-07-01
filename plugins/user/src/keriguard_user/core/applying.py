# -*- encoding: utf-8 -*-
"""keriguard_user.core.applying — WireGuardApplier wrapping keriguard CredService."""
from __future__ import annotations

from typing import TYPE_CHECKING

from keri import help

from keriguard.app.sentinel.services.cred_service import CredService
from keriguard.core.wireguarding import Schema

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

    def __init__(self, hby, rgy, kgb, config_dir: str, watcher_hab):
        self.hby = hby
        self.rgy = rgy
        self.cred_service = CredService(
            hby=hby,
            rgy=rgy,
            kgb=kgb,
            config_dir=config_dir,
            hab=watcher_hab,
            sentinel_aid=watcher_hab.pre,
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
                await self.cred_service.process_connection_credential(said, creder)
            else:
                logger.debug(f"WireGuardApplier: unknown schema {schema}, skipping {said}")
                return "error"
            return "applied"
        except PermissionError:
            logger.warning(f"WireGuardApplier: permission error applying {said} — sudoers not configured")
            return "pending_sudo"
        except Exception as exc:
            logger.warning(f"WireGuardApplier: error applying {said}: {exc}")
            return "error"