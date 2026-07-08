# -*- encoding: utf-8 -*-
"""keriguard_user.settings — KERIGuard user plugin settings page."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QHBoxLayout, QFileDialog, QWidget, QVBoxLayout

from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.page import LocksmithFormPage
from locksmith.ui.toolkit.widgets.fields import FloatingLabelComboBox, FloatingLabelLineEdit
from locksmith.ui.toolkit.widgets.buttons import LocksmithIconButton

from .db.basing import KERIGuardUserSettings

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = logging.getLogger(__name__)


class KERIGuardUserSettingsPage(LocksmithFormPage):

    def __init__(self, app: "LocksmithApplication", parent: "VaultPage | None" = None):
        super().__init__(
            title="KERIGuard Settings",
            icon_path=":/assets/material-icons/settings-hover.svg",
            parent=parent,
        )
        self.app = app
        self._build_content()

    def _build_content(self):
        self._build_source_section()
        self._build_registrar_url_section()
        self._build_issuer_section()
        self._build_config_dir_section()
        self._build_export_dir_section()
        self._build_poll_interval_section()
        self._build_kel_watch_interval_section()
        self.content_layout.addStretch()

    def _build_source_section(self):
        header = QLabel("Credential Source")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel(
            'Where this vault fetches issued credentials from. '
            'Use "registrar" for self-hosted deployments and "healthKERI" for SaaS mode.'
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(10)

        self._source_dropdown = FloatingLabelComboBox("Credential Source")
        self._source_dropdown.setFixedWidth(420)
        for mode in ["registrar", "healthKERI"]:
            self._source_dropdown.addItem(mode)
        self._source_dropdown.currentTextChanged.connect(self._on_source_changed)
        self.content_layout.addWidget(self._source_dropdown)

    def _build_registrar_url_section(self):
        self.content_layout.addSpacing(24)

        self._registrar_url_section = self._make_subsection()
        reg_layout = self._registrar_url_section.layout()

        header = QLabel("Registrar URL")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        reg_layout.addWidget(header)
        reg_layout.addSpacing(8)

        hint = QLabel("Base URL of the KERIGuard registrar service.")
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        reg_layout.addWidget(hint)
        reg_layout.addSpacing(10)

        self._registrar_url_field = FloatingLabelLineEdit("Registrar URL")
        self._registrar_url_field.setFixedWidth(420)
        self._registrar_url_field.line_edit.editingFinished.connect(self._on_registrar_url_changed)
        reg_layout.addWidget(self._registrar_url_field)

        self.content_layout.addWidget(self._registrar_url_section)

    def _build_issuer_section(self):
        self.content_layout.addSpacing(24)

        header = QLabel("Issuer")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel("AID and OOBI of the credential issuer (read-only; set during setup).")
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(10)

        self._issuer_aid_field = FloatingLabelLineEdit("Issuer AID (read-only)")
        self._issuer_aid_field.setFixedWidth(420)
        self._issuer_aid_field.line_edit.setReadOnly(True)
        self.content_layout.addWidget(self._issuer_aid_field)
        self.content_layout.addSpacing(8)

        self._issuer_oobi_field = FloatingLabelLineEdit("Issuer OOBI")
        self._issuer_oobi_field.setFixedWidth(420)
        self._issuer_oobi_field.line_edit.editingFinished.connect(self._on_issuer_oobi_changed)
        self.content_layout.addWidget(self._issuer_oobi_field)

    def _build_config_dir_section(self):
        self.content_layout.addSpacing(24)

        header = QLabel("WireGuard Config Directory")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel("Directory where WireGuard .conf files are written.")
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._config_dir_field = FloatingLabelLineEdit("WireGuard Config Directory")
        self._config_dir_field.setFixedWidth(375)
        self._config_dir_field.line_edit.editingFinished.connect(self._on_config_dir_changed)
        row.addWidget(self._config_dir_field)
        browse_btn = LocksmithIconButton(":/assets/material-icons/browse.svg", tooltip="Browse")
        browse_btn.setFixedSize(48, 48)
        browse_btn.clicked.connect(self._browse_config_dir)
        row.addWidget(browse_btn)
        row.addStretch()
        self.content_layout.addLayout(row)

    def _build_export_dir_section(self):
        self.content_layout.addSpacing(24)

        header = QLabel("Export Directory")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel(
            "Default directory for manually exported .cesr credential files. "
            "Leave blank to prompt each time."
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._export_dir_field = FloatingLabelLineEdit("Export Directory")
        self._export_dir_field.setFixedWidth(375)
        self._export_dir_field.line_edit.editingFinished.connect(self._on_export_dir_changed)
        row.addWidget(self._export_dir_field)
        browse_btn = LocksmithIconButton(":/assets/material-icons/browse.svg", tooltip="Browse")
        browse_btn.setFixedSize(48, 48)
        browse_btn.clicked.connect(self._browse_export_dir)
        row.addWidget(browse_btn)
        row.addStretch()
        self.content_layout.addLayout(row)

    def _build_poll_interval_section(self):
        self.content_layout.addSpacing(24)

        header = QLabel("Credential Poll Interval")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel(
            "Seconds between credential polling cycles — independent of the KEL watcher. (default: 30)"
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(10)

        self._poll_interval_field = FloatingLabelLineEdit("Credential Poll Interval")
        self._poll_interval_field.setFixedWidth(200)
        self._poll_interval_field.line_edit.editingFinished.connect(self._on_poll_interval_changed)
        self.content_layout.addWidget(self._poll_interval_field)

    def _build_kel_watch_interval_section(self):
        self.content_layout.addSpacing(24)

        header = QLabel("KEL Watch Interval")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)
        self.content_layout.addSpacing(8)

        hint = QLabel(
            "Seconds between witness queries for the issuer's key event log. "
            "The embedded sentinel checks all witnesses in parallel at this cadence "
            "and fetches any new ixn events needed for credential TEL verification. (default: 30)"
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(10)

        self._kel_watch_interval_field = FloatingLabelLineEdit("KEL Watch Interval")
        self._kel_watch_interval_field.setFixedWidth(200)
        self._kel_watch_interval_field.line_edit.editingFinished.connect(self._on_kel_watch_interval_changed)
        self.content_layout.addWidget(self._kel_watch_interval_field)

    def _make_subsection(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        return w

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def _get_db(self):
        if not self.app or not self.app.vault:
            return None
        return self.app.vault.plugin_state.get("keriguard_user", {}).get("db")

    def _get_settings(self) -> KERIGuardUserSettings | None:
        db = self._get_db()
        return db.keriguardUserSettings.get(keys=("settings",)) if db else None

    def _save_settings(self, **kwargs) -> None:
        db = self._get_db()
        if db is None:
            return
        existing = db.keriguardUserSettings.get(keys=("settings",)) or KERIGuardUserSettings()
        for key, value in kwargs.items():
            if value is not None:
                setattr(existing, key, value)
        db.keriguardUserSettings.pin(keys=("settings",), val=existing)
        if self.app and self.app.vault:
            self.app.vault.plugin_state.get("keriguard_user", {})["settings"] = existing

    def _load_settings(self) -> None:
        settings = self._get_settings()

        self._source_dropdown.combo_box.blockSignals(True)
        try:
            source = settings.credential_source if settings else "registrar"
            idx = self._source_dropdown.findText(source)
            self._source_dropdown.setCurrentIndex(idx if idx >= 0 else 0)
            self._registrar_url_section.setVisible(source == "registrar")
        finally:
            self._source_dropdown.combo_box.blockSignals(False)

        self._registrar_url_field.setText(settings.registrar_url if settings else "")
        self._issuer_aid_field.setText(settings.issuer_aid if settings else "")
        self._issuer_oobi_field.setText(settings.issuer_oobi if settings else "")
        self._config_dir_field.setText(settings.config_dir if settings else "")
        self._export_dir_field.setText(settings.export_dir if settings else "")
        self._poll_interval_field.setText(
            str(settings.poll_interval) if settings else "30"
        )
        self._kel_watch_interval_field.setText(
            str(settings.kel_watch_interval) if settings else "30"
        )

    # ------------------------------------------------------------------
    # Browse actions
    # ------------------------------------------------------------------

    def _browse_config_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Select WireGuard Config Directory",
            self._config_dir_field.text().strip() or "",
        )
        if chosen:
            self._config_dir_field.setText(chosen)
            self._save_settings(config_dir=chosen)

    def _browse_export_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Export Directory",
            self._export_dir_field.text().strip() or "",
        )
        if chosen:
            self._export_dir_field.setText(chosen)
            self._save_settings(export_dir=chosen)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_source_changed(self, text: str):
        self._registrar_url_section.setVisible(text == "registrar")
        self._save_settings(credential_source=text)

    def _on_registrar_url_changed(self):
        self._save_settings(registrar_url=self._registrar_url_field.text().strip())

    def _on_issuer_oobi_changed(self):
        self._save_settings(issuer_oobi=self._issuer_oobi_field.text().strip())

    def _on_config_dir_changed(self):
        self._save_settings(config_dir=self._config_dir_field.text().strip())

    def _on_export_dir_changed(self):
        self._save_settings(export_dir=self._export_dir_field.text().strip())

    def _on_poll_interval_changed(self):
        text = self._poll_interval_field.text().strip()
        try:
            interval = max(5, int(text))
        except ValueError:
            interval = 30
        self._poll_interval_field.setText(str(interval))
        self._save_settings(poll_interval=interval)

    def _on_kel_watch_interval_changed(self):
        text = self._kel_watch_interval_field.text().strip()
        try:
            interval = max(5, int(text))
        except ValueError:
            interval = 30
        self._kel_watch_interval_field.setText(str(interval))
        self._save_settings(kel_watch_interval=interval)

    def on_show(self) -> None:
        self._load_settings()

    def set_vault_name(self, vault_name: str):
        self.vault_name = vault_name