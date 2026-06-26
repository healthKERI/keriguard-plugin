# -*- encoding: utf-8 -*-
"""keriguard_client.plugin — KERIGuardClientPlugin for the Locksmith application."""
from __future__ import annotations

from typing import TYPE_CHECKING

from locksmith.plugins.base import PluginBase

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.core.vaulting import Vault
    from locksmith.ui.vault.page import VaultPage


class KERIGuardClientPlugin(PluginBase):
    """Locksmith plugin for KERIGuard client (end-user) functionality."""

    @property
    def plugin_id(self) -> str:
        return "keriguard_client"

    def initialize(self, app: "LocksmithApplication", parent) -> None:
        self._app = app
        self.parent = parent

    def on_vault_opened(self, vault: "Vault") -> None:
        pass

    def on_vault_closed(self, vault: "Vault") -> None:
        pass

    def get_pages(self) -> dict:
        return {}