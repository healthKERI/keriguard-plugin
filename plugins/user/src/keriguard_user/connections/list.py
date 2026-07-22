# -*- encoding: utf-8 -*-
"""keriguard_user.connections.list — Connections list page (received connection credentials)."""
import asyncio
from pathlib import Path
from typing import Dict, Any, TYPE_CHECKING

from PySide6.QtCore import Signal, QMetaObject, Qt, Q_ARG
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PySide6.QtGui import QPalette, QColor
from keri import help

from locksmith.ui import colors
from locksmith.ui.toolkit.tables import PaginatedTableWidget
from keriguard.core.wireguarding import Schema

if TYPE_CHECKING:
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class ConnectionsListPage(QWidget):
    """Paginated list of received KERIGuard connection credentials."""

    view_connection = Signal(str)
    import_clicked = Signal()
    _status_ready = Signal()  # internal signal to update table from main thread

    _ROW_ACTION_ICONS = {
        "View": ":/assets/material-icons/visibility.svg",
        "Start": ":/assets/material-icons/enable.svg",
        "Stop": ":/assets/material-icons/close.svg",
    }

    def __init__(self, app, parent: "VaultPage | None" = None):
        super().__init__(parent)
        self._parent = parent
        self.app = app
        self.vault_name = ""
        self._connections_cache: dict[str, dict[str, Any]] = {}
        self._pending_status_checks: list[tuple[str, str]] = []
        self._status_ready.connect(self._apply_status_updates)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors.BACKGROUND_CONTENT))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.table = PaginatedTableWidget(
            columns=["Name", "SAID", "Peer 1", "Peer 2", "Status"],
            column_widths={
                "Name": 180,
                "Status": 90,
                "Peer 1": 130,
                "Peer 2": 130,
                "Actions": 90,
            },
            title="Connections",
            icon_path=":/assets/material-icons/airline_stops.svg",
            show_add_button=True,
            add_button_text="Import Credential",
            row_actions=["View", "Start", "Stop"],
            row_action_icons=self._ROW_ACTION_ICONS,
            row_actions_callback=self._get_row_actions,
            items_per_page=10,
            show_search=True,
            parent=self,
        )
        layout.addWidget(self.table)
        self.table.row_action_triggered.connect(self._on_row_action)
        self.table.row_clicked.connect(self._on_row_clicked)
        self.table.add_clicked.connect(self.import_clicked.emit)

    def _get_peer_name(self, interface_said: str) -> str:
        if not interface_said or not self.app or not self.app.vault:
            return interface_said
        try:
            creder, *_ = self.app.vault.rgy.reger.cloneCred(said=interface_said)
            return (
                    creder.attrib.get("interfaceMetadata", {}).get("interfaceName", "")
                    or interface_said
            )
        except Exception:
            return interface_said

    def _resolve_local_iface_name(self, conn_said: str) -> str | None:
        """Return the WireGuard interface name for the local peer, or None."""
        try:
            if not self.app or not self.app.vault:
                return None
            rgy = self.app.vault.rgy
            conn_creder, *_ = rgy.reger.cloneCred(said=conn_said)
            edge_block = conn_creder.sad.get("e", {})
            user_aids = set(self.app.vault.hby.habs.keys())

            for peer_key in ("peer1", "peer2"):
                peer = edge_block.get(peer_key, {})
                iface_said = peer.get("n", "")
                if not iface_said:
                    continue
                iface_creder, *_ = rgy.reger.cloneCred(said=iface_said)
                if iface_creder.attrib.get("i") in user_aids:
                    return iface_creder.attrib.get("interfaceMetadata", {}).get("interfaceName", "")
        except Exception:
            pass
        return None

    def _transform_connection_to_row(self, conn: dict[str, Any]) -> dict[str, Any]:
        said = conn.get("said", "")
        self._connections_cache[said] = conn
        return {
            "Name": conn.get("connection_name", ""),
            "SAID": said,
            "Peer 1": conn.get("peer1_name", ""),
            "Peer 2": conn.get("peer2_name", ""),
            "Status": conn.get("wg_status", "Unknown"),
            "_said": said,
        }

    def _load_rows(self) -> list[dict[str, Any]]:
        if not self.app or not self.app.vault:
            return []

        vault = self.app.vault
        rgy = vault.rgy
        user_aids = set(vault.hby.habs.keys())

        # Collect interface SAIDs that belong to this vault
        local_iface_saids: set[str] = set()
        for saider in (rgy.reger.schms.get(keys=Schema.INTERFACE_SCHEMA) or []):
            try:
                creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                if creder.attrib.get("i") in user_aids:
                    local_iface_saids.add(creder.said)
            except Exception:
                pass

        rows: list[dict[str, Any]] = []
        try:
            for saider in (rgy.reger.schms.get(keys=Schema.CONNECTION_SCHEMA) or []):
                try:
                    creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                    edge_block = creder.sad.get("e", {})
                    peer1 = edge_block.get("peer1", {})
                    peer2 = edge_block.get("peer2", {})

                    if peer1.get("n") not in local_iface_saids and peer2.get("n") not in local_iface_saids:
                        continue

                    conn_meta = peer1.get("connectionMetadata", {})
                    rows.append(self._transform_connection_to_row({
                        "said": creder.said,
                        "peer1_name": self._get_peer_name(peer1.get("n", "")),
                        "peer2_name": self._get_peer_name(peer2.get("n", "")),
                        "connection_name": conn_meta.get("connectionName", ""),
                        "wg_status": "Unknown",
                    }))
                except Exception as exc:
                    logger.warning(f"Skipping connection {saider.qb64}: {exc}")
        except Exception as exc:
            logger.exception(f"Error iterating connection credentials: {exc}")

        return rows

    async def _check_statuses(self, conn_saids: list[str]) -> None:
        """Resolve WireGuard status for each connection asynchronously."""
        from keriguard.core.systeming import is_wireguard_up

        results: list[tuple[str, str]] = []
        for conn_said in conn_saids:
            iface_name = self._resolve_local_iface_name(conn_said)
            if not iface_name:
                continue
            try:
                is_up = await is_wireguard_up(iface_name)
                results.append((conn_said, "Active" if is_up else "Inactive"))
            except Exception:
                results.append((conn_said, "Unknown"))

        self._pending_status_checks = results
        self._status_ready.emit()

    def _get_config_dir(self) -> str | None:
        if not self.app or not self.app.vault:
            return None
        settings = self.app.vault.plugin_state.get("keriguard_user", {}).get("settings")
        return getattr(settings, "config_dir", None) or None

    def _get_row_actions(self, row_data: Dict[str, Any]) -> tuple[list[str], dict[str, str]]:
        """Show View plus a Start/Stop toggle reflecting the connection's tunnel state."""
        status = row_data.get("Status", "Unknown")
        toggle = "Stop" if status == "Active" else "Start"
        return ["View", toggle], self._ROW_ACTION_ICONS

    def _toggle_connection(self, said: str) -> None:
        conn = self._connections_cache.get(said, {})
        iface_name = self._resolve_local_iface_name(said)
        config_dir = self._get_config_dir()
        if not iface_name or not config_dir:
            logger.warning(f"Cannot toggle connection {said}: interface or config_dir unresolved")
            return

        config_path = str(Path(config_dir) / f"{iface_name}.conf")
        turning_on = conn.get("wg_status") != "Active"

        try:
            loop = asyncio.get_running_loop()
            asyncio.ensure_future(
                self._run_toggle(said, iface_name, config_path, turning_on), loop=loop
            )
        except RuntimeError:
            logger.debug("No running event loop; skipping connection toggle")

    async def _run_toggle(self, said: str, iface_name: str, config_path: str, turning_on: bool) -> None:
        from keriguard.core.systeming import (
            WireGuardControlError,
            start_wireguard,
            stop_wireguard,
        )

        try:
            if turning_on:
                await start_wireguard(iface_name, config_path)
            else:
                await stop_wireguard(iface_name, config_path)
        except WireGuardControlError as exc:
            logger.warning(f"Failed to toggle connection {said} ({iface_name}): {exc}")

        await self._check_statuses([said])

    def _apply_status_updates(self) -> None:
        """Called on the main thread when async status checks complete."""
        if not self._pending_status_checks:
            return

        status_map = dict(self._pending_status_checks)
        self._pending_status_checks = []

        # Update the cached data
        for said, status in status_map.items():
            if said in self._connections_cache:
                self._connections_cache[said]["wg_status"] = status

        # Rebuild rows from cache with updated statuses
        rows = [
            self._transform_connection_to_row(conn)
            for conn in self._connections_cache.values()
        ]
        self.table.set_static_data(rows)

    def _on_row_clicked(self, row_data: object) -> None:
        if isinstance(row_data, dict):
            self._on_row_action({str(k): v for k, v in row_data.items()}, "View")

    def _on_row_action(self, row_data: Dict[str, Any], action: str) -> None:
        said = row_data.get("_said", "")
        if not said:
            return
        if action == "View":
            self.view_connection.emit(said)
        elif action in ("Start", "Stop"):
            self._toggle_connection(said)

    def set_vault_name(self, vault_name: str) -> None:
        self.vault_name = vault_name

    def on_show(self) -> None:
        self._connections_cache.clear()
        rows = self._load_rows()
        self.table.set_static_data(rows)

        # Schedule async status resolution on the running event loop
        conn_saids = [r["_said"] for r in rows if r.get("_said")]
        if conn_saids:
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(self._check_statuses(conn_saids), loop=loop)
            except RuntimeError:
                logger.debug("No running event loop; skipping async status checks")