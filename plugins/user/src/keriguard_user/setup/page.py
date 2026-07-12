# -*- encoding: utf-8 -*-
"""keriguard_user.setup.page — SetupPage for KERIGuard user plugin initialisation."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import qasync
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QFileDialog,
)
from PySide6.QtCore import Signal, QTimer

from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.page import LocksmithFormPage
from locksmith.ui.toolkit.widgets.fields import FloatingLabelComboBox, FloatingLabelLineEdit, LocksmithPlainTextEdit
from locksmith.ui.toolkit.widgets.buttons import (
    LocksmithButton, LocksmithIconButton, LocksmithCopyButton
)

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = logging.getLogger(__name__)


class SetupPage(LocksmithFormPage):
    """Guided setup page: import a KERIGuard config file and initialise the user plugin."""

    setup_complete = Signal()
    initialization_done = Signal()

    def __init__(self, app: "LocksmithApplication", parent: "VaultPage | None" = None):
        super().__init__(
            title="KERIGuard Setup",
            icon_path=":/assets/custom/logos/keriguard-darkmode.png",
            parent=parent,
        )
        self.app = app
        self.vault_name = ""
        self._config = None
        self._initializing = False
        self._nav_timer: "QTimer | None" = None
        self._build_content()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_content(self):
        layout = self.content_layout

        desc = QLabel(
            "Connect this vault to a KERIGuard network to automatically receive "
            "and apply WireGuard credentials issued by the network administrator."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 15px; color: {colors.TEXT_SUBTLE};")
        layout.addWidget(desc)
        layout.addSpacing(32)

        self._add_section_header(
            layout,
            header="Import Config File",
            sub="Load a keriguard.conf YAML file to pre-fill the fields below.",
        )
        layout.addSpacing(10)
        self._build_config_file_row(layout)
        layout.addSpacing(32)

        self._add_section_header(
            layout,
            header="Credential Source",
            sub="Where this vault fetches issued credentials from.",
        )
        layout.addSpacing(10)
        self._credential_source_combo = FloatingLabelComboBox("Credential Source")
        self._credential_source_combo.setFixedWidth(420)
        for mode in ["registrar", "healthKERI"]:
            self._credential_source_combo.addItem(mode)
        self._credential_source_combo.currentTextChanged.connect(self._on_source_changed)
        layout.addWidget(self._credential_source_combo)
        layout.addSpacing(32)

        self._add_section_header(
            layout,
            header="Issuer",
            sub="The AID and OOBI of the administrator who will issue credentials to this vault.",
        )
        layout.addSpacing(10)
        self._issuer_aid_field = FloatingLabelLineEdit("Issuer AID")
        self._issuer_aid_field.setFixedWidth(420)
        layout.addWidget(self._issuer_aid_field)
        layout.addSpacing(8)
        self._issuer_oobi_field = FloatingLabelLineEdit("Issuer OOBI")
        self._issuer_oobi_field.setFixedWidth(420)
        layout.addWidget(self._issuer_oobi_field)
        layout.addSpacing(32)

        self._registrar_section = QWidget()
        reg_layout = QVBoxLayout(self._registrar_section)
        reg_layout.setContentsMargins(0, 0, 0, 0)
        reg_layout.setSpacing(8)
        self._add_section_header(
            reg_layout,
            header="Registrar URL",
            sub="Base URL of the KERIGuard registrar service.",
        )
        reg_layout.addSpacing(10)
        self._registrar_url_field = FloatingLabelLineEdit("Registrar URL")
        self._registrar_url_field.setFixedWidth(420)
        reg_layout.addWidget(self._registrar_url_field)
        layout.addWidget(self._registrar_section)
        layout.addSpacing(32)

        self._add_section_header(
            layout,
            header="WireGuard Config Directory",
            sub="Directory where WireGuard .conf files will be written.",
        )
        layout.addSpacing(10)
        self._build_dir_row(layout, "_config_dir_field", "WireGuard Config Directory", "_browse_config_dir")
        layout.addSpacing(32)

        self._summary_frame = self._build_summary_card()
        layout.addWidget(self._summary_frame)
        layout.addSpacing(32)

        self._sudoers_section = self._build_sudoers_section()
        layout.addWidget(self._sudoers_section)
        layout.addSpacing(32)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._init_button = LocksmithButton("Initialize")
        self._init_button.setFixedWidth(140)
        self._init_button.clicked.connect(self._on_initialize)
        btn_row.addWidget(self._init_button)
        btn_row.addStretch()
        layout.addLayout(btn_row)

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

    def _build_config_file_row(self, layout):
        row = QHBoxLayout()
        row.setSpacing(8)
        self._config_file_field = FloatingLabelLineEdit("Config File Path")
        self._config_file_field.setFixedWidth(375)
        self._config_file_field.line_edit.textChanged.connect(self._on_config_file_text_changed)
        row.addWidget(self._config_file_field)
        browse_btn = LocksmithIconButton(":/assets/material-icons/browse.svg", tooltip="Browse files")
        browse_btn.setFixedSize(48, 48)
        browse_btn.clicked.connect(self._browse_config_file)
        row.addWidget(browse_btn)
        row.addStretch()
        layout.addLayout(row)
        self._loading_config = False

    def _build_dir_row(self, layout, field_attr: str, label: str, browse_method: str):
        row = QHBoxLayout()
        row.setSpacing(8)
        field = FloatingLabelLineEdit(label)
        field.setFixedWidth(375)
        setattr(self, field_attr, field)
        field.line_edit.textChanged.connect(self._on_config_dir_text_changed)
        row.addWidget(field)
        browse_btn = LocksmithIconButton(":/assets/material-icons/browse.svg", tooltip="Browse")
        browse_btn.setFixedSize(48, 48)
        browse_btn.clicked.connect(getattr(self, browse_method))
        row.addWidget(browse_btn)
        row.addStretch()
        layout.addLayout(row)

    def _build_summary_card(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ border: 1px solid {colors.BORDER}; border-radius: 8px; "
            f"background: white; }}"
            f" QFrame QLabel {{ border: none; }}"
        )
        frame.setFixedWidth(420)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(8)

        lbl = QLabel("Summary")
        lbl.setStyleSheet("font-weight: 700; font-size: 14px;")
        inner.addWidget(lbl)
        self._summary_title_lbl = lbl

        self._summary_issuer = self._make_summary_row(inner, "Issuer AID")
        self._summary_source = self._make_summary_row(inner, "Credential Source")
        self._summary_config_dir = self._make_summary_row(inner, "Config Directory")
        return frame

    def _make_summary_row(self, layout, label: str) -> QLabel:
        row = QHBoxLayout()
        lbl = QLabel(label + ":")
        lbl.setFixedWidth(140)
        lbl.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE};")
        row.addWidget(lbl)
        val = QLabel("—")
        val.setStyleSheet("font-size: 13px;")
        row.addWidget(val, 1)
        layout.addLayout(row)
        return val

    def _build_sudoers_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Top row: header + subheader on the left, copy button on the right
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)

        header_col = QVBoxLayout()
        header_col.setSpacing(6)
        header = QLabel("Sudoers Configuration")
        header.setStyleSheet("font-weight: 600; font-size: 16px;")
        header_col.addWidget(header)

        hint = QLabel(
            "To allow WireGuard interfaces to be brought up automatically, add the "
            "following to /etc/sudoers.d/wireguard-keriguard.\n"
            "Note: the chown rule transfers config file ownership back to your user "
            "after writing, so the process can read it back (wg-quick still works "
            "as root regardless of ownership):"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE};")
        hint.setFixedWidth(800)
        header_col.addWidget(hint)

        top_row.addLayout(header_col)

        wg_quick = shutil.which("wg-quick") or "/usr/local/bin/wg-quick"
        wg = shutil.which("wg") or "/usr/local/bin/wg"
        _chown = shutil.which("chown") or "/usr/sbin/chown"
        snippet = (
            f"# KERIGuard WireGuard access\n"
            f"%admin ALL=(ALL) NOPASSWD: {wg_quick} up /usr/local/var/wireguard/keriguard/*\n"
            f"%admin ALL=(ALL) NOPASSWD: {wg_quick} down /usr/local/var/wireguard/keriguard/*\n"
            f"%admin ALL=(ALL) NOPASSWD: {wg_quick} strip /usr/local/var/wireguard/keriguard/*\n"
            f"%admin ALL=(ALL) NOPASSWD: {wg} show *\n"
            f"%admin ALL=(ALL) NOPASSWD: {wg} syncconf * /dev/stdin\n"
            f"%admin ALL=(ALL) NOPASSWD: /usr/bin/tee /usr/local/var/wireguard/keriguard/*\n"
            f"%admin ALL=(ALL) NOPASSWD: {_chown} * /usr/local/var/wireguard/keriguard/*"
        )

        self._sudoers_copy_button = LocksmithCopyButton(
            copy_content=snippet,
            tooltip="Copy sudoers snippet",
            icon_size=24,
        )
        top_row.addSpacing(10)
        top_row.addWidget(self._sudoers_copy_button)
        top_row.addStretch()

        layout.addLayout(top_row)

        self._sudoers_snippet_edit = LocksmithPlainTextEdit()
        self._sudoers_snippet_edit.setPlainText(snippet)
        self._sudoers_snippet_edit.setReadOnly(True)
        self._sudoers_snippet_edit._bg_color = "white"
        self._sudoers_snippet_edit._update_styling()
        # Size to fit content: count lines and set a reasonable fixed height
        line_count = snippet.count("\n") + 1
        self._sudoers_snippet_edit.setFixedHeight(max(line_count * 20 + 30, 100))
        self._sudoers_snippet_edit.setFixedWidth(840)

        snippet_row = QHBoxLayout()
        snippet_row.setContentsMargins(0, 0, 0, 0)
        snippet_row.addWidget(self._sudoers_snippet_edit)
        snippet_row.addStretch()
        layout.addLayout(snippet_row)
        layout.addWidget(self._sudoers_snippet_edit)

        return section


    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_config_file_text_changed(self, text: str):
        """Attempt to load the config whenever the path field text changes."""
        path = text.strip()
        if path:
            p = Path(path)
            if p.is_file() and p.suffix in (".conf", ".yaml", ".yml"):
                self._load_config_file(path)
                self.clear_error()
                return
            if self.error_label.text() != "Invalid config file path":
                self.show_error("Invalid config file path")
        else:
            self.clear_error()
        self._config = None
        self._update_summary()

    def _on_config_dir_text_changed(self, text: str):
        """Validate the directory path whenever the field text changes."""
        path = text.strip()
        if path:
            p = Path(path)
            if p.is_dir():
                self.clear_error()
                self._update_summary()
                return
            if self.error_label.text() != "Invalid WireGuard config directory":
                self.show_error("Invalid WireGuard config directory")
        else:
            self.clear_error()
        self._update_summary()

    def _browse_config_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select KERIGuard Config File", "", "YAML Files (*.conf *.yaml *.yml);;All Files (*)"
        )
        if not path:
            return
        self._config_file_field.setText(path)
        self._load_config_file(path)

    def _load_config_file(self, path: str):
        try:
            from keriguard.core.initializing import KeriguardConfig
            self._config = KeriguardConfig.load(path)
        except Exception as exc:
            logger.warning(f"SetupPage: could not load config file {path!r}: {exc}")
            self._config = None
            return

        issuer_aid = self._config.issuer.aid or ""
        issuer_oobi = self._config.issuer.oobi or ""
        self._issuer_aid_field.setText(issuer_aid)
        self._issuer_oobi_field.setText(issuer_oobi)

        if not self._config.local:
            idx = self._credential_source_combo.findText("healthKERI")
            self._credential_source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            idx = self._credential_source_combo.findText("registrar")
            self._credential_source_combo.setCurrentIndex(idx if idx >= 0 else 0)
            if self._config.registrar.url:
                self._registrar_url_field.setText(self._config.registrar.url)

        self._update_summary()

    def _browse_config_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Select WireGuard Config Directory",
            self._config_dir_field.text().strip() or "",
        )
        if chosen:
            self._config_dir_field.setText(chosen)
            self._update_summary()

    def _on_source_changed(self, text: str):
        self._registrar_section.setVisible(text == "registrar")
        self._update_summary()

    def _update_summary(self):
        issuer_aid = self._issuer_aid_field.text().strip()
        source = self._credential_source_combo.currentText()
        config_dir = self._config_dir_field.text().strip()

        short_aid = (issuer_aid[:20] + "…") if len(issuer_aid) > 20 else issuer_aid
        self._summary_issuer.setText(short_aid or "—")
        self._summary_source.setText(source or "—")
        self._summary_config_dir.setText(config_dir or "—")

    def _show_summary_complete(self):
        self._summary_frame.setStyleSheet(
            f"QFrame {{ border: 1px solid {colors.SUCCESS}; border-radius: 8px; "
            f"background: #f0fdf4; }}"
            f" QFrame QLabel {{ border: none; }}"
        )
        self._summary_title_lbl.setText("✓ Initialized")
        self._summary_title_lbl.setStyleSheet(
            f"font-weight: 700; font-size: 14px; color: {colors.SUCCESS};"
        )
        for lbl in (self._summary_issuer, self._summary_source, self._summary_config_dir):
            lbl.setStyleSheet(f"font-size: 13px; color: {colors.SUCCESS};")

    def _show_summary_default(self):
        self._summary_frame.setStyleSheet(
            f"QFrame {{ border: 1px solid {colors.BORDER}; border-radius: 8px; "
            f"background: white; }}"
            f" QFrame QLabel {{ border: none; }}"
        )
        self._summary_title_lbl.setText("Summary")
        self._summary_title_lbl.setStyleSheet("font-weight: 700; font-size: 14px;")
        for lbl in (self._summary_issuer, self._summary_source, self._summary_config_dir):
            lbl.setStyleSheet("font-size: 13px;")

    @qasync.asyncSlot()
    async def _on_initialize(self):
        if self._initializing:
            return
        self._initializing = True
        self._init_button.setText("Initializing…")
        self._init_button.setEnabled(False)

        try:
            await self._run_initialize()
        except Exception as exc:
            logger.exception(f"SetupPage: initialization failed: {exc}")
            self._init_button.setText("Initialize")
            self._init_button.setEnabled(True)
        finally:
            self._initializing = False

    async def _run_initialize(self):
        if not self.app or not self.app.vault:
            return

        vault = self.app.vault
        loop = asyncio.get_event_loop()

        issuer_aid = self._issuer_aid_field.text().strip()
        issuer_oobi = self._issuer_oobi_field.text().strip()
        registrar_url = self._registrar_url_field.text().strip()
        config_dir = self._config_dir_field.text().strip()
        credential_source = self._credential_source_combo.currentText()

        if not issuer_aid or not issuer_oobi or not config_dir:
            logger.warning("SetupPage: missing required fields")
            return

        from keriguard.core.initializing import load_schema, load_oobi
        from keriguard.core.wireguarding import SCHEMA_OOBIS, Schema
        from keriguard.db.basing import KERIGuardBaser

        # 1. Load schemas — prefer local files, fall back to remote OOBI
        await self._load_schemas(vault, loop)

        # 2. Resolve issuer OOBI
        await loop.run_in_executor(None, load_oobi, vault.hby, issuer_oobi, "issuer")

        # 3. Registrar mode: resolve registrar OOBI, store registrar/issuer in kgb
        if credential_source == "registrar" and registrar_url and self._config:
            kgb = vault.plugin_state.get("keriguard_user", {}).get("kgb")
            if kgb and self._config.registrar.oobi:
                await loop.run_in_executor(
                    None, load_oobi, vault.hby, self._config.registrar.oobi, "registrar"
                )
                kgb.set_registrar(
                    aid=self._config.registrar.aid,
                    keriguard_aid=self._config.registrar.keriguard.aid if self._config.registrar.keriguard else "",
                    oobi=self._config.registrar.oobi,
                    keriguard_oobi=self._config.registrar.keriguard.oobi if self._config.registrar.keriguard else "",
                    url=registrar_url,
                    ipaddress=self._config.registrar.keriguard.ipaddress if self._config.registrar.keriguard else None,
                    endpoint=self._config.registrar.keriguard.endpoint if self._config.registrar.keriguard else None,
                )
                kgb.set_issuer(aid=issuer_aid, oobi=issuer_oobi)

        # 4. Create watcher hab
        watcher_alias = f"{vault.hby.name}-user-watcher"
        existing_watcher = vault.hby.habByName(watcher_alias)
        if existing_watcher is None:
            vault.hby.makeHab(
                name=watcher_alias,
                transferable=False,
                icount=1,
                isith="1",
                ncount=1,
                nsith="1",
                toad=0,
            )

        # 5. Save settings
        from keriguard_user.db.basing import KERIGuardUserSettings
        kg_user_db = vault.plugin_state.get("keriguard_user", {}).get("db")
        if kg_user_db is None:
            logger.warning("SetupPage: user db not available in plugin_state")
            return

        settings = KERIGuardUserSettings(
            credential_source=credential_source,
            registrar_url=registrar_url,
            issuer_aid=issuer_aid,
            issuer_oobi=issuer_oobi,
            watcher_alias=watcher_alias,
            config_dir=config_dir,
            export_dir="",
            poll_interval=30,
            is_initialized=True,
        )
        kg_user_db.keriguardUserSettings.pin(keys=("settings",), val=settings)
        vault.plugin_state["keriguard_user"]["settings"] = settings

        # 6. Show success and schedule auto-navigation after 1 second
        self._init_button.hide()
        self._show_summary_complete()
        self.show_success(
            "KERIGuard user plugin initialized successfully. "
            "This vault will now automatically receive and apply WireGuard "
            "credentials from the configured issuer."
        )
        self.initialization_done.emit()
        self._nav_timer = QTimer()
        self._nav_timer.setSingleShot(True)
        self._nav_timer.timeout.connect(self.setup_complete.emit)
        self._nav_timer.start(1000)
        logger.info("SetupPage: KERIGuard user plugin initialized")

    async def _load_schemas(self, vault, loop):
        """Load KERIGuard schemas into the vault db.

        Tries in order: (1) already present — skip; (2) local schema files bundled
        with the keriguard package; (3) remote SCHEMA_OOBIS URL as last resort.
        """
        from pathlib import Path
        from keri.core import scheming
        from keriguard.core.wireguarding import SCHEMA_OOBIS, Schema
        from keriguard.core.initializing import load_schema
        import keriguard as _kg_mod

        # Local schema files live alongside the keriguard source tree.
        # Works for editable installs; silently skipped for wheel installs.
        _kg_root = Path(_kg_mod.__file__).parent.parent.parent
        _schema_dir = _kg_root / "schema"
        _local_filenames = {
            Schema.INTERFACE_SCHEMA: "wireguard-interface-v1.0.0.json",
            Schema.CONNECTION_SCHEMA: "wireguard-connection-v1.0.0.json",
        }

        # The user plugin only needs the interface and connection schemas.
        # TRUSTNET_SCHEMA is admin-only and its S3 OOBI is access-restricted.
        _needed = {Schema.INTERFACE_SCHEMA, Schema.CONNECTION_SCHEMA}

        for schema_said, schema_oobi in SCHEMA_OOBIS.items():
            if schema_said not in _needed:
                continue

            # 1. Already loaded — nothing to do.
            if vault.hby.db.schema.get(keys=(schema_said,)) is not None:
                logger.info(f"Schema {schema_said[:16]}… already in db, skipping")
                continue

            # 2. Try local file.
            local_path = _schema_dir / _local_filenames.get(schema_said, "")
            if local_path.exists() and local_path.is_file():
                try:
                    raw = local_path.read_bytes()
                    schemer = scheming.Schemer(raw=bytearray(raw))
                    if schemer.said == schema_said:
                        vault.hby.db.schema.pin(keys=(schemer.said,), val=schemer)
                        logger.info(f"Schema {schema_said[:16]}… loaded from local file")
                        continue
                except Exception as exc:
                    logger.warning(f"Local schema load failed ({local_path.name}): {exc}")

            # 3. Fall back to remote OOBI.
            try:
                await loop.run_in_executor(None, load_schema, vault.hby, schema_oobi, schema_said)
                logger.info(f"Schema {schema_said[:16]}… loaded from remote OOBI")
            except Exception as exc:
                logger.warning(f"Remote schema load failed ({schema_said[:16]}…): {exc}")

    def set_vault_name(self, vault_name):
        self.vault_name = vault_name

    def on_show(self):
        if self._nav_timer is not None:
            self._nav_timer.stop()
            self._nav_timer = None

        self.clear_error()
        self.clear_success()

        self._config = None
        self._initializing = False

        self._config_file_field.setText("")
        self._issuer_aid_field.setText("")
        self._issuer_oobi_field.setText("")
        self._registrar_url_field.setText("")
        self._config_dir_field.setText("")
        self._credential_source_combo.setCurrentIndex(0)

        self._init_button.setText("Initialize")
        self._init_button.setEnabled(True)
        self._init_button.show()

        self._show_summary_default()
        self._on_source_changed(self._credential_source_combo.currentText())