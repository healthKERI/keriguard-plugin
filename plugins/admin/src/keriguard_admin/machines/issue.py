# -*- encoding: utf-8 -*-
"""keriguard.machines.issue — Issue Interface Credential form page."""
import re
from typing import TYPE_CHECKING

import qasync
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QHBoxLayout, QWidget, QVBoxLayout, QPushButton
from keri import help
from keri.app import connecting
from keri.app.habbing import GroupHab
from keri.peer import exchanging
from keri.help import helping

from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.page import LocksmithFormPage
from locksmith.ui.toolkit.widgets.buttons import LocksmithButton, LocksmithInvertedButton
from locksmith.ui.toolkit.widgets.fields import FloatingLabelLineEdit, FloatingLabelComboBox

from keriguard.core.kering import Issuer
from ..core.remoting import (
    push_credential_to_registrar,
    push_credential_via_essr,
    push_introduction_to_registrar,
    _ensure_issuer_watched,
)

if TYPE_CHECKING:
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)

_IFACE_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class IssueInterfaceCredentialPage(LocksmithFormPage):
    """Full-page form to issue a WireGuard interface credential to a machine."""

    back_clicked = Signal()

    def __init__(self, app, parent: "VaultPage | None" = None):
        super().__init__(
            title="Issue Interface Credential",
            icon_path=":/assets/material-icons/devices.svg",
            parent=parent,
        )
        self.app = app
        self.vault_name = ""
        self._recipient_map: dict[str, str] = {}
        self._issuer_map: dict[str, str] = {}
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        content_layout = self.content_layout

        # Top description
        desc = QLabel(
            "Issue a WireGuard interface credential to a machine. The credential defines "
            "the machine's VPN address and port and is pushed to the registrar for the "
            "sentinel to retrieve."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 15px; color: {colors.TEXT_SUBTLE};")
        content_layout.addWidget(desc)
        content_layout.addSpacing(40)

        # --- Section 1: Machine ---
        content_layout.addWidget(self._section_header("Select Machine"))
        content_layout.addSpacing(10)
        sub1 = QLabel(
            "The machine that will receive this credential. Must be a resolved KERI "
            "contact or a local identifier."
        )
        sub1.setWordWrap(True)
        sub1.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE}; font-weight: 200;")
        content_layout.addWidget(sub1)
        content_layout.addSpacing(10)

        self._recipient_dropdown = FloatingLabelComboBox("Recipient Machine")
        self._recipient_dropdown.setFixedWidth(500)
        content_layout.addWidget(self._recipient_dropdown)
        content_layout.addSpacing(10)

        self._issuer_dropdown = FloatingLabelComboBox("Issuing Identifier")
        self._issuer_dropdown.setFixedWidth(500)
        content_layout.addWidget(self._issuer_dropdown)
        content_layout.addSpacing(40)

        # --- Section 2: Interface Configuration ---
        content_layout.addWidget(self._section_header("Interface Configuration"))
        content_layout.addSpacing(10)
        sub2 = QLabel("WireGuard interface parameters for this machine's VPN interface.")
        sub2.setWordWrap(True)
        sub2.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE}; font-weight: 200;")
        content_layout.addWidget(sub2)
        content_layout.addSpacing(10)

        self._iface_name = FloatingLabelLineEdit("Interface Name")
        self._iface_name.setFixedWidth(500)
        content_layout.addWidget(self._iface_name)
        content_layout.addSpacing(8)

        self._listen_port = FloatingLabelLineEdit("Listen Port")
        self._listen_port.setFixedWidth(500)
        content_layout.addWidget(self._listen_port)
        content_layout.addSpacing(8)

        self._address = FloatingLabelLineEdit("Address (CIDR)")
        self._address.setFixedWidth(500)
        content_layout.addWidget(self._address)
        content_layout.addSpacing(40)

        # --- Section 3: Metadata ---
        content_layout.addWidget(self._section_header("Metadata"))
        content_layout.addSpacing(10)
        sub3 = QLabel("Optional labels attached to the credential.")
        sub3.setWordWrap(True)
        sub3.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE}; font-weight: 200;")
        content_layout.addWidget(sub3)
        content_layout.addSpacing(10)

        self._description = FloatingLabelLineEdit("Description")
        self._description.setFixedWidth(500)
        content_layout.addWidget(self._description)
        content_layout.addSpacing(8)

        self._environment = FloatingLabelComboBox("Environment")
        self._environment.setFixedWidth(500)
        for env in ["", "production", "staging", "development", "test"]:
            self._environment.addItem(env)
        self._environment.setCurrentIndex(0)
        content_layout.addWidget(self._environment)
        content_layout.addSpacing(20)

        # --- Advanced section ---
        self._advanced_toggle = QPushButton("▶ Advanced options")
        self._advanced_toggle.setStyleSheet(
            f"background: transparent; border: none; color: {colors.TEXT_SUBTLE}; "
            f"font-size: 13px; text-align: left;"
        )
        self._advanced_toggle.setCursor(self._advanced_toggle.cursor())
        self._advanced_toggle.clicked.connect(self._toggle_advanced)
        content_layout.addWidget(self._advanced_toggle)

        self._advanced_widget = QWidget()
        adv_layout = QVBoxLayout(self._advanced_widget)
        adv_layout.setContentsMargins(0, 8, 0, 0)
        adv_layout.setSpacing(8)

        self._dns = FloatingLabelLineEdit("DNS Server(s)")
        self._dns.setFixedWidth(500)
        adv_layout.addWidget(self._dns)

        self._mtu = FloatingLabelLineEdit("MTU")
        self._mtu.setFixedWidth(500)
        adv_layout.addWidget(self._mtu)

        self._table = FloatingLabelLineEdit("Routing Table")
        self._table.setFixedWidth(500)
        adv_layout.addWidget(self._table)

        self._advanced_widget.setVisible(False)
        content_layout.addWidget(self._advanced_widget)
        content_layout.addSpacing(40)

        # --- Button row ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = LocksmithInvertedButton("Cancel")
        self._cancel_btn.setFixedWidth(140)
        self._cancel_btn.clicked.connect(self.back_clicked.emit)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addSpacing(12)
        self._issue_btn = LocksmithButton("Issue Credential")
        self._issue_btn.setFixedWidth(180)
        self._issue_btn.clicked.connect(self._on_issue_clicked)
        btn_row.addWidget(self._issue_btn)
        btn_row.addStretch()
        content_layout.addLayout(btn_row)

        content_layout.addStretch()

    def _section_header(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"font-weight: bold; font-size: 20px; color: {colors.TEXT_MENU};"
        )
        return label

    def _toggle_advanced(self):
        visible = not self._advanced_widget.isVisible()
        self._advanced_widget.setVisible(visible)
        self._advanced_toggle.setText("▼ Advanced options" if visible else "▶ Advanced options")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_vault_name(self, vault_name: str):
        self.vault_name = vault_name

    def on_show(self):
        self.clear_error()
        self.clear_success()
        self._load_dropdowns()
        self._reset_form()

    def _load_dropdowns(self):
        if not self.app or not self.app.vault:
            return

        hby = self.app.vault.hby
        self._recipient_map = {}
        self._issuer_map = {}

        self._recipient_dropdown.clear()
        self._issuer_dropdown.clear()

        # hby.habs is keyed by AID prefix; hab.name is the human alias
        for aid, hab in hby.habs.items():
            if isinstance(hab, GroupHab):
                continue
            display = f"{hab.name} — {aid}"
            self._recipient_map[display] = aid
            self._issuer_map[display] = hab.name
            self._recipient_dropdown.addItem(display)
            self._issuer_dropdown.addItem(display)

        try:
            for rm_id in self.app.vault.org.list():
                display = f"{rm_id['alias']} — {rm_id['id']}"
                self._recipient_map[display] = rm_id['id']
                self._recipient_dropdown.addItem(display)
        except Exception as exc:
            logger.warning(f"Could not load contacts: {exc}")

        self._recipient_dropdown.setCurrentIndex(-1)
        self._issuer_dropdown.setCurrentIndex(-1)

        if len(self._issuer_map) == 1:
            self._issuer_dropdown.setCurrentIndex(0)

    def _reset_form(self):
        self._recipient_dropdown.setCurrentIndex(-1)
        if len(self._issuer_map) != 1:
            self._issuer_dropdown.setCurrentIndex(-1)
        self._iface_name.clear()
        self._listen_port.clear()
        self._address.clear()
        self._description.clear()
        self._environment.setCurrentIndex(0)
        self._dns.clear()
        self._mtu.clear()
        self._table.clear()
        self._advanced_widget.setVisible(False)
        self._advanced_toggle.setText("▶ Advanced options")
        self._issue_btn.setEnabled(True)
        self._issue_btn.setText("Issue Credential")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_form(self) -> bool:
        if self._recipient_dropdown.currentIndex() < 0:
            self.show_error("Please select a recipient machine.")
            return False
        if self._issuer_dropdown.currentIndex() < 0:
            self.show_error("Please select an issuing identifier.")
            return False
        iface_name = self._iface_name.text().strip()
        if not iface_name or not _IFACE_NAME_RE.match(iface_name) or len(iface_name) > 64:
            self.show_error(
                "Interface name is required, must be 1–64 alphanumeric/underscore/hyphen characters."
            )
            return False
        port_text = self._listen_port.text().strip()
        if not port_text:
            self.show_error("Listen port is required.")
            return False
        try:
            port = int(port_text)
            if not (1 <= port <= 65535):
                raise ValueError()
        except ValueError:
            self.show_error("Listen port must be an integer between 1 and 65535.")
            return False
        if not self._address.text().strip():
            self.show_error("Address (CIDR) is required.")
            return False
        desc = self._description.text().strip()
        if desc and len(desc) > 256:
            self.show_error("Description must be 256 characters or fewer.")
            return False
        mtu_text = self._mtu.text().strip()
        if mtu_text:
            try:
                mtu = int(mtu_text)
                if not (576 <= mtu <= 65535):
                    raise ValueError()
            except ValueError:
                self.show_error("MTU must be an integer between 576 and 65535.")
                return False
        return True

    # ------------------------------------------------------------------
    # Issuance
    # ------------------------------------------------------------------

    @qasync.asyncSlot()
    async def _on_issue_clicked(self):
        if not self._validate_form():
            return

        self._issue_btn.setEnabled(False)
        self._issue_btn.setText("Issuing…")
        self.clear_error()

        try:
            hby = self.app.vault.hby
            rgy = self.app.vault.rgy
            issuer_alias = self._issuer_map[self._issuer_dropdown.currentText()]
            hab = hby.habByName(issuer_alias)

            recipient_aid = self._recipient_map[self._recipient_dropdown.currentText()]

            org = connecting.Organizer(hby=hby)
            contact = org.get(recipient_aid)
            recipient_alias = contact.get("alias", "") if contact else ""
            recipient_oobi = contact.get("oobi", "") if contact else ""

            issuer = Issuer(hby=hby, hab=hab, rgy=rgy)

            interface_config = {
                "listenPort": int(self._listen_port.text().strip()),
                "address": [self._address.text().strip()],
            }
            if (dns_text := self._dns.text().strip()):
                interface_config["dns"] = [s.strip() for s in dns_text.split(",") if s.strip()]
            if (mtu_text := self._mtu.text().strip()):
                interface_config["mtu"] = int(mtu_text)
            if (table_text := self._table.text().strip()):
                interface_config["table"] = table_text

            interface_metadata = {"interfaceName": self._iface_name.text().strip()}
            if (desc := self._description.text().strip()):
                interface_metadata["interfaceDescription"] = desc
            if (env := self._environment.currentText()):
                interface_metadata["environment"] = env

            kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
            settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
            essr = self.app.vault.plugin_state.get("keriguard", {}).get("essr")

            creder = await issuer.issue_interface_credential(
                recipient=recipient_aid,
                registry_name=settings.registry_name if settings else "",
                interface=interface_config,
                interface_metadata=interface_metadata,
                auths={},
            )

            grant = issuer.grant(creder.said, recipient_aid)
            grant_bytes = bytes(grant)

            introduction_bytes = None
            if recipient_oobi:
                intro_data = dict(
                    aid=recipient_aid,
                    alias=recipient_alias,
                    oobi=recipient_oobi,
                )
                exn, end = exchanging.exchange(
                    route="/introduction",
                    payload=intro_data,
                    sender=hab.pre,
                    date=helping.nowIso8601(),
                )
                introduction_bytes = bytes(hab.endorse(serder=exn, last=False, pipelined=False))

            publish_mode = settings.publish_mode if settings else "registrar"

            if publish_mode == "healthKERI" and essr:
                await push_credential_via_essr(grant_bytes, essr, creder.said, introduction_bytes)
                account = self.app.vault.plugin_state.get("keriguard", {}).get("account")
                team = self.app.vault.plugin_state.get("keriguard", {}).get("team")
                if account and team:
                    await _ensure_issuer_watched(essr, hab, hby, account, team)
            elif settings and settings.registrar_url:
                await push_credential_to_registrar(grant_bytes, settings.registrar_url)
                if introduction_bytes:
                    await push_introduction_to_registrar(introduction_bytes, settings.registrar_url)

            if hasattr(self.app.vault, 'signals') and self.app.vault.signals:
                self.app.vault.signals.emit_doer_event(
                    doer_name="IssueCredentialDoer",
                    event_type="credential_issued",
                    data={"schema": creder.schema, "said": creder.said},
                )

            self.show_success(
                f"Interface credential issued successfully. SAID: {creder.said}"
            )

        except Exception as exc:
            logger.exception(f"IssueInterfaceCredentialPage: issuance failed: {exc}")
            self.show_error(f"Issuance failed: {exc}")
        finally:
            self._issue_btn.setEnabled(True)
            self._issue_btn.setText("Issue Credential")