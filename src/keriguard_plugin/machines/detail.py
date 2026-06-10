# -*- encoding: utf-8 -*-                                                                                                                                                                                                                                                                                      
"""keriguard.machines.detail — Machine detail page."""
from typing import Any, TYPE_CHECKING

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy,
)
from PySide6.QtGui import QIcon, QPalette, QColor
from keri import help

from locksmith.ui import colors
from locksmith.ui.toolkit.tables import PaginatedTableWidget
from locksmith.ui.toolkit.widgets import LocksmithButton
from locksmith.ui.toolkit.widgets.buttons import LocksmithCopyButton, BackButton
from locksmith.ui.vault.healthKERI.profile.widgets import EditableInfoRow
from keriguard.core.wireguarding import Schema

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)

class MachineDetailPage(QWidget):
    """Detail view for a single KERIGuard machine (interface credential)."""

    back_clicked = Signal()

    def __init__(self, app: "LocksmithApplication", parent: "VaultPage | None" = None):
        super().__init__(parent)
        self._parent = parent
        self.app = app
        self.vault_name = ""
        self._current_said = ""

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors.BACKGROUND_CONTENT))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"background-color: {colors.BACKGROUND_CONTENT}; border: none;"
        )
        scroll.viewport().setStyleSheet(
            f"background-color: {colors.BACKGROUND_CONTENT};"
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(50, 40, 50, 40)
        content_layout.setSpacing(15)

        back_row = QHBoxLayout()
        back_btn = BackButton()
        back_btn.setFixedWidth(80)
        back_btn.clicked.connect(self.back_clicked.emit)
        back_row.addStretch()
        back_row.addWidget(back_btn)

        content_layout.addLayout(back_row)

        content_layout.addWidget(self._create_header_section())
        content_layout.addWidget(self._create_interface_section())
        content_layout.addWidget(self._create_connections_section(), 1)

        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def _create_header_section(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(30)

        icon_label = QLabel()
        icon_label.setFixedSize(80, 80)
        icon = QIcon(":/assets/material-icons/settings-hover.svg")
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(80, 80))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"""                                                                                                                                                                                                                                                                          
              background-color: {colors.BACKGROUND_HOVER};                                                                                                                                                                                                                                                       
              border: 1px solid {colors.BORDER_FOCUS};                                                                                                                                                                                                                                                           
              border-radius: 40px;                                                                                                                                                                                                                                                                               
          """)
        layout.addWidget(icon_label)

        info_col = QWidget()
        info_layout = QVBoxLayout(info_col)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(5)

        self.name_label = QLabel("—")
        self.name_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #333;")
        info_layout.addWidget(self.name_label)

        self.aid_label = QLabel("—")
        self.aid_label.setStyleSheet(
            "font-size: 16px; font-family: monospace; color: #666;"
        )
        info_layout.addWidget(self.aid_label)

        layout.addWidget(info_col, 1)
        return header

    def _create_interface_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(0)

        # SAID — hand-built row: monospace value + copy button
        said_row = QWidget()
        said_layout = QHBoxLayout(said_row)
        said_layout.setContentsMargins(0, 15, 0, 15)
        said_layout.setSpacing(16)

        said_label = QLabel("SAID")
        said_label.setFixedWidth(120)
        said_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #555;")
        said_layout.addWidget(said_label)

        self.said_value = QLabel("—")
        self.said_value.setStyleSheet(
            "font-size: 14px; font-family: monospace; color: #333;"
        )
        said_layout.addWidget(self.said_value, 1)

        self._said_copy_btn = LocksmithCopyButton(copy_content="")
        said_layout.addWidget(self._said_copy_btn)

        layout.addWidget(said_row)
        layout.addWidget(self._divider())

        # Standard read-only attribute rows
        self.address_row = EditableInfoRow("Address", "—", "address", editable=False)
        layout.addWidget(self.address_row)
        layout.addWidget(self._divider())

        self.port_row = EditableInfoRow("Port", "—", "port", editable=False)
        layout.addWidget(self.port_row)
        layout.addWidget(self._divider())

        self.environment_row = EditableInfoRow("Environment", "—", "environment", editable=False)
        layout.addWidget(self.environment_row)
        layout.addWidget(self._divider())

        self.description_row = EditableInfoRow("Description", "—", "description", editable=False)
        layout.addWidget(self.description_row)
        layout.addWidget(self._divider())

        self.status_row = EditableInfoRow("Status", "Issued", "status", editable=False)
        layout.addWidget(self.status_row)
        layout.addWidget(self._divider())

        return section

    def _create_connections_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 40, 0, 0)
        layout.setSpacing(0)

        self.connections_table = PaginatedTableWidget(
            columns=[
                "Connected Machine", "Endpoint", "Allowed IPs",
                "Connection Name", "Environment", "Status",
            ],
            column_widths={
                "Connected Machine": 180,
                "Endpoint": 160,
                "Allowed IPs": 160,
                "Environment": 110,
                "Status": 80,
            },
            title="Peer Connections",
            icon_path=":/assets/material-icons/settings-hover.svg",
            show_add_button=False,
            row_actions=["View"],
            row_action_icons={"View": ":/assets/material-icons/visibility.svg"},
            items_per_page=10,
            show_search=False,
            parent=self,
        )
        self.connections_table.row_action_triggered.connect(self._on_connection_action)
        layout.addWidget(self.connections_table, 1)
        return section

    def _divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(
            f"background-color: {colors.BACKGROUND_NEUTRAL}; border: none;"
        )
        div.setFixedHeight(1)
        return div

    def _on_connection_action(self, row_data: dict, action: str) -> None:
        # Connection detail page is deferred.
        logger.debug(f"Connection action '{action}' on {row_data.get('_conn_said', '')} — not yet implemented")

    def load_machine(self, said: str) -> None:
        self._current_said = said
        self._load_interface_data()
        self._load_connections()

    def _load_interface_data(self) -> None:
        if not self._current_said or not self.app or not self.app.vault:
            return
        try:
            creder, *_ = self.app.vault.rgy.reger.cloneCred(said=self._current_said)
        except Exception as exc:
            logger.warning(f"MachineDetailPage: could not load credential {self._current_said}: {exc}")
            return

        payload = creder.attrib
        iface = payload.get("interface", {})
        meta = payload.get("interfaceMetadata", {})

        self.name_label.setText(meta.get("interfaceName", "") or "—")
        self.aid_label.setText(payload.get("i", "") or "—")

        self.said_value.setText(creder.said)
        self._said_copy_btn._copy_content = creder.said  # update in-place; _copy_content is the backing attr

        self.address_row.set_value(", ".join(iface.get("address", [])) or "—")
        self.port_row.set_value(str(iface.get("listenPort", "")) or "—")
        self.environment_row.set_value(meta.get("environment", "") or "—")
        self.description_row.set_value(meta.get("description", "") or "—")
        self.status_row.set_value("Issued")


    def _load_connections(self) -> None:
        if not self._current_said or not self.app or not self.app.vault:
            self.connections_table.set_static_data([])
            return

        rgy = self.app.vault.rgy

        # Determine the registry this machine belongs to so we can scope connections.
        try:
            iface_creder, *_ = rgy.reger.cloneCred(said=self._current_said)
            registry_regk = iface_creder.regi
        except Exception as exc:
            logger.warning(f"MachineDetailPage: could not resolve registry for {self._current_said}: {exc}")
            self.connections_table.set_static_data([])
            return

        rows: list[dict[str, Any]] = []
        try:
            for saider in (rgy.reger.schms.get(keys=Schema.CONNECTION_SCHEMA) or []):
                try:
                    conn_creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                    if conn_creder.regi != registry_regk:
                        continue

                    edge_block = conn_creder.sad.get("e", {})
                    peer1 = edge_block.get("peer1", {})
                    peer2 = edge_block.get("peer2", {})

                    if peer1.get("n") == self._current_said:
                        other_block = peer2
                    elif peer2.get("n") == self._current_said:
                        other_block = peer1
                    else:
                        continue

                    conn_meta = other_block.get("connectionMetadata", {})
                    rows.append({
                        "Connected Machine": self._get_peer_name(other_block.get("n", "")),
                        "Endpoint": other_block.get("endpoint", ""),
                        "Allowed IPs": ", ".join(other_block.get("allowedIps", [])),
                        "Connection Name": conn_meta.get("connectionName", ""),
                        "Environment": conn_meta.get("environment", ""),
                        "Status": "Issued",
                        "_conn_said": conn_creder.said,
                    })
                except Exception as exc:
                    logger.warning(f"Skipping connection credential {saider.qb64}: {exc}")
        except Exception as exc:
            logger.exception(f"Error loading connections for {self._current_said}: {exc}")

        self.connections_table.set_static_data(rows)

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

    def set_vault_name(self, vault_name: str) -> None:
        self.vault_name = vault_name

    def on_show(self) -> None:
        if self._current_said:
            self._load_interface_data()
            self._load_connections()