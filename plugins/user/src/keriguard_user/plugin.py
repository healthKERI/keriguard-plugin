# -*- encoding: utf-8 -*-
"""keriguard_user.plugin — KERIGuardUserPlugin for the Locksmith application."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget
from keri import help

from locksmith.plugins.base import PluginBase, AccountProviderPlugin
from locksmith.ui.vault.menu import MenuButton, MenuSpacer
from locksmith.ui.toolkit.widgets.buttons import BackButton

from .db.basing import KERIGuardUserBaser

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.core.vaulting import Vault

logger = help.ogler.getLogger(__name__)


class KERIGuardUserPlugin(PluginBase, AccountProviderPlugin):
    """Locksmith plugin for KERIGuard recipient (user) functionality."""

    @property
    def plugin_id(self) -> str:
        return "keriguard_user"

    def initialize(self, app: "LocksmithApplication", parent) -> None:
        self._app = app
        self.parent = parent
        self._db: KERIGuardUserBaser | None = None
        self._kgb = None
        self._poller = None
        self._applier = None
        self._watcher = None
        self._sentinel_db = None
        self._poll_task: asyncio.Task | None = None
        self._pages: dict[str, QWidget] = {}
        self._build_pages(app)
        self._build_menu()

    def _build_pages(self, app: "LocksmithApplication") -> None:
        from .setup.page import SetupPage
        from .machines.list import MachinesListPage
        from .machines.detail import MachineDetailPage
        from .machines.import_ import ImportInterfaceCredentialPage
        from .connections.list import ConnectionsListPage
        from .connections.detail import ConnectionDetailPage
        from .connections.import_ import ImportConnectionCredentialPage
        from .settings import KERIGuardUserSettingsPage

        setup_page = SetupPage(app, self.parent)
        machines_list = MachinesListPage(app, self.parent)
        machine_detail = MachineDetailPage(app, self.parent)
        import_interface = ImportInterfaceCredentialPage(app, self.parent)
        connections_list = ConnectionsListPage(app, self.parent)
        connection_detail = ConnectionDetailPage(app, self.parent)
        import_connection = ImportConnectionCredentialPage(app, self.parent)
        settings_page = KERIGuardUserSettingsPage(app, self.parent)

        self._pages = {
            "keriguard_user_setup": setup_page,
            "keriguard_user_machines": machines_list,
            "keriguard_user_machine_detail": machine_detail,
            "keriguard_user_import_interface": import_interface,
            "keriguard_user_connections": connections_list,
            "keriguard_user_connection_detail": connection_detail,
            "keriguard_user_import_connection": import_connection,
            "keriguard_user_settings": settings_page,
        }

        # Setup completion
        setup_page.setup_complete.connect(self._on_setup_complete)
        setup_page.initialization_done.connect(self._on_initialization_done)

        # Machines navigation
        machines_list.view_machine.connect(self._on_view_machine)
        machines_list.import_clicked.connect(self._on_import_interface)
        machine_detail.back_clicked.connect(self._on_back_to_machines)
        machine_detail.view_connection.connect(self._on_view_connection)
        import_interface.back_clicked.connect(self._on_back_to_machines)
        import_interface.import_complete.connect(self._on_import_complete_machines)

        # Connections navigation
        connections_list.view_connection.connect(self._on_view_connection)
        connections_list.import_clicked.connect(self._on_import_connection)
        connection_detail.back_clicked.connect(self._on_back_to_connections)
        import_connection.back_clicked.connect(self._on_back_to_connections)
        import_connection.import_complete.connect(self._on_import_complete_connections)

    def on_vault_opened(self, vault: "Vault") -> None:
        self._db = KERIGuardUserBaser(name=vault.hby.name, reopen=True)

        try:
            from keriguard.db.basing import KERIGuardBaser
            self._kgb = KERIGuardBaser(name=vault.hby.name, reopen=True)
        except Exception as exc:
            logger.warning(f"KERIGuardUserPlugin: could not open KERIGuardBaser: {exc}")
            self._kgb = None

        settings = self._db.keriguardUserSettings.get(keys=("settings",))

        vault.plugin_state["keriguard_user"] = {
            "db": self._db,
            "kgb": self._kgb,
            "settings": settings,
        }

        if settings and settings.is_initialized:
            self._start_polling(vault, settings)

    def _start_polling(self, vault: "Vault", settings) -> None:
        watcher_hab = vault.hby.habByName(settings.watcher_alias) if settings.watcher_alias else None
        if watcher_hab is None:
            logger.warning("KERIGuardUserPlugin: watcher hab not found, skipping poll start")
            return

        essr = self._build_essr(vault)

        from .core.fetching import CredentialPoller
        from .core.applying import WireGuardApplier

        self._poller = CredentialPoller(
            hby=vault.hby,
            hab=watcher_hab,
            rgy=vault.rgy,
            settings=settings,
            essr=essr,
        )

        # Start the watcher first so its parser is available for peer OOBI resolution
        # in WireGuardApplier._resolve_peer_aids.
        self._start_watcher(vault, watcher_hab, settings)

        registrar_url = (
            settings.registrar_url
            if settings.credential_source == "registrar"
            else None
        )
        self._applier = WireGuardApplier(
            hby=vault.hby,
            rgy=vault.rgy,
            kgb=self._kgb,
            config_dir=settings.config_dir,
            watcher_hab=watcher_hab,
            registrar_url=registrar_url,
            watcher=self._watcher,
            credential_source=settings.credential_source,
            essr=essr,
        )

        vault.plugin_state["keriguard_user"]["applier"] = self._applier

        self._poll_task = asyncio.create_task(
            self._startup_and_poll(vault, settings),
            name="keriguard_user_poll",
        )

    def _start_watcher(self, vault: "Vault", watcher_hab, settings) -> None:
        from pathlib import Path
        try:
            from sentinel.core.witnessing import Watcher
            from sentinel.db.basing import SentinelBaser
        except ImportError as exc:
            logger.warning(f"KERIGuardUserPlugin: sentinel not available, skipping KEL watcher: {exc}")
            return

        try:
            sentinel_db_name = f"{vault.hby.name}-watcher"
            self._sentinel_db = SentinelBaser(name=sentinel_db_name, reopen=True)

            export_dir = settings.export_dir or str(Path.home() / ".keri" / "keriguard-kel")

            self._watcher = Watcher(
                db=self._sentinel_db,
                hby=vault.hby,
                hab=watcher_hab,
                rgy=vault.rgy,
                export_dir=export_dir,
                registrar_url=settings.registrar_url if settings.credential_source == "registrar" else None,
            )

            # Register the issuer AID for watching (idempotent — already in db.obvs on re-open)
            if settings.issuer_aid:
                self._watcher.watch(settings.issuer_aid)

            # Honour the configured kel_watch_interval
            self._watcher.start()
            if self._watcher.sentinel_launcher:
                self._watcher.sentinel_launcher.WATCHERRETRY = getattr(
                    settings, "kel_watch_interval", 30
                )

            logger.info(
                f"KERIGuardUserPlugin: KEL watcher started for issuer {settings.issuer_aid[:16]}… "
                f"(interval={getattr(settings, 'kel_watch_interval', 30)}s)"
            )
        except Exception as exc:
            logger.exception(f"KERIGuardUserPlugin: could not start KEL watcher: {exc}")

    def _build_essr(self, vault: "Vault"):
        hk_state = vault.plugin_state.get("healthkeri", {})
        account = hk_state.get("account")
        if account is None:
            return None
        try:
            from kept.hk.configing import HealthKERIConfig
            from kept.hk.essring import APIClient
            config = HealthKERIConfig.get_instance()
            hab = vault.hby.habByName(account.alias)
            if hab:
                return APIClient(
                    url=config.protected_url,
                    root=config.api_aid,
                    hby=vault.hby,
                    hab=hab,
                )
        except Exception as exc:
            logger.debug(f"KERIGuardUserPlugin: could not build ESSR client: {exc}")
        return None

    async def _startup_and_poll(self, vault: "Vault", settings) -> None:
        """Unified credential-apply loop.

        Tracks which SAIDs have been successfully applied so that:
        - Credentials already in the registry at startup are picked up even
          when the embedded sentinel loads them concurrently with this task.
        - Transient failures ("error", "pending_oobi", "pending_sudo") are
          retried on the next poll interval.
        - Interface credentials are always applied before connection credentials
          so the .conf file exists before a peer is appended to it.
        """
        from keriguard.core.wireguarding import Schema

        _applied: set[str] = set()

        while True:
            try:
                rgy = vault.rgy

                # Collect everything in the registry, interfaces first.
                iface_saids = [s.qb64 for s in (rgy.reger.schms.get(keys=Schema.INTERFACE_SCHEMA) or [])]
                conn_saids = [s.qb64 for s in (rgy.reger.schms.get(keys=Schema.CONNECTION_SCHEMA) or [])]
                ordered = iface_saids + conn_saids

                # In healthKERI mode the ESSR client may not have been available at
                # startup (healthKERI account not yet configured).  Retry each iteration
                # so polling activates as soon as the account is ready.
                if (
                    settings.credential_source == "healthKERI"
                    and self._poller is not None
                    and self._poller.loader is None
                ):
                    essr = self._build_essr(vault)
                    if essr is not None:
                        self._poller.set_essr(essr)
                        if self._applier is not None:
                            self._applier.set_essr(essr)
                        logger.info(
                            "KERIGuardUserPlugin: healthKERI account now available, "
                            "SaaS credential polling activated"
                        )

                # Also ask the registrar for any freshly pushed credentials.
                try:
                    new_saids = await self._poller.poll_once(vault.hby)
                    for s in new_saids:
                        if s not in ordered:
                            ordered.append(s)
                except Exception as exc:
                    logger.warning(f"KERIGuardUserPlugin: poll error: {exc}")

                pending = [s for s in ordered if s not in _applied]
                if pending:
                    logger.debug(
                        f"KERIGuardUserPlugin: {len(pending)} credential(s) pending apply"
                    )

                refreshed = False
                for said in pending:
                    result = await self._applier.apply(said)
                    if result == "applied":
                        _applied.add(said)
                        refreshed = True
                    else:
                        logger.debug(
                            f"KERIGuardUserPlugin: apply {said[:16]}… → {result} (will retry)"
                        )

                if refreshed:
                    self._refresh_list_pages()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"KERIGuardUserPlugin: polling error: {exc}")

            await asyncio.sleep(settings.poll_interval)

    def _refresh_list_pages(self) -> None:
        for key in ("keriguard_user_machines", "keriguard_user_connections"):
            page = self._pages.get(key)
            if page and hasattr(page, "on_show"):
                page.on_show()

    def on_vault_closed(self, vault: "Vault") -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

        if self._watcher:
            try:
                self._watcher.stop()
            except Exception as exc:
                logger.debug(f"KERIGuardUserPlugin: watcher stop error: {exc}")
            self._watcher = None

        if self._sentinel_db:
            try:
                self._sentinel_db.close()
            except Exception as exc:
                logger.debug(f"KERIGuardUserPlugin: sentinel_db close error: {exc}")
            self._sentinel_db = None

        vault.plugin_state.pop("keriguard_user", None)

        if self._db:
            self._db.close()
            self._db = None
        if self._kgb:
            self._kgb.close()
            self._kgb = None

    # ------------------------------------------------------------------
    # Navigation handlers
    # ------------------------------------------------------------------

    def _on_initialization_done(self) -> None:
        """Called immediately when initialization succeeds; starts polling."""
        if self._app and self._app.vault:
            settings = self._db.keriguardUserSettings.get(keys=("settings",))
            if settings and settings.is_initialized:
                self._start_polling(self._app.vault, settings)

    def _on_setup_complete(self) -> None:
        """Called 1 second after initialization; navigates to the settings page."""
        for item in self._keriguard_submenu_items:
            if isinstance(item, MenuButton):
                item.set_active(False)
        settings_btn = self._nav_buttons_by_page.get("keriguard_user_settings")
        if settings_btn:
            settings_btn.set_active(True)
        self._navigate("keriguard_user_settings")
        page = self._pages.get("keriguard_user_settings")
        if page and hasattr(page, "on_show"):
            page.on_show()

    def _on_view_machine(self, said: str) -> None:
        detail = self._pages.get("keriguard_user_machine_detail")
        if detail:
            detail.load_machine(said)
            self._navigate("keriguard_user_machine_detail")

    def _on_back_to_machines(self) -> None:
        self._navigate("keriguard_user_machines")
        page = self._pages.get("keriguard_user_machines")
        if page and hasattr(page, "on_show"):
            page.on_show()

    def _on_import_interface(self) -> None:
        self._navigate("keriguard_user_import_interface")
        page = self._pages.get("keriguard_user_import_interface")
        if page and hasattr(page, "on_show"):
            page.on_show()

    def _on_import_complete_machines(self) -> None:
        self._on_back_to_machines()

    def _on_view_connection(self, said: str) -> None:
        detail = self._pages.get("keriguard_user_connection_detail")
        if detail:
            detail.load_connection(said)
            self._navigate("keriguard_user_connection_detail")

    def _on_back_to_connections(self) -> None:
        self._navigate("keriguard_user_connections")
        page = self._pages.get("keriguard_user_connections")
        if page and hasattr(page, "on_show"):
            page.on_show()

    def _on_import_connection(self) -> None:
        self._navigate("keriguard_user_import_connection")
        page = self._pages.get("keriguard_user_import_connection")
        if page and hasattr(page, "on_show"):
            page.on_show()

    def _on_import_complete_connections(self) -> None:
        self._on_back_to_connections()

    # ------------------------------------------------------------------
    # Menu / PluginBase interface
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        self._account_button = MenuButton(
            QIcon(":/assets/custom/logos/keriguard-darkmode.png"),
            "KERIGuard",
        )
        self._account_button.is_account_btn = True
        self._keriguard_submenu_items = self._create_submenu_items()

    def _create_submenu_items(self) -> list[QWidget]:
        items: list[QWidget] = []
        items.append(BackButton(dark_mode=False))
        items.append(MenuSpacer(15))

        nav_buttons_config = [
            (":/assets/material-icons/devices.svg", "Machines", "keriguard_user_machines"),
            (":/assets/material-icons/airline_stops.svg", "Connections", "keriguard_user_connections"),
            (":/assets/material-icons/settings-hover.svg", "Settings", "keriguard_user_settings"),
        ]

        self._nav_buttons_by_page: dict[str, MenuButton] = {}
        for icon_path, label, page_key in nav_buttons_config:
            btn = MenuButton(QIcon(icon_path), label)
            btn.clicked.connect(self._make_nav_handler(page_key, btn))
            items.append(btn)
            self._nav_buttons_by_page[page_key] = btn

        return items

    def _is_initialized(self) -> bool:
        if not self._app or not self._app.vault:
            return False
        settings = self._app.vault.plugin_state.get("keriguard_user", {}).get("settings")
        return settings is not None and settings.is_initialized

    def _make_nav_handler(self, page_key: str, button: MenuButton):
        def handler():
            if not self._is_initialized():
                return
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
        settings = self._db.keriguardUserSettings.get(keys=("settings",)) if self._db else None
        return settings is not None and settings.is_initialized

    def get_setup_page(self, vault: "Vault") -> tuple[str, bool]:
        return ("keriguard_user_setup", True)