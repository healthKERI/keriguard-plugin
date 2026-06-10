# -*- encoding: utf-8 -*-
"""keriguard.plugin — KERIGuardPlugin for the Locksmith application."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from keri import help

from locksmith.plugins.base import PluginBase, AccountProviderPlugin
from locksmith.ui.vault.menu import MenuButton, MenuSpacer
from locksmith.ui.toolkit.widgets.buttons import BackButton

from .db.basing import KERIGuardBaser, sync_account_to_keriguard

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.core.vaulting import Vault
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class KERIGuardPlugin(PluginBase, AccountProviderPlugin):
    """Locksmith plugin for the KERIGuard platform."""

    @property
    def plugin_id(self) -> str:
        return "keriguard"

    def initialize(self, app: "LocksmithApplication", parent) -> None:
        self._app = app
        self.parent = parent
        self._db: KERIGuardBaser | None = None
        self._pages: dict[str, QWidget] = {}
        self._build_pages(app)
        self._build_menu()

    def _build_pages(self, app: "LocksmithApplication") -> None:
        from .machines.list import MachinesListPage
        from .machines.detail import MachineDetailPage
        from .connections.list import ConnectionsListPage
        from .connections.detail import ConnectionDetailPage
        from .settings import KERIGuardSettingsPage

        machines_list = MachinesListPage(app, self.parent)
        machine_detail = MachineDetailPage(app, self.parent)
        connections_list = ConnectionsListPage(app, self.parent)
        connection_detail = ConnectionDetailPage(app, self.parent)

        self._pages = {
            "keriguard_machines": machines_list,
            "keriguard_machine_detail": machine_detail,
            "keriguard_connections": connections_list,
            "keriguard_connection_detail": connection_detail,
            "keriguard_settings": KERIGuardSettingsPage(app, self.parent),
            "keriguard_placeholder": KERIGuardPlaceholderPage("KERIGuard", self.parent),
        }

        machines_list.view_machine.connect(self._on_view_machine)
        machine_detail.back_clicked.connect(self._on_back_to_machines)
        machine_detail.view_connection.connect(self._on_view_connection)
        connections_list.view_connection.connect(self._on_view_connection)
        connection_detail.back_clicked.connect(self._on_back_to_connections)

    def on_vault_opened(self, vault: "Vault") -> None:
        self._db = KERIGuardBaser(name=vault.hby.name, reopen=True)

        _, account = next(self._db.keriguardAccounts.getItemIter(), (None, None))
        _, team = next(self._db.keriguardTeams.getItemIter(), (None, None))

        vault.plugin_state["keriguard"] = {
            "account": account,
            "team": team,
            "db": self._db,
        }

        if hasattr(vault, "signals") and vault.signals:
            vault.signals.doer_event.connect(self._on_vault_doer_event)

    def on_vault_closed(self, vault: "Vault") -> None:
        if hasattr(vault, "signals") and vault.signals:
            try:
                vault.signals.doer_event.disconnect(self._on_vault_doer_event)
            except (RuntimeError, TypeError):
                pass

        vault.plugin_state.pop("keriguard", None)

        if self._db:
            self._db.close()
            self._db = None

    def _on_vault_doer_event(self, doer_name: str, event_type: str, data: dict) -> None:
        if doer_name == "TeamCreationPage" and event_type == "hk_team_created":
            logger.info("KERIGuardPlugin: healthKERI account created — syncing")
            sync_account_to_keriguard(self._app)

    def _build_menu(self) -> None:
        self._account_button = MenuButton(
            QIcon(":/assets/custom/logos/keriguard-darkmode.png"),
            "KERIGuard"
        )
        self._account_button.is_account_btn = True
        self._keriguard_submenu_items = self._create_submenu_items()

    def _on_view_machine(self, said: str) -> None:
        detail_page = self._pages.get("keriguard_machine_detail")
        if detail_page:
            detail_page.load_machine(said)
            self._navigate("keriguard_machine_detail")

    def _on_back_to_machines(self) -> None:
        self._navigate("keriguard_machines")
        list_page = self._pages.get("keriguard_machines")
        if list_page and hasattr(list_page, "on_show"):
            list_page.on_show()

    def _on_view_connection(self, said: str) -> None:
        detail = self._pages.get("keriguard_connection_detail")
        if detail:
            detail.load_connection(said)
            self._navigate("keriguard_connection_detail")

    def _on_back_to_connections(self) -> None:
        self._navigate("keriguard_connections")
        page = self._pages.get("keriguard_connections")
        if page and hasattr(page, "on_show"):
            page.on_show()

    def _create_submenu_items(self) -> list[QWidget]:
        items: list[QWidget] = []
        items.append(BackButton(dark_mode=False))
        items.append(MenuSpacer(15))

        nav_buttons_config = [
            (":/assets/material-icons/devices.svg", "Machines", "keriguard_machines"),
            (":/assets/material-icons/airline_stops.svg", "Connections", "keriguard_connections"),
            (":/assets/material-icons/settings-hover.svg", "Settings", "keriguard_settings"),
        ]

        self._nav_buttons_by_page: dict[str, MenuButton] = {}
        for icon_path, label, page_key in nav_buttons_config:
            btn = MenuButton(QIcon(icon_path), label)
            btn.clicked.connect(self._make_nav_handler(page_key, btn))
            items.append(btn)
            self._nav_buttons_by_page[page_key] = btn

        return items

    def _make_nav_handler(self, page_key: str, button: MenuButton):
        def handler():
            for item in self._keriguard_submenu_items:
                if isinstance(item, MenuButton):
                    item.set_active(False)
            button.set_active(True)
            self._navigate(page_key)
            page = self._pages.get(page_key)
            if page and hasattr(page, "on_show"):
                page.on_show()
        return handler

    def _navigate(self, page_key: str) -> None:
        vault_page = self._get_vault_page()
        if vault_page:
            vault_page._show_page(page_key)

    def _get_vault_page(self):
        if hasattr(self._app, "_vault_page"):
            return self._app._vault_page
        return None

    def get_menu_entry(self) -> MenuButton:
        return self._account_button

    def get_menu_section(self) -> list[QWidget]:
        return self._keriguard_submenu_items

    def get_pages(self) -> dict[str, QWidget]:
        return self._pages

    def is_setup_complete(self, vault: "Vault") -> bool:
        return True

    def get_setup_page(self, vault: "Vault") -> tuple[str, bool]:
        return ("keriguard_machines", True)


class KERIGuardPlaceholderPage(QWidget):
    """Placeholder for unimplemented KERIGuard sub-pages."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.vault_name = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #333;")
        layout.addWidget(title_label)
        placeholder_label = QLabel("This plugin requires a healthKERI account.")
        placeholder_label.setStyleSheet("font-size: 14px; color: #666;")
        layout.addWidget(placeholder_label)
        layout.addStretch()

    def set_vault_name(self, vault_name: str):
        self.vault_name = vault_name