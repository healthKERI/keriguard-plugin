# -*- encoding: utf-8 -*-
"""keriguard.connections.issue — Issue Connection Credential form page."""
import re
from typing import TYPE_CHECKING

import qasync
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QHBoxLayout, QWidget, QVBoxLayout, QPushButton
from keri import help
from keri.app.habbing import GroupHab

from locksmith.ui import colors
from locksmith.ui.toolkit.widgets.page import LocksmithFormPage
from locksmith.ui.toolkit.widgets.buttons import LocksmithButton, LocksmithInvertedButton
from locksmith.ui.toolkit.widgets.fields import FloatingLabelLineEdit, FloatingLabelComboBox

from keriguard.core.kering import Issuer
from keriguard.core.wireguarding import Schema
from ..core.kering import issue_connection_credential_by_saids
from ..core.remoting import (
    push_credential_to_registrar,
    push_credential_via_essr,
    _ensure_issuer_watched,
)

if TYPE_CHECKING:
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)

_CONN_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class IssueConnectionCredentialPage(LocksmithFormPage):
    """Full-page form to issue a WireGuard connection credential linking two machines."""

    back_clicked = Signal()

    def __init__(self, app, parent: "VaultPage | None" = None):
        super().__init__(
            title="Issue Connection Credential",
            icon_path=":/assets/material-icons/airline_stops.svg",
            parent=parent,
        )
        self.app = app
        self.vault_name = ""
        self._issuer_map: dict[str, str] = {}
        self._machines: list[dict] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self):
        cl = self.content_layout

        # Top description
        desc = QLabel(
            "Issue a WireGuard connection credential linking two machines. The credential "
            "is pushed to the registrar and allows each machine's sentinel to configure "
            "the peer section of its WireGuard interface."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 15px; color: {colors.TEXT_SUBTLE};")
        cl.addWidget(desc)
        cl.addSpacing(40)

        # --- Section: Issuing Identifier ---
        cl.addWidget(self._section_header("Issuing Identifier"))
        cl.addSpacing(10)

        self._issuer_dropdown = FloatingLabelComboBox("Issuing Identifier")
        self._issuer_dropdown.setFixedWidth(500)
        cl.addWidget(self._issuer_dropdown)
        cl.addSpacing(40)

        # --- Section: Peer 1 ---
        cl.addWidget(self._section_header("Peer 1"))
        cl.addSpacing(10)
        sub_p1 = QLabel(
            "The first machine in this connection and how it appears as a peer to Peer 2."
        )
        sub_p1.setWordWrap(True)
        sub_p1.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE}; font-weight: 200;")
        cl.addWidget(sub_p1)
        cl.addSpacing(10)

        self._peer1_machine_dropdown = FloatingLabelComboBox("Peer 1 Machine")
        self._peer1_machine_dropdown.setFixedWidth(500)
        cl.addWidget(self._peer1_machine_dropdown)
        cl.addSpacing(8)

        self._peer1_allowed_ips = FloatingLabelLineEdit("Allowed IPs (CIDR)")
        self._peer1_allowed_ips.setFixedWidth(500)
        cl.addWidget(self._peer1_allowed_ips)
        cl.addSpacing(8)

        self._peer1_endpoint = FloatingLabelLineEdit("Endpoint (host:port)")
        self._peer1_endpoint.setFixedWidth(500)
        cl.addWidget(self._peer1_endpoint)
        cl.addSpacing(10)

        self._peer1_advanced_toggle = QPushButton("▶ Advanced options")
        self._peer1_advanced_toggle.setStyleSheet(
            f"background: transparent; border: none; color: {colors.TEXT_SUBTLE}; "
            f"font-size: 13px; text-align: left;"
        )
        self._peer1_advanced_toggle.clicked.connect(self._toggle_peer1_advanced)
        cl.addWidget(self._peer1_advanced_toggle)

        self._peer1_advanced = QWidget()
        p1_adv_layout = QVBoxLayout(self._peer1_advanced)
        p1_adv_layout.setContentsMargins(0, 8, 0, 0)
        p1_adv_layout.setSpacing(8)
        self._peer1_keepalive = FloatingLabelLineEdit("Keepalive (seconds)")
        self._peer1_keepalive.setFixedWidth(500)
        p1_adv_layout.addWidget(self._peer1_keepalive)
        self._peer1_peer_name = FloatingLabelLineEdit("Peer Name")
        self._peer1_peer_name.setFixedWidth(500)
        p1_adv_layout.addWidget(self._peer1_peer_name)
        self._peer1_advanced.setVisible(False)
        cl.addWidget(self._peer1_advanced)
        cl.addSpacing(40)

        # --- Section: Peer 2 ---
        cl.addWidget(self._section_header("Peer 2"))
        cl.addSpacing(10)
        sub_p2 = QLabel(
            "The second machine in this connection and how it appears as a peer to Peer 1."
        )
        sub_p2.setWordWrap(True)
        sub_p2.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE}; font-weight: 200;")
        cl.addWidget(sub_p2)
        cl.addSpacing(10)

        self._peer2_machine_dropdown = FloatingLabelComboBox("Peer 2 Machine")
        self._peer2_machine_dropdown.setFixedWidth(500)
        cl.addWidget(self._peer2_machine_dropdown)
        cl.addSpacing(8)

        self._peer2_allowed_ips = FloatingLabelLineEdit("Allowed IPs (CIDR)")
        self._peer2_allowed_ips.setFixedWidth(500)
        cl.addWidget(self._peer2_allowed_ips)
        cl.addSpacing(8)

        self._peer2_endpoint = FloatingLabelLineEdit("Endpoint (host:port)")
        self._peer2_endpoint.setFixedWidth(500)
        cl.addWidget(self._peer2_endpoint)
        cl.addSpacing(10)

        self._peer2_advanced_toggle = QPushButton("▶ Advanced options")
        self._peer2_advanced_toggle.setStyleSheet(
            f"background: transparent; border: none; color: {colors.TEXT_SUBTLE}; "
            f"font-size: 13px; text-align: left;"
        )
        self._peer2_advanced_toggle.clicked.connect(self._toggle_peer2_advanced)
        cl.addWidget(self._peer2_advanced_toggle)

        self._peer2_advanced = QWidget()
        p2_adv_layout = QVBoxLayout(self._peer2_advanced)
        p2_adv_layout.setContentsMargins(0, 8, 0, 0)
        p2_adv_layout.setSpacing(8)
        self._peer2_keepalive = FloatingLabelLineEdit("Keepalive (seconds)")
        self._peer2_keepalive.setFixedWidth(500)
        p2_adv_layout.addWidget(self._peer2_keepalive)
        self._peer2_peer_name = FloatingLabelLineEdit("Peer Name")
        self._peer2_peer_name.setFixedWidth(500)
        p2_adv_layout.addWidget(self._peer2_peer_name)
        self._peer2_advanced.setVisible(False)
        cl.addWidget(self._peer2_advanced)
        cl.addSpacing(40)

        # --- Section: Connection ---
        cl.addWidget(self._section_header("Connection"))
        cl.addSpacing(10)
        sub_conn = QLabel(
            "Shared metadata describing this connection. Applied to both peer blocks."
        )
        sub_conn.setWordWrap(True)
        sub_conn.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SUBTLE}; font-weight: 200;")
        cl.addWidget(sub_conn)
        cl.addSpacing(10)

        self._conn_name = FloatingLabelLineEdit("Connection Name")
        self._conn_name.setFixedWidth(500)
        cl.addWidget(self._conn_name)
        cl.addSpacing(8)

        self._purpose = FloatingLabelLineEdit("Purpose")
        self._purpose.setFixedWidth(500)
        cl.addWidget(self._purpose)
        cl.addSpacing(8)

        self._environment = FloatingLabelComboBox("Environment")
        self._environment.setFixedWidth(500)
        for env in ["", "production", "staging", "development", "test"]:
            self._environment.addItem(env)
        self._environment.setCurrentIndex(0)
        cl.addWidget(self._environment)
        cl.addSpacing(8)

        self._bandwidth_class = FloatingLabelComboBox("Bandwidth Class")
        self._bandwidth_class.setFixedWidth(500)
        for bw in ["", "low", "medium", "high", "critical"]:
            self._bandwidth_class.addItem(bw)
        self._bandwidth_class.setCurrentIndex(0)
        cl.addWidget(self._bandwidth_class)
        cl.addSpacing(40)

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
        cl.addLayout(btn_row)

        cl.addStretch()

    def _section_header(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"font-weight: bold; font-size: 20px; color: {colors.TEXT_MENU};"
        )
        return label

    def _toggle_peer1_advanced(self):
        visible = not self._peer1_advanced.isVisible()
        self._peer1_advanced.setVisible(visible)
        self._peer1_advanced_toggle.setText(
            "▼ Advanced options" if visible else "▶ Advanced options"
        )

    def _toggle_peer2_advanced(self):
        visible = not self._peer2_advanced.isVisible()
        self._peer2_advanced.setVisible(visible)
        self._peer2_advanced_toggle.setText(
            "▼ Advanced options" if visible else "▶ Advanced options"
        )

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
        rgy = self.app.vault.rgy

        # Issuer dropdown — hby.habs is keyed by AID prefix; hab.name is the human alias
        self._issuer_map = {}
        self._issuer_dropdown.clear()
        for aid, hab in hby.habs.items():
            if isinstance(hab, GroupHab):
                continue
            display = f"{hab.name} — {aid}"
            self._issuer_map[display] = hab.name
            self._issuer_dropdown.addItem(display)
        self._issuer_dropdown.setCurrentIndex(-1)
        if len(self._issuer_map) == 1:
            self._issuer_dropdown.setCurrentIndex(0)

        # Machine dropdowns — populated from issued interface credentials
        self._machines = []
        self._peer1_machine_dropdown.clear()
        self._peer2_machine_dropdown.clear()

        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
        if not settings or not settings.registry_name:
            return

        registry = rgy.registryByName(settings.registry_name)
        if registry is None:
            return

        try:
            for saider in (rgy.reger.schms.get(keys=Schema.INTERFACE_SCHEMA) or []):
                try:
                    creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                    if creder.regi != registry.regk:
                        continue
                    iface_name = creder.attrib.get("interfaceMetadata", {}).get("interfaceName", "")
                    recipient_aid = creder.attrib.get("i", "")
                    recipient_name = recipient_aid[:12] + "…" if recipient_aid else "unknown"
                    local_hab = hby.habByPre(recipient_aid) if recipient_aid else None
                    if local_hab:
                        recipient_name = local_hab.name
                    elif recipient_aid:
                        contact = self.app.vault.org.get(recipient_aid)
                        if contact:
                            recipient_name = contact.get("alias", recipient_name)
                    label = f"{iface_name} ({recipient_name}) — {creder.said}"
                    self._machines.append({"label": label, "said": creder.said})
                    self._peer1_machine_dropdown.addItem(label)
                    self._peer2_machine_dropdown.addItem(label)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning(f"Could not load interface credentials: {exc}")

        self._peer1_machine_dropdown.setCurrentIndex(-1)
        self._peer2_machine_dropdown.setCurrentIndex(-1)

    def _reset_form(self):
        if len(self._issuer_map) != 1:
            self._issuer_dropdown.setCurrentIndex(-1)
        self._peer1_machine_dropdown.setCurrentIndex(-1)
        self._peer2_machine_dropdown.setCurrentIndex(-1)
        self._peer1_allowed_ips.clear()
        self._peer1_endpoint.clear()
        self._peer1_keepalive.clear()
        self._peer1_peer_name.clear()
        self._peer1_advanced.setVisible(False)
        self._peer1_advanced_toggle.setText("▶ Advanced options")
        self._peer2_allowed_ips.clear()
        self._peer2_endpoint.clear()
        self._peer2_keepalive.clear()
        self._peer2_peer_name.clear()
        self._peer2_advanced.setVisible(False)
        self._peer2_advanced_toggle.setText("▶ Advanced options")
        self._conn_name.clear()
        self._purpose.clear()
        self._environment.setCurrentIndex(0)
        self._bandwidth_class.setCurrentIndex(0)
        self._issue_btn.setEnabled(True)
        self._issue_btn.setText("Issue Credential")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_form(self) -> bool:
        if self._issuer_dropdown.currentIndex() < 0:
            self.show_error("Please select an issuing identifier.")
            return False
        if self._peer1_machine_dropdown.currentIndex() < 0:
            self.show_error("Please select a machine for Peer 1.")
            return False
        if self._peer2_machine_dropdown.currentIndex() < 0:
            self.show_error("Please select a machine for Peer 2.")
            return False
        idx1 = self._peer1_machine_dropdown.currentIndex()
        idx2 = self._peer2_machine_dropdown.currentIndex()
        if self._machines[idx1]["said"] == self._machines[idx2]["said"]:
            self.show_error("Peer 1 and Peer 2 must be different machines.")
            return False
        if not self._peer1_allowed_ips.text().strip():
            self.show_error("Peer 1 Allowed IPs is required.")
            return False
        if not self._peer2_allowed_ips.text().strip():
            self.show_error("Peer 2 Allowed IPs is required.")
            return False
        conn_name = self._conn_name.text().strip()
        if not conn_name or not _CONN_NAME_RE.match(conn_name) or len(conn_name) > 64:
            self.show_error(
                "Connection name is required, must be 1–64 alphanumeric/underscore/hyphen characters."
            )
            return False
        for field_name, field_widget in [
            ("Peer 1 keepalive", self._peer1_keepalive),
            ("Peer 2 keepalive", self._peer2_keepalive),
        ]:
            val = field_widget.text().strip()
            if val:
                try:
                    if int(val) < 0:
                        raise ValueError()
                except ValueError:
                    self.show_error(f"{field_name} must be a non-negative integer.")
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

            iface1_said = self._machines[self._peer1_machine_dropdown.currentIndex()]["said"]
            iface2_said = self._machines[self._peer2_machine_dropdown.currentIndex()]["said"]

            def _parse_allowed_ips(text: str) -> list:
                return [s.strip() for s in text.split(",") if s.strip()]

            peer1_config: dict = {
                "allowedIps": _parse_allowed_ips(self._peer1_allowed_ips.text())
            }
            if (ep := self._peer1_endpoint.text().strip()):
                peer1_config["endpoint"] = ep
            if (ka := self._peer1_keepalive.text().strip()):
                peer1_config["persistentKeepalive"] = int(ka)
            if (pn := self._peer1_peer_name.text().strip()):
                peer1_config["peerName"] = pn

            peer2_config: dict = {
                "allowedIps": _parse_allowed_ips(self._peer2_allowed_ips.text())
            }
            if (ep := self._peer2_endpoint.text().strip()):
                peer2_config["endpoint"] = ep
            if (ka := self._peer2_keepalive.text().strip()):
                peer2_config["persistentKeepalive"] = int(ka)
            if (pn := self._peer2_peer_name.text().strip()):
                peer2_config["peerName"] = pn

            conn_meta: dict = {"connectionName": self._conn_name.text().strip()}
            if (purpose := self._purpose.text().strip()):
                conn_meta["purpose"] = purpose
            if (env := self._environment.currentText()):
                conn_meta["environment"] = env
            if (bw := self._bandwidth_class.currentText()):
                conn_meta["bandwidthClass"] = bw

            issuer = Issuer(hby=hby, hab=hab, rgy=rgy)

            creder = await issue_connection_credential_by_saids(
                issuer=issuer,
                iface1_said=iface1_said,
                peer1_config=peer1_config,
                iface2_said=iface2_said,
                peer2_config=peer2_config,
                conn_meta=conn_meta,
                auths={},
            )

            kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
            settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
            essr = self.app.vault.plugin_state.get("keriguard", {}).get("essr")

            # Connection credentials have no single recipient AID in attrib;
            # use the first peer's interface credential recipient as the grant addressee.
            iface1_creder, *_ = rgy.reger.cloneCred(said=iface1_said)
            recipient_aid = iface1_creder.attrib.get("i")
            grant = issuer.grant(creder.said, recipient_aid)
            grant_bytes = bytes(grant)

            publish_mode = settings.publish_mode if settings else "registrar"

            if publish_mode == "healthKERI" and essr:
                await push_credential_via_essr(grant_bytes, essr)
                account = self.app.vault.plugin_state.get("keriguard", {}).get("account")
                team = self.app.vault.plugin_state.get("keriguard", {}).get("team")
                if account and team:
                    await _ensure_issuer_watched(essr, hab, hby, account, team)
            elif settings and settings.registrar_url:
                await push_credential_to_registrar(grant_bytes, settings.registrar_url)

            if hasattr(self.app.vault, 'signals') and self.app.vault.signals:
                self.app.vault.signals.emit_doer_event(
                    doer_name="IssueCredentialDoer",
                    event_type="credential_issued",
                    data={"schema": creder.schema, "said": creder.said},
                )

            self.show_success(
                f"Connection credential issued successfully. SAID: {creder.said}"
            )

        except Exception as exc:
            logger.exception(f"IssueConnectionCredentialPage: issuance failed: {exc}")
            self.show_error(f"Issuance failed: {exc}")
        finally:
            self._issue_btn.setEnabled(True)
            self._issue_btn.setText("Issue Credential")