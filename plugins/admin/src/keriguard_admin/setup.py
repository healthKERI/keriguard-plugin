# -*- encoding: utf-8 -*-
"""

keriguard_admin — KERIGuard plugin settings page.
"""
from keri import help

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QButtonGroup, QFrame, QPushButton, QVBoxLayout, QFileDialog,
)
from keri.app.habbing import GroupHab
from keriguard_admin.db.basing import KERIGuardSettings
from locksmith.core.apping import LocksmithApplication
from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.buttons import LocksmithButton, LocksmithIconButton, LocksmithInvertedButton
from locksmith.ui.toolkit.widgets.fields import FloatingLabelComboBox, FloatingLabelLineEdit
from locksmith.ui.toolkit.widgets.page import LocksmithFormPage
from locksmith.ui.toolkit.widgets import CollapsibleSection
from locksmith.ui.navigation import Pages


from locksmith.ui.vault.page import VaultPage

BORDER = "#d7d9dc"
TEXT_PRIMARY = "#1a1a1a"
TEXT_SECONDARY = "#54575a"
PANEL_BG = "#fafbfb"

logger = help.ogler.getLogger(__name__)


# --------------------------------------------------------------------------
# KERIGuard Admin Setup Page
# --------------------------------------------------------------------------

class KERIGuardAdminSetupPage(LocksmithFormPage):

    setup_complete_clicked = Signal()

    SUBTITLES = {
        "opensource": "Open source — connect a public or self-hosted repository.",
        "serviceprovider": "Service provider — connect to a vendor-managed integration.",
    }

    def __init__(self, app: "LocksmithApplication", parent: "VaultPage | None" = None):
        super().__init__(
            title="KERIGuard VPN Setup",
            icon_path=":/assets/material-icons/settings-hover.svg",
            parent=parent,
        )
        self.app = app
        self._parent = parent
        self._build_content()
        logger.info("KERIGuard setup initialized")


    def _build_content(self):
        self._build_mode_section()
        self._build_registrar_url_section()
        self._build_service_provider_section()
        self._build_issuer_section()
        self._build_export_dir_section()
        self.content_layout.addSpacing(50)
        self._build_notification()
        self._build_button_row()
        self.content_layout.addStretch()

    def _build_mode_section(self):
        header = QLabel("Publish Mode")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel(
            "Select the publish mode for your KERIGuard mesh network "
            "whether you want to use the open source registrar or a service provider"
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(20)

        self.toggle = SegmentedToggle([
            ("serviceprovider", "Service Provider", ":/assets/material-icons/trip.svg", ":/assets/material-icons/dark-trip.svg"),
            ("opensource", "Open Source", ":/assets/material-icons/open-source.svg", ":/assets/material-icons/dark-open-source.svg"),
        ])
        self.toggle.setFixedWidth(525)
        self.toggle.valueChanged.connect(self._on_toggle_changed)
        self.content_layout.addWidget(self.toggle)

    def _build_service_provider_section(self):

        self._service_provider_section = CollapsibleSection(
            button=self.toggle,
            on_expand_changed=None
        )
        self._service_provider_section.toggle()

        layout = QVBoxLayout()
        layout.addSpacing(24)
        layout.setContentsMargins(25, 0, 0, 0)

        header = QLabel("Service Provider")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        layout.addWidget(header)
        layout.addSpacing(8)

        hint = QLabel(
            "Select the Service Provider to use as your watcher network and credential registrar."
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(10)

        self._service_provider_dropdown = FloatingLabelComboBox("Registry")
        self._service_provider_dropdown.addItem("healthKERI")

        self._service_provider_dropdown.setFixedWidth(420)
        layout.addWidget(self._service_provider_dropdown)

        self._service_provider_section.set_content_layout(layout)
        self.content_layout.addWidget(self._service_provider_section)

    def _build_registrar_url_section(self):

        self._registrar_url_section = CollapsibleSection(
            button=self.toggle,
            on_expand_changed=None
        )

        layout = QVBoxLayout()
        layout.addSpacing(24)
        layout.setContentsMargins(25, 0, 0, 0)

        header = QLabel("Registrar URL")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        layout.addWidget(header)
        layout.addSpacing(8)

        hint = QLabel(
            "Enter the URL of the KERIGuard registrar service. After issuing a credential, "
            "the plugin pushes CESR bytes here so sentinel nodes can retrieve it."
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(10)

        self._registrar_url_field = FloatingLabelLineEdit("Registrar URL")
        self._registrar_url_field.setFixedWidth(420)
        layout.addWidget(self._registrar_url_field)
        self._registrar_url_section.set_content_layout(layout)
        self.content_layout.addWidget(self._registrar_url_section)

    def _build_issuer_section(self):

        layout = QVBoxLayout()
        layout.addSpacing(24)
        layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.addLayout(layout)

        header = QLabel("Issuer")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        layout.addWidget(header)
        layout.addSpacing(8)

        hint = QLabel(
            "Select the Identifier to use as your KERIGuard credential issuer."
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(10)

        self._issuer_dropdown = FloatingLabelComboBox("Issuer")
        self._issuer_dropdown.setFixedWidth(420)
        layout.addWidget(self._issuer_dropdown)

    def _build_export_dir_section(self):
        self.content_layout.addSpacing(30)

        header = QLabel("Export Directory")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel(
            "When set, exported .cesr grant files are written here automatically. "
            "Leave blank to choose a location each time via the Export action."
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)

        self._export_dir_field = FloatingLabelLineEdit("Export Directory")
        self._export_dir_field.setFixedWidth(375)
        row.addWidget(self._export_dir_field)

        browse_btn = LocksmithIconButton(":/assets/material-icons/browse.svg", tooltip="Browse files")
        browse_btn.setFixedHeight(48)
        browse_btn.setFixedWidth(48)
        browse_btn.clicked.connect(self._browse_export_dir)
        row.addWidget(browse_btn)

        row.addStretch()
        self.content_layout.addLayout(row)

    def _build_notification(self):
        hint = QLabel(
            "Click Complete Setup to save your settings, load KERIGuard schema and "
            "create a credential registry for issuing KERIGuard credentials."
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 15px;")
        hint.setWordWrap(True)

        self.content_layout.addSpacing(10)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(40)

    def _build_button_row(self):
        # --- Button row ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._cancel_btn = LocksmithInvertedButton("Cancel")
        self._cancel_btn.setFixedWidth(140)
        self._cancel_btn.clicked.connect(self._cancel)

        btn_row.addWidget(self._cancel_btn)
        btn_row.addSpacing(12)

        self._issue_btn = LocksmithButton("Complete Setup")
        self._issue_btn.setFixedWidth(180)
        self._issue_btn.clicked.connect(self._save_settings)

        btn_row.addWidget(self._issue_btn)
        btn_row.addStretch()

        self.content_layout.addLayout(btn_row)

    def _browse_export_dir(self) -> None:
        current = self._export_dir_field.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Export Directory",
            current or "",
            )
        if chosen:
            self._export_dir_field.setText(chosen)
            logger.info(f"KERIGuard export directory selected: {chosen!r}")

    def _get_settings(self) -> KERIGuardSettings | None:
        if not self.app or not self.app.vault:
            return None
        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        return kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None

    def _cancel(self) -> None:
        self._parent.navigate_to(Pages.VAULT)

    def _save_settings(self) -> None:
        if not self.app or not self.app.vault:
            return
        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        if kg_db is None:
            return

        hby = self.app.vault.hby
        rgy = self.app.vault.rgy

        settings = KERIGuardSettings()
        logger.info(f"setting issuer alias as {self._issuer_dropdown.currentText()}")
        issuer_alias = self._issuer_map[self._issuer_dropdown.currentText()]
        hab = hby.habByName(issuer_alias)
        settings.issuer_aid = hab.pre

        registrar_url = self._registrar_url_field.text().strip()
        if registrar_url is not None:
            settings.registrar_url = registrar_url

        export_dir = self._export_dir_field.text().strip()
        if export_dir is not None:
            settings.export_dir = export_dir

        publish_mode = self.toggle.value()
        if publish_mode is not None:
            settings.publish_mode = publish_mode

        kg_db.keriguardSettings.pin(keys=("settings",), val=settings)
        self.setup_complete_clicked.emit()

    def on_show(self) -> None:
        logger.info("KERIGuard setup shown")
        self._load_dropdowns()

    def _on_toggle_changed(self, value: str):
        self._service_provider_section.toggle()
        self._registrar_url_section.toggle()

    def selected_mode(self) -> str:
        return self.toggle.value()

    def _load_dropdowns(self):
        logger.info("LOADING DROPDOWNS")
        if not self.app or not self.app.vault:
            return

        hby = self.app.vault.hby
        self._issuer_map = {}

        self._issuer_dropdown.clear()

        # hby.habs is keyed by AID prefix; hab.name is the human alias
        for aid, hab in hby.habs.items():
            if isinstance(hab, GroupHab) or not hab.kever.wits:
                continue
            display = f"{hab.name} — {aid}"
            logger.info(f"ADDING {display}")
            self._issuer_map[display] = hab.name
            self._issuer_dropdown.addItem(display)

        self._issuer_dropdown.setCurrentIndex(-1)

        if len(self._issuer_map) == 1:
            self._issuer_dropdown.setCurrentIndex(0)


# --------------------------------------------------------------------------
# Segmented toggle control
# --------------------------------------------------------------------------

class SegmentedToggle(QWidget):
    """Two-option segmented control with an animated sliding highlight."""

    valueChanged = Signal(str)

    def __init__(self, options: list[tuple[str, str, str, str]], parent=None):
        """options: exactly two (value, label) tuples."""
        super().__init__(parent)

        assert len(options) == 2, "SegmentedToggle only supports two options"
        self._options = options
        self._current_index = 0
        self.setFixedHeight(40)

        self._track = QFrame(self)
        self._track.setObjectName("track")
        self._track.setStyleSheet(f"""
            #track {{
                background: #eceef0;
                border: 1px solid {BORDER};
                border-radius: 20px;
            }}
        """)

        self._highlight = QFrame(self._track)
        self._highlight.setStyleSheet(f"""
            background: {colors.PRIMARY};
            border-radius: 17px;
        """)

        self._buttons: list[tuple[QPushButton, str, str]] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for i, (_value, label, icon_path, dark_icon_path) in enumerate(options):
            btn = QPushButton(label, self._track)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFlat(True)
            icon = QIcon(icon_path)
            btn.setIcon(icon)
            btn.setIconSize(QSize(20, 20))
            self._group.addButton(btn, i)
            self._buttons.append((btn, icon_path, dark_icon_path))

        self._buttons[0][0].setChecked(True)
        self._highlight.lower()  # keep highlight behind button text
        self._group.idClicked.connect(self._on_clicked)

        self._anim = QPropertyAnimation(self._highlight, b"geometry")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._update_text_colors()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._track.setGeometry(0, 0, self.width(), self.height())
        seg_w = self.width() // 2
        for i, (btn, _, _) in enumerate(self._buttons):
            btn.setGeometry(i * seg_w, 0, seg_w, self.height())
        self._highlight.setGeometry(
            self._current_index * seg_w + 3, 3, seg_w - 6, self.height() - 6
        )

    def _on_clicked(self, index: int):
        self._animate_to(index)
        value, _label, _icon_path, _dark = self._options[index]
        self.valueChanged.emit(value)

    def _animate_to(self, index: int):
        seg_w = self.width() // 2
        end_rect = QRect(index * seg_w + 3, 3, seg_w - 6, self.height() - 6)
        self._anim.stop()
        self._anim.setStartValue(self._highlight.geometry())
        self._anim.setEndValue(end_rect)
        self._anim.start()
        self._current_index = index
        self._update_text_colors()

    def _update_text_colors(self):
        for i, (btn, icon_path, dark_icon_path) in enumerate(self._buttons):
            color = "#ffffff" if i == self._current_index else TEXT_SECONDARY
            btn.setStyleSheet(f"""
                QPushButton {{
                    border: none;
                    font-size: 13px;
                    font-weight: 600;
                    color: {color};
                }}
            """)
            icon = QIcon(icon_path if i == self._current_index else dark_icon_path)
            btn.setIcon(icon)
            btn.setIconSize(QSize(20, 20))


    def value(self) -> str:
        return self._options[self._current_index][0]


# --------------------------------------------------------------------------
# Content panels (the part that "morphs")
# --------------------------------------------------------------------------

def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_SECONDARY};")
    return label


