# -*- encoding: utf-8 -*-
"""keriguard.settings — KERIGuard plugin settings page."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel

from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.page import LocksmithFormPage
from locksmith.ui.toolkit.widgets.fields import FloatingLabelComboBox, FloatingLabelLineEdit

from .db.basing import KERIGuardSettings

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = logging.getLogger(__name__)

class KERIGuardSettingsPage(LocksmithFormPage):

    def __init__(self, app: "LocksmithApplication", parent: "VaultPage | None" = None):
        super().__init__(
            title="KERIGuard Settings",
            icon_path=":/assets/material-icons/settings-hover.svg",
            parent=parent,
        )
        self.app = app
        self._build_content()


    def _build_content(self):
        self._build_registry_section()
        self._build_registrar_url_section()
        self.content_layout.addStretch()


    def _build_registry_section(self):
        header = QLabel("Credential Registry")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)

        hint = QLabel(
            "Select the KERI registry that scopes all KERIGuard credentials. "
            "If none appear, create one with: kli vc registry incept"
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(8)

        self._registry_dropdown = FloatingLabelComboBox("Registry")
        self._registry_dropdown.setFixedWidth(420)
        self._registry_dropdown.currentTextChanged.connect(self._on_registry_changed)
        self.content_layout.addWidget(self._registry_dropdown)

    def _build_registrar_url_section(self):
        self.content_layout.addSpacing(24)

        header = QLabel("Registrar URL")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.content_layout.addWidget(header)

        hint = QLabel(
            "URL of the KERIGuard registrar service. After issuing a credential, "
            "the plugin pushes CESR bytes here so sentinel nodes can retrieve it."
        )
        hint.setStyleSheet(f"color: {colors.TEXT_SUBTLE}; font-size: 13px;")
        hint.setWordWrap(True)
        self.content_layout.addWidget(hint)
        self.content_layout.addSpacing(8)

        self._registrar_url_field = FloatingLabelLineEdit("Registrar URL")
        self._registrar_url_field.setFixedWidth(420)
        self._registrar_url_field.line_edit.editingFinished.connect(
            self._on_registrar_url_changed
        )
        self.content_layout.addWidget(self._registrar_url_field)

    def _get_settings(self) -> KERIGuardSettings | None:
        if not self.app or not self.app.vault:
            return None
        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        return kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None

    def _save_settings(
            self,
            registry_name: str | None = None,
            registrar_url: str | None = None,
    ) -> None:
        if not self.app or not self.app.vault:
            return
        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        if kg_db is None:
            return
        existing = kg_db.keriguardSettings.get(keys=("settings",)) or KERIGuardSettings()
        if registry_name is not None:
            existing.registry_name = registry_name
        if registrar_url is not None:
            existing.registrar_url = registrar_url
        kg_db.keriguardSettings.pin(keys=("settings",), val=existing)

    def _load_settings(self) -> None:
        if not self.app or not self.app.vault:
            return
        settings = self._get_settings()

        self._registry_dropdown.combo_box.blockSignals(True)
        try:
            self._registry_dropdown.clear()
            for registry in self.app.vault.rgy.regs.values():
                self._registry_dropdown.addItem(registry.name)
            if settings and settings.registry_name:
                idx = self._registry_dropdown.findText(settings.registry_name)
                self._registry_dropdown.setCurrentIndex(idx if idx >= 0 else -1)
            else:
                self._registry_dropdown.setCurrentIndex(-1)
        finally:
            self._registry_dropdown.combo_box.blockSignals(False)

        self._registrar_url_field.setText(
            settings.registrar_url if settings else ""
        )

    def _on_registry_changed(self, text: str) -> None:
        self._save_settings(registry_name=text)
        logger.info(f"KERIGuard registry selected: {text!r}")

    def _on_registrar_url_changed(self) -> None:
        url = self._registrar_url_field.text().strip()
        self._save_settings(registrar_url=url)
        logger.info(f"KERIGuard registrar URL saved: {url!r}")

    def on_show(self) -> None:
        self._load_settings()