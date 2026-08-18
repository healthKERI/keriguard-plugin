# -*- encoding: utf-8 -*-
"""keriguard.connections.list — Connections list page."""
from pathlib import Path
from typing import Dict, Any, TYPE_CHECKING

import qasync
from PySide6.QtCore import Signal
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy, QFileDialog, QMessageBox
from keri import help
from keri.core.serdering import SerderACDC
from keri.kering import Ilks
from keriguard.core.kering import Issuer
from keriguard.core.wireguarding import Schema
from locksmith.ui import colors
from locksmith.ui.toolkit.tables import PaginatedTableWidget

if TYPE_CHECKING:
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class ConnectionsListPage(QWidget):
    """Paginated list of KERIGuard connections."""

    view_connection = Signal(str)  # emits connection credential SAID

    def __init__(self, app, parent: "VaultPage | None" = None):
        super().__init__(parent)
        self._parent = parent
        self.app = app
        self.vault_name = ""
        self._connections_cache: dict[str, SerderACDC] = {}
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

        # Define all possible row action icons
        self._row_action_icons = {
            "View": ":/assets/material-icons/visibility.svg",
            "Disconnect": ":/assets/material-icons/link-off.svg",
            "Export": ":/assets/material-icons/export.svg",
            "Delete": ":/assets/material-icons/delete.svg",
        }

        self.table = PaginatedTableWidget(
            columns=["Peer 1", "Peer 1 IP", "Peer 2", "Peer 2 IP", "Status"],
            column_widths={
                "Peer 1": 140,
                "Peer 1 IP": 170,
                "Peer 2": 140,
                "Peer 2 IP": 170,
                "Actions": 90,
            },
            title="Connections",
            icon_path=":/assets/material-icons/airline_stops.svg",
            show_add_button=True,
            column_sort_mapping={
                "Peer 1": "peer1_name",
                "Peer 2": "peer2_name",
                "Status": "status"
            },
            add_button_text="Connect Devices",
            row_actions=["View", "Disconnect", "Export", "Delete"],
            row_action_icons=self._row_action_icons,
            row_actions_callback=self._get_row_actions,
            items_per_page=10,
            show_search=True,
            parent=self,
        )

        layout.addWidget(self.table)
        self.table.row_action_triggered.connect(self._on_row_action)
        self.table.row_clicked.connect(self._on_row_clicked)
        self.table.add_clicked.connect(self._on_issue_connection)
        self.table.load_requested.connect(self._on_load_requested)

    def _get_row_actions(self, row_data: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
        """
        Determine which row actions to show based on connection status.

        - Shows "Disconnect" only when status is "Active"
        - Shows "Delete" only when status is "Disconnected" (revoked)
        - Always shows "View" and "Export"

        Returns:
            Tuple of (actions list, action icons dict)
        """
        status = row_data.get("Status", "")
        actions = ["View"]

        if status == "Active":
            actions.append("Disconnect")
        elif status == "Disconnected":
            actions.append("Delete")

        actions.append("Export")

        # Filter icons to only include those for displayed actions
        icons = {action: self._row_action_icons[action] for action in actions}

        return actions, icons

    @qasync.asyncSlot(dict)
    async def _on_load_requested(self, params: dict):
        self.table.set_static_data(self._load_rows())

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

    def _load_rows(self) -> list[dict[str, Any]]:
        if not self.app or not self.app.vault:
            return []

        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
        if not settings or not settings.registry_name:
            return []

        registry = self.app.vault.rgy.registryByName(settings.registry_name)
        if registry is None:
            logger.info(f"KERIGuard: registry {settings.registry_name} not found")
            return []

        rgy = self.app.vault.rgy
        rows: list[dict[str, Any]] = []
        try:
            for saider in (rgy.reger.schms.get(keys=Schema.CONNECTION_SCHEMA) or []):
                try:
                    creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                    if creder.regi != registry.regk:
                        continue

                    rows.append(self._transform_connection_to_row(creder))

                except Exception as exc:
                    logger.warning(f"Skipping connection credential {saider.qb64}: {exc}")
        except Exception as exc:
            logger.exception(f"Error iterating connection credentials: {exc}")

        return rows

    def _transform_connection_to_row(self, creder: SerderACDC) -> dict[str, Any]:
        edge_block = creder.sad.get("e", {})
        peer1 = edge_block.get("peer1", {})
        peer2 = edge_block.get("peer2", {})

        interface_said = peer1.get("n", "")
        peer1_creder, *_ = self.app.vault.rgy.reger.cloneCred(said=interface_said)
        contact = self.app.vault.org.get(peer1_creder.attrib.get("i", ""))
        peer1_name = contact.get("alias", "")
        interface = peer1_creder.attrib.get("interface", "")
        peer1_ip = interface.get("address")[0].split("/")[0]

        interface_said = peer2.get("n", "")
        peer2_creder, *_ = self.app.vault.rgy.reger.cloneCred(said=interface_said)
        contact = self.app.vault.org.get(peer2_creder.attrib.get("i", ""))
        peer2_name = contact.get("alias", "")
        interface = peer2_creder.attrib.get("interface", "")
        peer2_ip = interface.get("address")[0].split("/")[0]

        conn_meta = peer1.get("connectionMetadata", {})
        connection_name = conn_meta.get("connectionName", "")
        said = creder.said

        regk = creder.regi
        status = self.app.vault.rgy.tevers[regk].vcState(creder.said)
        if status.et in [Ilks.iss, Ilks.bis]:
            status = "Active"
            status_color = colors.SUCCESS_INDICATOR
        elif status.et in [Ilks.rev, Ilks.brv]:
            status = "Disconnected"
            status_color = colors.DANGER
        else:
            status = "Unknown"
            status_color = colors.WARNING_YELLOW

        self._connections_cache[said] = creder
        return {
            "Name": connection_name,
            "SAID": said,
            "Peer 1": peer1_name,
            "Peer 1 IP": peer1_ip,
            "Peer 2": peer2_name,
            "Peer 2 IP": peer2_ip,
            "Status": status,
            "Status_color": status_color,
            "_said": said,
        }

    def _on_row_clicked(self, row_data: object) -> None:
        if isinstance(row_data, dict):
            data: Dict[str, Any] = {str(k): v for k, v in row_data.items()}
            self._on_row_action(data, "View")

    def _on_row_action(self, row_data: Dict[str, Any], action: str) -> None:
        if action == "View":
            said = row_data.get("_said", "")
            if said:
                self.view_connection.emit(said)
        elif action == "Disconnect":
            self._on_disconnect_connection(row_data)
        elif action == "Delete":
            self._on_delete_connection(row_data)
        elif action == "Export":
            self._export_credential(row_data)

    def _on_disconnect_connection(self, row_data: Dict[str, Any]) -> None:
        """Handle Disconnect connection action."""
        from .disconnect import DisconnectConnectionDialog

        connection_said = row_data.get("_said", "")
        connection_name = row_data.get("Name", "")

        if not connection_said:
            logger.error("Cannot disconnect: no connection SAID found")
            return

        if not connection_name:
            # Fallback to SAID prefix if name is missing
            connection_name = connection_said[:12]

        logger.info(f"Opening disconnect dialog for connection: {connection_name}")

        dialog = DisconnectConnectionDialog(
            app=self.app,
            connection_name=connection_name,
            connection_said=connection_said,
            on_success=self._on_connection_disconnected,
            parent=self._parent
        )
        dialog.open()

    def _on_connection_disconnected(self, connection_said: str):
        """Handle successful connection disconnection."""
        logger.info(f"Connection {connection_said} disconnected, reloading list")
        self.on_show()  # Refresh the connections list

    def _on_delete_connection(self, row_data: Dict[str, Any]) -> None:
        """Handle Delete connection action."""
        from .delete import DeleteConnectionDialog

        connection_said = row_data.get("_said", "")
        connection_name = row_data.get("Name", "")

        if not connection_said:
            logger.error("Cannot delete: no connection SAID found")
            return

        if not connection_name:
            # Fallback to SAID prefix if name is missing
            connection_name = connection_said[:12]

        logger.info(f"Opening delete dialog for connection: {connection_name}")

        dialog = DeleteConnectionDialog(
            app=self.app,
            connection_name=connection_name,
            connection_said=connection_said,
            on_success=self._on_connection_deleted,
            parent=self._parent
        )
        dialog.open()

    def _on_connection_deleted(self, connection_said: str):
        """Handle successful connection deletion."""
        logger.info(f"Connection {connection_said} deleted, reloading list")
        self.on_show()  # Refresh the connections list

    def _on_issue_connection(self) -> None:
        """Show the Issue Connection Credential dialog."""
        from .connect import IssueConnectionCredentialDialog

        dialog = IssueConnectionCredentialDialog(self.app, self._parent)
        dialog.connection_issued.connect(self._on_connection_issued)
        dialog.exec()

    def _on_connection_issued(self, said: str) -> None:
        """Handle successful connection credential issuance."""
        logger.info(f"Connection credential issued with SAID: {said}")
        # Refresh the table to show the new connection
        self.on_show()

    def _export_credential(self, row_data: Dict[str, Any]) -> None:
        said = row_data.get("_said", "")
        if not said or not self.app or not self.app.vault:
            return

        conn_name = row_data.get("Name") or said[:12]
        default_filename = f"{conn_name}.cesr"

        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
        start_dir = (settings.export_dir if settings and settings.export_dir else "") or str(Path.home())

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Connection Credential",
            str(Path(start_dir) / default_filename),
            "CESR Files (*.cesr);;All Files (*)",
        )
        if not path:
            return

        try:
            hby = self.app.vault.hby
            rgy = self.app.vault.rgy
            creder, *_ = rgy.reger.cloneCred(said=said)
            issuer_pre = creder.sad.get("i", "")
            hab = hby.habByPre(issuer_pre)
            if hab is None:
                QMessageBox.warning(
                    self,
                    "Export Failed",
                    "Cannot export: issuing identifier not found in this vault.",
                )
                return
            issuer = Issuer(hby=hby, hab=hab, rgy=rgy)
            recipient_aid = creder.attrib.get("i", "")
            grant = issuer.grant(said, recipient_aid)
            Path(path).write_bytes(bytes(grant))
            logger.info(f"Connection credential {said} exported to {path}")
        except Exception as exc:
            logger.exception(f"Export failed for {said}: {exc}")
            QMessageBox.warning(self, "Export Failed", f"Could not export credential:\n{exc}")

    def set_vault_name(self, vault_name: str) -> None:
        self.vault_name = vault_name

    def on_show(self) -> None:
        self._connections_cache.clear()
        self.table.request_load()
