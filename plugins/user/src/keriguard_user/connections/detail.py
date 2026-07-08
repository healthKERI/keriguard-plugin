# -*- encoding: utf-8 -*-
"""keriguard_user.connections.detail — Connection detail page (received connection credential)."""
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy,
)
from PySide6.QtGui import QIcon, QPalette, QColor
from keri import help

from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.buttons import LocksmithCopyButton, BackButton
from locksmith.ui.vault.healthKERI.profile.widgets import EditableInfoRow
from ..db.basing import KERIGuardConnectionNote, KERIGuardMachineNote

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class ConnectionDetailPage(QWidget):
    """Detail view for a received KERIGuard connection credential."""

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
        scroll.setStyleSheet(f"background-color: {colors.BACKGROUND_CONTENT}; border: none;")
        scroll.viewport().setStyleSheet(f"background-color: {colors.BACKGROUND_CONTENT};")

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
        content_layout.addWidget(self._create_credential_section())

        peer1_panel, self._peer1_widgets = self._create_peer_panel("Peer 1")
        peer2_panel, self._peer2_widgets = self._create_peer_panel("Peer 2")
        content_layout.addWidget(peer1_panel)
        content_layout.addWidget(peer2_panel)
        content_layout.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def _create_header_section(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(30)

        icon_label = QLabel()
        icon_label.setFixedSize(80, 80)
        icon = QIcon(":/assets/material-icons/airline_stops.svg")
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(80, 80))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        info_col = QWidget()
        info_layout = QVBoxLayout(info_col)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(5)

        self.name_label = QLabel("—")
        self.name_label.setStyleSheet("font-size: 28px; font-weight: bold; color: #333;")
        info_layout.addWidget(self.name_label)

        self.said_label = QLabel("—")
        self.said_label.setStyleSheet("font-size: 16px; font-family: monospace; color: #666;")
        info_layout.addWidget(self.said_label)

        layout.addWidget(info_col, 1)
        return header

    def _create_credential_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(15, 16, 0, 0)
        layout.setSpacing(0)

        said_row = QWidget()
        said_layout = QHBoxLayout(said_row)
        said_layout.setContentsMargins(0, 15, 0, 15)
        said_layout.setSpacing(16)
        said_lbl = QLabel("SAID")
        said_lbl.setFixedWidth(135)
        said_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #555;")
        said_layout.addWidget(said_lbl)
        self.said_value = QLabel("—")
        self.said_value.setStyleSheet("font-size: 14px; font-family: monospace; color: #333;")
        said_layout.addWidget(self.said_value, 1)
        self._said_copy_btn = LocksmithCopyButton(copy_content="")
        said_layout.addWidget(self._said_copy_btn)
        layout.addWidget(said_row)
        layout.addWidget(self._divider())

        self.conn_name_row = self._create_info_row("Connection Name")
        layout.addWidget(self.conn_name_row[0])
        layout.addWidget(self._divider())

        self.purpose_row = self._create_info_row("Purpose")
        layout.addWidget(self.purpose_row[0])
        layout.addWidget(self._divider())

        self.environment_row = self._create_info_row("Environment")
        layout.addWidget(self.environment_row[0])
        layout.addWidget(self._divider())

        self.bandwidth_row = self._create_info_row("Bandwidth Class")
        layout.addWidget(self.bandwidth_row[0])
        layout.addWidget(self._divider())

        self.issued_row = self._create_info_row("Issued Date")
        layout.addWidget(self.issued_row[0])
        layout.addWidget(self._divider())

        self.description_row = EditableInfoRow("Description", "—", "description", editable=True)
        self.description_row.value_label.setWordWrap(True)
        self.description_row.label_widget.setFixedWidth(135)
        self.description_row.value_changed.connect(self._on_connection_description_changed)
        layout.addWidget(self.description_row)
        layout.addWidget(self._divider())

        return section

    def _create_peer_panel(self, panel_title: str) -> tuple[QWidget, dict]:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 40, 0, 0)
        layout.setSpacing(0)

        header = QLabel(panel_title)
        header.setStyleSheet("font-size: 18px; font-weight: 700; color: #333;")
        layout.addWidget(header)
        layout.addSpacing(10)
        layout.addWidget(self._thick_divider())

        machine_row = self._create_info_row("Machine")
        layout.addWidget(machine_row[0])
        layout.addWidget(self._divider())

        endpoint_widget, endpoint_value, endpoint_copy = self._create_copyable_row("Endpoint")
        endpoint_div = self._divider()
        endpoint_container = self._optional_container(endpoint_widget, endpoint_div)
        layout.addWidget(endpoint_container)

        allowed_ips_widget, allowed_ips_value, allowed_ips_copy = self._create_copyable_row("Allowed IPs")
        layout.addWidget(allowed_ips_widget)
        layout.addWidget(self._divider())

        peer_name_row = self._create_info_row("Peer Name")
        peer_name_div = self._divider()
        peer_name_container = self._optional_container(peer_name_row[0], peer_name_div)
        layout.addWidget(peer_name_container)

        keepalive_row = self._create_info_row("Keepalive")
        keepalive_div = self._divider()
        keepalive_container = self._optional_container(keepalive_row[0], keepalive_div)
        layout.addWidget(keepalive_container)

        description_row = EditableInfoRow("Description", "—", "description", editable=True)
        description_row.value_label.setWordWrap(True)
        description_row.label_widget.setFixedWidth(135)
        layout.addWidget(description_row)
        layout.addWidget(self._thick_divider())

        widgets = {
            "machine_row": machine_row,
            "endpoint_container": endpoint_container,
            "endpoint_widget": endpoint_widget,
            "endpoint_value": endpoint_value,
            "endpoint_copy": endpoint_copy,
            "allowed_ips_widget": allowed_ips_widget,
            "allowed_ips_value": allowed_ips_value,
            "allowed_ips_copy": allowed_ips_copy,
            "peer_name_container": peer_name_container,
            "peer_name_row": peer_name_row,
            "keepalive_container": keepalive_container,
            "keepalive_row": keepalive_row,
            "description_row": description_row,
        }
        return panel, widgets

    def _optional_container(self, row_widget: QWidget, divider: QFrame) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(row_widget)
        layout.addWidget(divider)
        return container

    def _create_copyable_row(self, label_text: str) -> tuple:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 15, 0, 15)
        row_layout.setSpacing(16)
        label = QLabel(label_text)
        label.setFixedWidth(135)
        label.setStyleSheet("font-size: 14px; font-weight: 600; color: #555;")
        row_layout.addWidget(label)
        value_label = QLabel("—")
        value_label.setStyleSheet("font-size: 14px; color: #333;")
        row_layout.addWidget(value_label, 1)
        copy_btn = LocksmithCopyButton(copy_content="")
        row_layout.addWidget(copy_btn)
        return row, value_label, copy_btn

    def _create_info_row(self, label_text: str, default_value: str = "—") -> tuple:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 15, 0, 15)
        row_layout.setSpacing(16)
        label = QLabel(label_text)
        label.setFixedWidth(135)
        label.setStyleSheet("font-size: 14px; font-weight: 600; color: #555;")
        row_layout.addWidget(label)
        value_label = QLabel(default_value)
        value_label.setStyleSheet("font-size: 14px; color: #333;")
        row_layout.addWidget(value_label, 1)
        return row, value_label

    def _divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"background-color: {colors.BACKGROUND_NEUTRAL}; border: none;")
        div.setFixedHeight(1)
        return div

    def _thick_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"background-color: {colors.BACKGROUND_NEUTRAL}; border: none;")
        div.setFixedHeight(3)
        return div

    def load_connection(self, said: str) -> None:
        self._current_said = said
        self._load_connection_data()

    def on_show(self) -> None:
        if self._current_said:
            self._load_connection_data()

    def _load_connection_data(self) -> None:
        if not self._current_said or not self.app or not self.app.vault:
            return
        try:
            creder, *_ = self.app.vault.rgy.reger.cloneCred(said=self._current_said)
        except Exception as exc:
            logger.warning(f"ConnectionDetailPage: could not load {self._current_said}: {exc}")
            return

        edge_block = creder.sad.get("e", {})
        peer1 = edge_block.get("peer1", {})
        peer2 = edge_block.get("peer2", {})
        conn_meta = peer1.get("connectionMetadata", {})

        conn_name = conn_meta.get("connectionName", "") or "—"
        self.name_label.setText(conn_name)
        self.said_label.setText(creder.said)
        self.said_value.setText(creder.said)
        self._said_copy_btn._copy_content = creder.said

        self.conn_name_row[1].setText(conn_name)
        self.purpose_row[1].setText(conn_meta.get("purpose", "") or "—")
        self.environment_row[1].setText(conn_meta.get("environment", "") or "—")
        self.bandwidth_row[1].setText(conn_meta.get("bandwidthClass", "") or "—")
        self.issued_row[1].setText(creder.attrib.get("dt", "") or "—")

        db = self.app.vault.plugin_state.get("keriguard_user", {}).get("db")
        conn_note = db.keriguardConnectionNotes.get(keys=(creder.said,)) if db else None
        self.description_row.set_value(conn_note.description if conn_note else "—")

        self._populate_peer_panel(self._peer1_widgets, peer1)
        self._populate_peer_panel(self._peer2_widgets, peer2)

    def _populate_peer_panel(self, widgets: dict, peer_block: dict) -> None:
        interface_said = peer_block.get("n", "")
        widgets["machine_row"][1].setText(self._get_peer_name(interface_said))

        endpoint = peer_block.get("endpoint", "")
        widgets["endpoint_container"].setVisible(bool(endpoint))
        if endpoint:
            widgets["endpoint_value"].setText(endpoint)
            widgets["endpoint_copy"]._copy_content = endpoint

        allowed_ips_list = peer_block.get("allowedIps", [])
        allowed_ips_str = ", ".join(allowed_ips_list) if allowed_ips_list else "—"
        widgets["allowed_ips_value"].setText(allowed_ips_str)
        widgets["allowed_ips_copy"]._copy_content = ", ".join(allowed_ips_list)

        peer_name = peer_block.get("peerName", "")
        widgets["peer_name_container"].setVisible(bool(peer_name))
        if peer_name:
            widgets["peer_name_row"][1].setText(peer_name)

        keepalive = peer_block.get("persistentKeepalive")
        widgets["keepalive_container"].setVisible(keepalive is not None)
        if keepalive is not None:
            widgets["keepalive_row"][1].setText(f"{keepalive}s")

        db = self.app.vault.plugin_state.get("keriguard_user", {}).get("db") if self.app and self.app.vault else None
        machine_note = db.keriguardMachineNotes.get(keys=(interface_said,)) if db else None
        desc_row = widgets["description_row"]
        desc_row.set_value(machine_note.description if machine_note else "—")
        try:
            desc_row.value_changed.disconnect()
        except RuntimeError:
            pass
        desc_row.value_changed.connect(
            lambda _field, val, said=interface_said: self._on_machine_description_changed(said, val)
        )

    def _on_connection_description_changed(self, field_name: str, new_value: str) -> None:
        if not self._current_said or not self.app or not self.app.vault:
            return
        db = self.app.vault.plugin_state.get("keriguard_user", {}).get("db")
        if db is None:
            return
        db.keriguardConnectionNotes.pin(
            keys=(self._current_said,),
            val=KERIGuardConnectionNote(description=new_value),
        )

    def _on_machine_description_changed(self, interface_said: str, new_value: str) -> None:
        if not self.app or not self.app.vault:
            return
        db = self.app.vault.plugin_state.get("keriguard_user", {}).get("db")
        if db is None:
            return
        db.keriguardMachineNotes.pin(
            keys=(interface_said,),
            val=KERIGuardMachineNote(description=new_value),
        )

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