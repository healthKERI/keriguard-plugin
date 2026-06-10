# -*- encoding: utf-8 -*-
"""keriguard.connections.list — Connections list page."""
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
    """Paginated list of KERIGuard connections."""

    view_connection = Signal(str)  # emits connection credential SAID

    def __init__(self, app, parent: "VaultPage | None" = None):
        super().__init__(parent)
        self._parent = parent
        self.app = app
        self.vault_name = ""
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
            show_add_button=False,
            row_actions=["View"],
            row_action_icons={"View": ":/assets/material-icons/visibility.svg"},
            items_per_page=10,
            show_search=True,
            parent=self,
        )

        layout.addWidget(self.table)
        self.table.row_action_triggered.connect(self._on_row_action)
        self.table.row_clicked.connect(self._on_row_clicked)

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

    def _transform_connection_to_row(self, conn: dict[str, Any]) -> dict[str, Any]:
        return {
            "Name": conn.get("connection_name", ""),
            "SAID": conn.get("said", ""),
            "Peer 1": conn.get("peer1_name", ""),
            "Peer 2": conn.get("peer2_name", ""),
            "Status": "Issued",
            "_said": conn.get("said", ""),
        }

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

                    edge_block = creder.sad.get("e", {})
                    peer1 = edge_block.get("peer1", {})
                    peer2 = edge_block.get("peer2", {})
                    conn_meta = peer1.get("connectionMetadata", {})

                    rows.append(self._transform_connection_to_row({
                        "said": creder.said,
                        "peer1_name": self._get_peer_name(peer1.get("n", "")),
                        "peer2_name": self._get_peer_name(peer2.get("n", "")),
                        "connection_name": conn_meta.get("connectionName", ""),
                    }))
                except Exception as exc:
                    logger.warning(f"Skipping connection credential {saider.qb64}: {exc}")
        except Exception as exc:
            logger.exception(f"Error iterating connection credentials: {exc}")

        return rows

    def _on_row_clicked(self, row_data: object) -> None:
        if isinstance(row_data, dict):
            data: Dict[str, Any] = {str(k): v for k, v in row_data.items()}
            self._on_row_action(data, "View")

    def _on_row_action(self, row_data: Dict[str, Any], action: str) -> None:
        if action == "View":
            said = row_data.get("_said", "")
            if said:
                self.view_connection.emit(said)

    def set_vault_name(self, vault_name: str) -> None:
        self.vault_name = vault_name

    def on_show(self) -> None:
        self.table.set_static_data(self._load_rows())
