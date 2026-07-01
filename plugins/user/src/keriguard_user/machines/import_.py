# -*- encoding: utf-8 -*-
"""keriguard_user.machines.import_ — Import interface credential from .cesr file."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import qasync
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QFileDialog, QLabel

from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.page import LocksmithFormPage
from locksmith.ui.toolkit.widgets.fields import FloatingLabelLineEdit
from locksmith.ui.toolkit.widgets.buttons import LocksmithIconButton, LocksmithButton

from keriguard.core.wireguarding import Schema

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = logging.getLogger(__name__)


def _all_saids(rgy) -> set:
    saids = set()
    for schema in (Schema.INTERFACE_SCHEMA, Schema.CONNECTION_SCHEMA):
        for saider in (rgy.reger.schms.get(keys=schema) or []):
            saids.add(saider.qb64)
    return saids


class ImportInterfaceCredentialPage(LocksmithFormPage):
    """Import an interface credential from a .cesr grant file."""

    back_clicked = Signal()
    import_complete = Signal()

    def __init__(self, app: "LocksmithApplication", parent: "VaultPage | None" = None):
        super().__init__(
            title="Import Interface Credential",
            icon_path=":/assets/material-icons/devices.svg",
            parent=parent,
        )
        self.app = app
        self.vault_name = ""
        self._importing = False
        self._build_content()

    def _build_content(self):
        layout = self.content_layout

        self._add_section_header(
            layout,
            header="Import Interface Credential",
            sub=(
                "Select a .cesr file exported by the KERIGuard admin. "
                "The credential will be loaded into your vault and a WireGuard interface "
                "configuration will be generated automatically."
            ),
        )
        layout.addSpacing(16)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._file_field = FloatingLabelLineEdit("Credential File (.cesr)")
        self._file_field.setFixedWidth(375)
        row.addWidget(self._file_field)
        browse_btn = LocksmithIconButton(":/assets/material-icons/browse.svg", tooltip="Browse")
        browse_btn.setFixedSize(48, 48)
        browse_btn.clicked.connect(self._browse_file)
        row.addWidget(browse_btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addSpacing(24)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._import_btn = LocksmithButton("Import")
        self._import_btn.setFixedWidth(120)
        self._import_btn.clicked.connect(self._on_import)
        btn_row.addWidget(self._import_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addSpacing(16)

        self._status_label = self._make_status_label()
        layout.addWidget(self._status_label)
        layout.addStretch()

    def _add_section_header(self, layout: QVBoxLayout, header: str, sub: str):
        h = QLabel(header)
        h.setStyleSheet(f"font-weight: bold; font-size: 20px; color: {colors.TEXT_MENU};")
        layout.addWidget(h)
        layout.addSpacing(6)
        s = QLabel(sub)
        s.setWordWrap(True)
        s.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE}; font-weight: 200;")
        layout.addWidget(s)

    def _make_status_label(self):
        from PySide6.QtWidgets import QLabel
        label = QLabel("")
        label.setWordWrap(True)
        label.hide()
        return label

    def _browse_file(self):
        settings = self._get_settings()
        start_dir = (settings.export_dir if settings and settings.export_dir else "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Credential File", start_dir,
            "CESR Files (*.cesr);;All Files (*)",
        )
        if path:
            self._file_field.setText(path)

    def _get_settings(self):
        if not self.app or not self.app.vault:
            return None
        return self.app.vault.plugin_state.get("keriguard_user", {}).get("settings")

    def _show_status(self, message: str, error: bool = False):
        self._status_label.setText(message)
        color = "#dc3545" if error else "#28a745"
        self._status_label.setStyleSheet(f"font-size: 14px; color: {color};")
        self._status_label.show()

    @qasync.asyncSlot()
    async def _on_import(self):
        if self._importing:
            return
        path = self._file_field.text().strip()
        if not path:
            self._show_status("Please select a .cesr file.", error=True)
            return

        self._importing = True
        self._import_btn.setText("Importing…")
        self._import_btn.setEnabled(False)
        self._status_label.hide()

        try:
            await self._run_import(path)
        except Exception as exc:
            logger.exception(f"ImportInterfaceCredentialPage: import failed: {exc}")
            self._show_status(f"Import failed: {exc}", error=True)
        finally:
            self._importing = False
            self._import_btn.setText("Import")
            self._import_btn.setEnabled(True)

    async def _run_import(self, path: str):
        if not self.app or not self.app.vault:
            return

        vault = self.app.vault
        ims = Path(path).read_bytes()

        # Extract recipient AID from grant exchange
        from keri.core.serdering import SerderKERI
        try:
            grant_serder = SerderKERI(raw=ims)
            recp = grant_serder.ked.get("a", {}).get("i", "")
        except Exception as exc:
            logger.warning(f"Could not parse grant serder: {exc}")
            recp = ""

        user_aids = set(vault.hby.habs.keys())
        if recp and recp not in user_aids:
            self._show_status(
                f"Credential is not addressed to this vault (recipient: {recp[:16]}…).",
                error=True,
            )
            return

        # Find hab for the recipient
        if recp:
            hab = vault.hby.habByPre(recp) or vault.hby.habs.get(recp)
        else:
            # Fall back to first available hab
            hab = next(iter(vault.hby.habs.values()), None)

        if hab is None:
            self._show_status("No suitable identifier found in this vault.", error=True)
            return

        before = _all_saids(vault.rgy)

        # Admit / parse the credential
        loop = asyncio.get_event_loop()
        try:
            from keri.app import ipexing
            admitter = ipexing.Admitter(vault.hby, hab, vault.rgy)
            await loop.run_in_executor(None, admitter.parse, ims)
        except Exception as exc:
            logger.warning(f"Admitter.parse failed, falling back to Parser: {exc}")
            try:
                from keri.core import parsing
                from keri.vdr import verifying
                verifier = verifying.Verifier(hby=vault.hby, reger=vault.rgy.reger)
                psr = parsing.Parser(kvy=vault.hby.kvy, tvy=vault.rgy.tvy, vry=verifier)
                await loop.run_in_executor(None, psr.parse, ims)
            except Exception as exc2:
                self._show_status(f"Could not parse credential file: {exc2}", error=True)
                return

        after = _all_saids(vault.rgy)
        new_saids = after - before

        if not new_saids:
            self._show_status(
                "Credential was already present or could not be verified.", error=False
            )
            self.import_complete.emit()
            return

        # Apply WireGuard config for each new credential
        applier = vault.plugin_state.get("keriguard_user", {}).get("applier")
        results = []
        for said in new_saids:
            if applier:
                result = await applier.apply(said)
                results.append((said, result))
            else:
                results.append((said, "no_applier"))

        pending_sudo = any(r == "pending_sudo" for _, r in results)
        if pending_sudo:
            self._show_status(
                "Credential imported. WireGuard interface is pending — ensure sudoers is configured.",
            )
        else:
            self._show_status(f"Successfully imported {len(new_saids)} credential(s).")

        self.import_complete.emit()

    def set_vault_name(self, vault_name: str):
        self.vault_name = vault_name

    def on_show(self):
        self._file_field.setText("")
        self._status_label.hide()