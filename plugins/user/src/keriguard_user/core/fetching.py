# -*- encoding: utf-8 -*-
"""keriguard_user.core.fetching — CredentialPoller wrapping sentinel loader classes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from keri import help

from keriguard.core.wireguarding import Schema

if TYPE_CHECKING:
    from keriguard_user.db.basing import KERIGuardUserSettings

logger = help.ogler.getLogger(__name__)


def _all_saids(rgy) -> set:
    """Return set of all interface + connection SAIDs currently in rgy."""
    saids = set()
    for schema in (Schema.INTERFACE_SCHEMA, Schema.CONNECTION_SCHEMA):
        for saider in (rgy.reger.schms.get(keys=schema) or []):
            saids.add(saider.qb64)
    return saids


class CredentialPoller:
    """Polls for new credentials from a registrar or healthKERI SaaS service."""

    def __init__(self, hby, hab, rgy, settings: "KERIGuardUserSettings", essr=None):
        self.rgy = rgy
        self.issuer_aid = settings.issuer_aid
        self._saas = False

        if settings.credential_source == "healthKERI" and essr is not None:
            from sentinel.core.credentialing import SaaSCredentialLoader
            self.loader = SaaSCredentialLoader(
                hby=hby,
                hab=hab,
                rgy=rgy,
                export_dir=settings.export_dir,
                essr=essr,
            )
            self._saas = True
        else:
            from sentinel.core.credentialing import CredentialLoader
            self.loader = CredentialLoader(
                hby=hby,
                hab=hab,
                rgy=rgy,
                export_dir=settings.export_dir,
                registrar_url=settings.registrar_url,
            )

    async def poll_once(self, hby) -> list[str]:
        """Search for new credentials. Returns list of newly loaded SAIDs."""
        if self.issuer_aid not in hby.kevers:
            logger.debug(f"CredentialPoller: issuer {self.issuer_aid} not yet in kevers")
            return []

        sn = hby.kevers[self.issuer_aid].sner.num
        before = _all_saids(self.rgy)

        try:
            await self.loader.search_for_credentials(self.issuer_aid, sn)
        except Exception as exc:
            logger.warning(f"CredentialPoller: search_for_credentials failed: {exc}")
            return []

        return list(_all_saids(self.rgy) - before)