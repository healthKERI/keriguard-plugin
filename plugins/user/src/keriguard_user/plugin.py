# -*- encoding: utf-8 -*-
"""keriguard_user.plugin — KERIGuardUserPlugin for the Locksmith application."""
from __future__ import annotations

import asyncio
import sys
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

        self._poller = CredentialPoller(
            hby=vault.hby,
            hab=watcher_hab,
            rgy=vault.rgy,
            settings=settings,
            essr=essr,
        )

        # KEL watching (issuer + guardian keystate, for viewing/de-escrow) is
        # independent of credential apply, which is now handled exclusively
        # by the guardian daemon (dev or prod) -- see plugin.py module docs.
        self._start_watcher(vault, watcher_hab, settings)

        self._poll_task = asyncio.create_task(
            self._startup_and_poll(vault, settings),
            name="keriguard_user_poll",
        )

    def _start_watcher(self, vault: "Vault", watcher_hab, settings) -> None:
        try:
            from sentinel.core.witnessing import Watcher
            from sentinel.db.basing import SentinelBaser
        except ImportError as exc:
            logger.warning(f"KERIGuardUserPlugin: sentinel not available, skipping KEL watcher: {exc}")
            return

        try:
            from .core import keystore

            sentinel_db_name = f"{vault.hby.name}-watcher"
            self._sentinel_db = SentinelBaser(name=sentinel_db_name, reopen=True)

            export_dir = settings.export_dir or str(keystore.DEFAULT_EXPORT_DIR)

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

            # Register the guardian AID for watching too — its KEL is
            # resolved into vault.hby at Setup time via settings.server_oobi
            # (a witness-mediated OOBI for server_aid, see setup/page.py),
            # which is the precondition Watcher.watch()/Sentinel.watch()
            # need (sentinel/core/witnessing.py: "Unable to watch unknown
            # aid" if the AID isn't already in hby.kevers).
            if getattr(settings, "server_aid", ""):
                self._watcher.watch(settings.server_aid)

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
            from .core.essring import APIClient
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
        """Credential polling loop.

        Pulls credentials into vault.rgy (for the Machines/Connections UI and
        to de-escrow against up-to-date issuer keystate) and refreshes those
        list pages whenever something new shows up. Applying credentials to
        the WireGuard config is no longer done here -- that's exclusively the
        guardian daemon's job (dev or prod; see `_launch_daemons`), which
        discovers and applies credentials issued to `server_aid` via its own,
        independent KEL/TEL watching.
        """
        from keriguard.core.wireguarding import Schema

        _seen: set[str] = set()

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

                newly_seen = [s for s in ordered if s not in _seen]
                if newly_seen:
                    _seen.update(newly_seen)
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
        # Guardian/sentinel daemons are intentionally NOT stopped here --
        # the whole point of daemonizing them is that they keep applying
        # credentials in the background independent of this vault being
        # open. They're stopped only via an explicit user action (Settings
        # page "Stop Daemons", see `stop_daemons` below) or, for dev-mode
        # subprocesses, the `atexit` safety net in `core/daemon_launch.py`
        # when the app process itself exits.
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
        from .core.helper_launch import launch_helper_app

        launch_helper_app()

        if self._app and self._app.vault:
            settings = self._db.keriguardUserSettings.get(keys=("settings",))
            if settings and settings.is_initialized:
                self._start_polling(self._app.vault, settings)
                asyncio.create_task(
                    self._launch_daemons(settings), name="keriguard_user_daemon_launch"
                )

    async def _launch_daemons(self, settings) -> None:
        """Bootstrap the guardian/sentinel daemons and register the issuer
        AID with the now-running sentinel daemon (DAEMONS.md Phase 3).
        Called once, from `_on_initialization_done` right after Setup
        provisions this vault's identity -- not re-triggered on a later
        vault reopen (daemons are no longer stopped on vault close, so
        there's nothing to restart there; `start_daemons()` is the manual
        equivalent, wired to the Settings page's "Start Daemons" button).

        Prod (frozen macOS): the daemons are machine-singleton launchd
        agents (fixed labels, one shared plist/config.yaml per user account
        -- `keystore.GUARDIAN_AGENT_LABEL`/`SENTINEL_AGENT_LABEL`), even
        though every vault provisions its own distinct guardian/sentinel
        identity. `launchctl bootstrap` on an already-loaded label is a
        no-op that silently keeps the *first* vault's identity running -- so
        re-writing the plist/config.yaml for a second vault would just leave
        stale, misleading files on disk, and `register_issuer_watch` would
        burn its full retry budget against a socket path keyed to a
        sentinel_aid that was never actually started. Guard both by checking
        launchd install state first: only the first vault to reach this
        point actually launches anything. A non-owning vault (or one where
        neither daemon is installed yet) only gets credentials *pulled in
        for viewing* via its own in-process KEL watcher/poller -- applying
        them to the WireGuard config is exclusively the daemon's job, so a
        non-owning vault does not apply anything locally.

        Dev (unfrozen macOS, opt-in `KERIGUARD_DEV_DAEMONS=1`): real `kg`/
        `sentinel` subprocesses, not launchd -- see
        `daemon_launch.should_use_dev_daemons()` and
        `guardian_launch.launch_guardian_daemon_dev`/
        `sentinel_launch.launch_sentinel_daemon_dev`. No-op entirely if the
        env var isn't set, or off unfrozen/non-macOS or a frozen build.
        """
        from .core.daemon_launch import should_use_dev_daemons
        from .core.daemon_watch import register_issuer_watch

        loop = asyncio.get_event_loop()

        if should_use_dev_daemons():
            from .core.sentinel_launch import launch_sentinel_daemon_dev
            from .core.guardian_launch import launch_guardian_daemon_dev

            sentinel_proc = await loop.run_in_executor(None, launch_sentinel_daemon_dev, settings)
            if sentinel_proc is not None:
                await loop.run_in_executor(None, register_issuer_watch, settings)

            guardian_proc = await loop.run_in_executor(None, launch_guardian_daemon_dev, settings)
            guardian_ok = guardian_proc is not None
        else:
            from .core.sentinel_launch import launch_sentinel_daemon
            from .core.guardian_launch import launch_guardian_daemon
            from .core.guardian_check import is_guardian_installed
            from .core.sentinel_check import is_sentinel_installed

            sentinel_already_running = await loop.run_in_executor(None, is_sentinel_installed)
            if sentinel_already_running:
                logger.info(
                    "KERIGuardUserPlugin: sentinel daemon already running under another "
                    "vault's identity; this vault relies on its in-process KEL watcher instead"
                )
            else:
                sentinel_ok = await loop.run_in_executor(None, launch_sentinel_daemon, settings)
                if sentinel_ok:
                    await loop.run_in_executor(None, register_issuer_watch, settings)

            guardian_already_running = await loop.run_in_executor(None, is_guardian_installed)
            if guardian_already_running:
                logger.info(
                    "KERIGuardUserPlugin: guardian daemon already running under another "
                    "vault's identity; this vault will not apply credentials locally"
                )
                guardian_ok = False
            else:
                guardian_ok = await loop.run_in_executor(None, launch_guardian_daemon, settings)

        owns_daemon = bool(guardian_ok)
        if owns_daemon != getattr(settings, "owns_daemon", False):
            settings.owns_daemon = owns_daemon
            self._db.keriguardUserSettings.pin(keys=("settings",), val=settings)
            if self._app and self._app.vault:
                self._app.vault.plugin_state["keriguard_user"]["settings"] = settings

    def start_daemons(self) -> None:
        """Manually (re)start the guardian/sentinel daemons -- wired to the
        Settings page's "Start Daemons" button.

        Daemons already launch automatically once, on Setup completion (see
        `_launch_daemons`'s docstring); this exists so a user who explicitly
        stopped them (or whose dev-mode daemons died) can bring them back up
        without redoing Setup, now that closing/reopening the vault no
        longer starts or stops them on its own."""
        if not (self._app and self._app.vault) or not self._db:
            return
        settings = self._db.keriguardUserSettings.get(keys=("settings",))
        if not settings or not settings.is_initialized:
            return
        asyncio.create_task(
            self._launch_daemons(settings), name="keriguard_user_daemon_start"
        )

    def stop_daemons(self) -> None:
        """Manually stop the guardian/sentinel daemons -- wired to the
        Settings page's "Stop Daemons" button. Safe no-op if neither is
        currently running.

        Note: prod (frozen macOS) daemons are machine-wide singletons
        (DAEMONS.md Phase 3e) -- this stops whichever vault's identity
        currently owns the daemon slot, not necessarily this vault's own.
        Dev-mode daemons are namespaced per vault instead (see
        `keystore.dev_guardian_agent_label`), so this only stops *this*
        vault's own dev daemons -- hence the settings lookup below."""
        from .core.daemon_launch import should_use_dev_daemons

        if should_use_dev_daemons():
            if not self._db:
                return
            settings = self._db.keriguardUserSettings.get(keys=("settings",))
            if not settings:
                return
            from .core.guardian_launch import stop_guardian_daemon_dev
            from .core.sentinel_launch import stop_sentinel_daemon_dev

            stop_guardian_daemon_dev(settings)
            stop_sentinel_daemon_dev(settings)
        else:
            from .core.guardian_launch import stop_guardian_daemon
            from .core.sentinel_launch import stop_sentinel_daemon

            stop_guardian_daemon()
            stop_sentinel_daemon()

    def daemons_status(self) -> dict:
        """Current guardian/sentinel run state, for the Settings page to
        render. `supported` is False when this build/run can't launch
        daemons at all (non-macOS, or unfrozen without
        `KERIGUARD_DEV_DAEMONS=1`) -- Start/Stop should be disabled then."""
        from .core.daemon_launch import daemons_supported, is_frozen_macos, should_use_dev_daemons

        if should_use_dev_daemons():
            settings = self._db.keriguardUserSettings.get(keys=("settings",)) if self._db else None
            if settings:
                from .core.guardian_launch import is_guardian_dev_running
                from .core.sentinel_launch import is_sentinel_dev_running

                guardian_running = is_guardian_dev_running(settings)
                sentinel_running = is_sentinel_dev_running(settings)
            else:
                guardian_running = False
                sentinel_running = False
        elif is_frozen_macos():
            from .core.guardian_check import is_guardian_installed
            from .core.sentinel_check import is_sentinel_installed

            guardian_running = is_guardian_installed()
            sentinel_running = is_sentinel_installed()
        else:
            guardian_running = False
            sentinel_running = False

        return {
            "supported": daemons_supported(),
            "guardian_running": guardian_running,
            "sentinel_running": sentinel_running,
        }

    def _on_keriguard_menu_opened(self) -> None:
        """Runs each time the KERIGuard menu entry is opened: verifies the
        helper is installed and reachable, alerting/attempting install/smoke
        testing as appropriate."""
        from .core.helper_check import is_helper_installed

        if not is_helper_installed():
            from .core.helper_launch import launch_helper_app

            if not getattr(sys, "frozen", False):
                logger.error(
                    "KERIGuardUserPlugin: KERIGuardHelper is not installed/registered on this "
                    "machine. This is a dev (non-frozen) run, so it can't be auto-installed -- "
                    "build and install KERIGuardHelper.app yourself (see keriguard-helper's "
                    "Local dev workflow) if you need to exercise the WireGuard tunnel path."
                )
            else:
                logger.warning("KERIGuardUserPlugin: KERIGuardHelper not installed; attempting to launch it")
                launch_helper_app()
            return

        asyncio.create_task(self._run_helper_smoke_test(), name="keriguard_user_helper_smoke_test")
        self._check_daemon_health()

    def _check_daemon_health(self) -> None:
        """Logs guardian/sentinel launchd + heartbeat status; no user-facing
        alert yet (helper checks above already gate the initial nudge)."""
        from .core.daemon_launch import is_frozen_macos

        if not is_frozen_macos():
            return

        from .core.guardian_check import is_guardian_installed, is_guardian_alive
        from .core.sentinel_check import is_sentinel_installed

        if not is_guardian_installed():
            logger.warning("KERIGuardUserPlugin: guardian daemon not registered with launchd")
        elif not is_guardian_alive():
            logger.warning("KERIGuardUserPlugin: guardian daemon heartbeat is stale")

        if not is_sentinel_installed():
            logger.warning("KERIGuardUserPlugin: sentinel daemon not registered with launchd")

    async def _run_helper_smoke_test(self) -> None:
        from .core.helper_check import smoke_test_ipc

        ok, response, error = await smoke_test_ipc()
        if ok:
            logger.info(f"KERIGuardUserPlugin: helper IPC smoke test passed ({response})")
        else:
            logger.error(f"KERIGuardUserPlugin: helper IPC smoke test failed: {error or response}")

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
            QIcon(":/assets/custom/logos/vpn-user-lightmode.png"),
            "KERIGuard",
        )
        self._account_button.is_account_btn = True
        self._account_button.clicked.connect(self._on_keriguard_menu_opened)
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