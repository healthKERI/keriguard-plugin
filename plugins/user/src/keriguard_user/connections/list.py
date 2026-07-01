# -*- encoding: utf-8 -*-
"""keriguard_user.connections.list — Connections list page (received connection credentials)."""
from typing import Dict, Any, TYPE_CHECKING

from PySide6.QtCore import Signal
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

    def __init__(self, app, parent: "VaultPage | None" = None):
        super().__init__(parent)
        self._parent = parent
        self.app = app
        self.vault_name = ""
        self._connections_cache: dict[str, dict[str, Any]] = {}
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
            row_actions=["View"],
            row_action_icons={"View": ":/assets/material-icons/visibility.svg"},
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

    def _wg_status_for_conn(self, conn_said: str) -> str:
        """Check WireGuard status for the local peer of this connection."""
        try:
            if not self.app or not self.app.vault:
                return "Unknown"
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
                    iface_name = iface_creder.attrib.get("interfaceMetadata", {}).get("interfaceName", "")
                    from keriguard.core.systeming import _is_wireguard_up
                    return "Active" if _is_wireguard_up(iface_name) else "Inactive"
        except Exception:
            pass
        return "Unknown"

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
                        "wg_status": self._wg_status_for_conn(creder.said),
                    }))
                except Exception as exc:
                    logger.warning(f"Skipping connection {saider.qb64}: {exc}")
        except Exception as exc:
            logger.exception(f"Error iterating connection credentials: {exc}")

        return rows

    def _on_row_clicked(self, row_data: object) -> None:
        if isinstance(row_data, dict):
            self._on_row_action({str(k): v for k, v in row_data.items()}, "View")

    def _on_row_action(self, row_data: Dict[str, Any], action: str) -> None:
        if action == "View":
            said = row_data.get("_said", "")
            if said:
                self.view_connection.emit(said)

    def set_vault_name(self, vault_name: str) -> None:
        self.vault_name = vault_name

    def on_show(self) -> None:
        self._connections_cache.clear()
        self.table.set_static_data(self._load_rows())