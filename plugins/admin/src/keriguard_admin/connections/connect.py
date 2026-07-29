# -*- encoding: utf-8 -*-
"""keriguard.connections.issue — Issue Connection Credential dialog."""
import re
from typing import TYPE_CHECKING

import qasync
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QHBoxLayout, QWidget, QVBoxLayout, QPushButton, QScrollArea
from keri import help
from keri.help import helping
from keriguard.core.kering import Issuer
from keriguard.core.wireguarding import Schema
from locksmith.ui import colors
from locksmith.ui.toolkit.widgets import (
    LocksmithDialog,
    LocksmithButton,
    LocksmithInvertedButton
)
from locksmith.ui.toolkit.widgets.fields import FloatingLabelLineEdit, FloatingLabelComboBox, AutocompleteLineEdit
from locksmith.ui.vault.healthKERI.core import remoting
from locksmith.ui.vault.identifiers.authenticate import WitnessAuthenticationDialog

from ..core.kering import issue_connection_credential_by_saids
from ..core.remoting import (
    push_credential_to_registrar,
    push_credential_via_essr,
    _ensure_issuer_watched,
)

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)

_CONN_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class MachineAutocomplete(AutocompleteLineEdit):
    """Autocomplete control for searching live machines."""

    def __init__(self, app: "LocksmithApplication", placeholder_text: str, parent: QWidget | None = None):
        super().__init__(
            placeholder_text=placeholder_text,
            parent=parent,
            max_results=5,
            min_chars=1,  # Allow searching with empty string
            debounce_ms=100
        )
        self.app = app
        self._credentials_by_aid: dict[str, dict] = {}  # Maps machine AID to credential SAID
        self._search_task = None

    async def perform_search(self, query: str) -> list[dict]:
        """
        Override to return cached results from async search.

        The actual search is triggered asynchronously via _trigger_async_search.
        This method just returns the cached results.
        """
        # Trigger async search (will update cached results when complete)
        return await self._trigger_async_search(query)

    async def _trigger_async_search(self, query: str):
        """Trigger async search for machines."""
        if not self.app or not self.app.vault:
            return []

        try:

            # Fetch live machines from healthKERI API
            response = await remoting.fetch_live_machines(
                app=self.app,
                page=0,
                page_size=50,  # Fetch more for better search results
                filter_term=query if query else None,
                machine_type="keriguard"
            )

            if not response.get('success'):
                logger.warning(f"Failed to fetch machines: {response.get('error')}")
                return []

            machines = response.get('machines', [])
            if not machines:
                logger.debug("0 machines returned")
                return []

            # Build credential mapping from interface credentials
            self._credentials_by_aid.clear()
            kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
            settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None

            if settings and settings.registry_name:
                registry = self.app.vault.rgy.registryByName(settings.registry_name)
                if registry:
                    rgy = self.app.vault.rgy
                    for saider in (rgy.reger.schms.get(keys=Schema.INTERFACE_SCHEMA) or []):
                        try:
                            creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                            if creder.regi != registry.regk:
                                continue

                            # Map the issuee AID to the credential SAID
                            payload = creder.attrib
                            issuee = payload.get("i", "")
                            if issuee:
                                interface_data = payload.get("interface", {})

                                data = {
                                    "said": creder.said,
                                    "address": interface_data.get("address", []),
                                    "listenPort": interface_data.get("listenPort"),
                                }
                                self._credentials_by_aid[issuee] = data
                        except Exception as exc:
                            logger.debug(f"Skipping credential {saider.qb64}: {exc}")


            # Format results for autocomplete
            results = []
            for machine in machines:
                machine_id = machine.get('id', '')
                machine_name = machine.get('name', '')
                aid = machine.get('server_aid', '')
                tags = machine.get('tags', [])

                # Only include machines that have an interface credential
                if aid in self._credentials_by_aid:
                    data = self._credentials_by_aid[aid]
                    credential_said = data.get("said")
                    tags_str = ', '.join(tags) if tags else ''

                    # Create display string
                    display = machine_name
                    if tags_str:
                        display = f"{machine_name} ({tags_str})"

                    ipaddress = data.get("address", [])[0]
                    listen_port = data.get("listenPort")

                    results.append({
                        'display': display,
                        'value': {
                            'id': machine_id,
                            'name': machine_name,
                            'server_aid': aid,
                            'credential_said': credential_said,
                            'tags': tags,
                            'ipaddress': ipaddress,
                            'listen_port': listen_port
                        }
                    })

            # Update cached results and refresh popup
            return results

        except Exception as exc:
            logger.exception(f"Error during machine search: {exc}")
            return []

    def get_selected_credential_said(self) -> str | None:
        """Get the credential SAID of the selected machine."""
        # Check if there's selected data from itemSelected signal
        if hasattr(self, '_selected_value') and self._selected_value:
            if isinstance(self._selected_value, dict):
                return self._selected_value.get('said')
        return None

    def set_selected_value(self, value):
        """Store the selected value when an item is selected."""
        self._selected_value = value


class IssueConnectionCredentialDialog(LocksmithDialog):
    """Dialog for issuing a WireGuard connection credential linking two machines."""

    connection_issued = Signal(str)  # Emits credential SAID when issued

    def __init__(
            self,
            app: "LocksmithApplication",
            parent: "VaultPage | None" = None
    ):
        """
        Initialize the IssueConnectionCredentialDialog.

        Args:
            app: Application instance
            parent: Parent widget (VaultPage)
        """
        self.app = app
        self.vault_name = ""
        self.peer1_name = ""
        self.peer2_name = ""
        self._selected_peer1_machine = None
        self._selected_peer2_machine = None
        self._should_reset_on_show = True  # Flag to control reset behavior

        # Create content widget with scroll area for long form
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(f"QScrollArea {{ background-color: {colors.BACKGROUND_CONTENT}; border: none; }}")

        content_widget = QWidget()
        content_widget.setStyleSheet(f"background-color: {colors.BACKGROUND_CONTENT};")
        self.layout = QVBoxLayout(content_widget)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(15)

        # Create button row
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.cancel_button = LocksmithInvertedButton("Cancel")
        button_row.addWidget(self.cancel_button)

        button_row.addSpacing(10)

        self.issue_button = LocksmithButton("Connect Devices")
        button_row.addWidget(self.issue_button)

        # Connect signals
        self.cancel_button.clicked.connect(self.close)
        self.issue_button.clicked.connect(self._on_issue_clicked)

        # Initialize parent dialog
        super().__init__(
            parent=parent,
            title="Issue Connection Credential",
            title_icon=":/assets/material-icons/airline_stops.svg",
            content=scroll_area,
            buttons=button_row,
            show_overlay=False
        )

        # Build the form UI
        self._build_content()
        self.setFixedSize(700, 900)

        scroll_area.setWidget(content_widget)
        self.app.vault.signals.auth_codes_entered.connect(self._on_auth_codes_entered)

        logger.info("IssueConnectionCredentialDialog initialized")

    def _build_content(self):
        """Build the form content."""
        # Top description
        desc = QLabel(
            "Issue a WireGuard connection credential linking two machines. The credential "
            "is pushed to the registrar and allows each machine's sentinel to configure "
            "the peer section of its WireGuard interface."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 13px; color: {colors.TEXT_SECONDARY};")
        self.layout.addWidget(desc)
        self.layout.addSpacing(15)

        # --- Section: Peer 1 ---
        self.layout.addWidget(self._section_header("Peer 1"))
        self.layout.addSpacing(5)
        sub_p1 = QLabel(
            "The first machine in this connection and how it appears as a peer to Peer 2."
        )
        sub_p1.setWordWrap(True)
        sub_p1.setStyleSheet(f"font-size: 12px; color: {colors.TEXT_SECONDARY};")
        self.layout.addWidget(sub_p1)
        self.layout.addSpacing(5)

        # Replace dropdown with autocomplete
        self._peer1_machine = MachineAutocomplete(self.app, "Search Peer 1 Machine by name", self)
        self._peer1_machine.setFixedWidth(600)
        self._peer1_machine.itemSelected.connect(lambda value: self._peer1_machine_selected(value))
        self.layout.addWidget(self._peer1_machine)
        self.layout.addSpacing(8)

        self._peer1_allowed_ips = FloatingLabelLineEdit("Allowed IPs (CIDR)")
        self._peer1_allowed_ips.setFixedWidth(600)
        self._peer1_allowed_ips.setReadOnly(True)
        self.layout.addWidget(self._peer1_allowed_ips)
        self.layout.addSpacing(8)

        self._peer1_endpoint_row = QHBoxLayout()
        self._peer1_endpoint_row.setContentsMargins(0, 0, 0, 0)
        self._peer1_endpoint_row.setSpacing(10)
        self._peer1_endpoint = FloatingLabelLineEdit("Endpoint (host or ip address)")
        self._peer1_endpoint.setFixedWidth(400)
        self._peer1_endpoint_row.addWidget(self._peer1_endpoint)
        self._peer1_listen_port = QLabel()
        self._peer1_endpoint_row.addWidget(self._peer1_listen_port)

        self.layout.addLayout(self._peer1_endpoint_row)
        self.layout.addSpacing(5)

        self._peer1_advanced_toggle = QPushButton("▶ Advanced options")
        self._peer1_advanced_toggle.setStyleSheet(
            f"background: transparent; border: none; color: {colors.TEXT_SUBTLE}; "
            f"font-size: 12px; text-align: left;"
        )
        self._peer1_advanced_toggle.clicked.connect(self._toggle_peer1_advanced)
        self.layout.addWidget(self._peer1_advanced_toggle)

        self._peer1_advanced = QWidget()
        p1_adv_layout = QVBoxLayout(self._peer1_advanced)
        p1_adv_layout.setContentsMargins(0, 8, 0, 0)
        p1_adv_layout.setSpacing(8)
        self._peer1_keepalive = FloatingLabelLineEdit("Keepalive (seconds)")
        self._peer1_keepalive.setFixedWidth(600)
        p1_adv_layout.addWidget(self._peer1_keepalive)
        self._peer1_advanced.setVisible(False)
        self.layout.addWidget(self._peer1_advanced)
        self.layout.addSpacing(20)

        # --- Section: Peer 2 ---
        self.layout.addWidget(self._section_header("Peer 2"))
        self.layout.addSpacing(5)
        sub_p2 = QLabel(
            "The second machine in this connection and how it appears as a peer to Peer 1."
        )
        sub_p2.setWordWrap(True)
        sub_p2.setStyleSheet(f"font-size: 12px; color: {colors.TEXT_SECONDARY};")
        self.layout.addWidget(sub_p2)
        self.layout.addSpacing(5)

        # Replace dropdown with autocomplete
        self._peer2_machine = MachineAutocomplete(self.app, "Search Peer 2 Machine by name", self)
        self._peer2_machine.setFixedWidth(600)
        self._peer2_machine.itemSelected.connect(lambda value: self._peer2_machine_selected(value))
        self.layout.addWidget(self._peer2_machine)
        self.layout.addSpacing(8)

        self._peer2_allowed_ips = FloatingLabelLineEdit("Allowed IPs (CIDR)")
        self._peer2_allowed_ips.setFixedWidth(600)
        self._peer2_allowed_ips.setReadOnly(True)
        self.layout.addWidget(self._peer2_allowed_ips)
        self.layout.addSpacing(8)

        self._peer2_endpoint_row = QHBoxLayout()
        self._peer2_endpoint_row.setContentsMargins(0, 0, 0, 0)
        self._peer2_endpoint = FloatingLabelLineEdit("Endpoint (host or ip address)")
        self._peer2_endpoint.setFixedWidth(400)
        self._peer2_endpoint_row.addWidget(self._peer2_endpoint)
        self._peer2_listen_port = QLabel()
        self._peer2_endpoint_row.addWidget(self._peer2_listen_port)

        self.layout.addLayout(self._peer2_endpoint_row)


        self.layout.addSpacing(5)

        self._peer2_advanced_toggle = QPushButton("▶ Advanced options")
        self._peer2_advanced_toggle.setStyleSheet(
            f"background: transparent; border: none; color: {colors.TEXT_SUBTLE}; "
            f"font-size: 12px; text-align: left;"
        )
        self._peer2_advanced_toggle.clicked.connect(self._toggle_peer2_advanced)
        self.layout.addWidget(self._peer2_advanced_toggle)

        self._peer2_advanced = QWidget()
        p2_adv_layout = QVBoxLayout(self._peer2_advanced)
        p2_adv_layout.setContentsMargins(0, 8, 0, 0)
        p2_adv_layout.setSpacing(8)
        self._peer2_keepalive = FloatingLabelLineEdit("Keepalive (seconds)")
        self._peer2_keepalive.setFixedWidth(600)
        p2_adv_layout.addWidget(self._peer2_keepalive)
        self._peer2_peer_name = FloatingLabelLineEdit("Peer Name")
        self._peer2_peer_name.setFixedWidth(600)
        p2_adv_layout.addWidget(self._peer2_peer_name)
        self._peer2_advanced.setVisible(False)
        self.layout.addWidget(self._peer2_advanced)
        self.layout.addSpacing(20)

        # --- Section: Connection ---
        self.layout.addWidget(self._section_header("Connection"))
        self.layout.addSpacing(5)
        sub_conn = QLabel(
            "Shared metadata describing this connection. Applied to both peer blocks."
        )
        sub_conn.setWordWrap(True)
        sub_conn.setStyleSheet(f"font-size: 12px; color: {colors.TEXT_SECONDARY};")
        self.layout.addWidget(sub_conn)
        self.layout.addSpacing(5)

        self._conn_name = FloatingLabelLineEdit("Connection Name")
        self._conn_name.setFixedWidth(600)
        self.layout.addWidget(self._conn_name)
        self.layout.addSpacing(8)

        self._purpose = FloatingLabelLineEdit("Purpose")
        self._purpose.setFixedWidth(600)
        self.layout.addWidget(self._purpose)
        self.layout.addSpacing(8)

        self._environment = FloatingLabelComboBox("Environment")
        self._environment.setFixedWidth(600)
        for env in ["", "production", "staging", "development", "test"]:
            self._environment.addItem(env)
        self._environment.setCurrentIndex(0)
        self.layout.addWidget(self._environment)
        self.layout.addSpacing(8)

        self._bandwidth_class = FloatingLabelComboBox("Bandwidth Class")
        self._bandwidth_class.setFixedWidth(600)
        for bw in ["", "low", "medium", "high", "critical"]:
            self._bandwidth_class.addItem(bw)
        self._bandwidth_class.setCurrentIndex(0)
        self.layout.addWidget(self._bandwidth_class)

        self.layout.addStretch()

    def _section_header(self, text: str) -> QLabel:
        """Create a section header label."""
        label = QLabel(text)
        label.setStyleSheet(
            f"font-weight: bold; font-size: 16px; color: {colors.TEXT_PRIMARY};"
        )
        return label

    def _toggle_peer1_advanced(self):
        """Toggle peer 1 advanced options visibility."""
        visible = not self._peer1_advanced.isVisible()
        self._peer1_advanced.setVisible(visible)
        self._peer1_advanced_toggle.setText(
            "▼ Advanced options" if visible else "▶ Advanced options"
        )

    def _toggle_peer2_advanced(self):
        """Toggle peer 2 advanced options visibility."""
        visible = not self._peer2_advanced.isVisible()
        self._peer2_advanced.setVisible(visible)
        self._peer2_advanced_toggle.setText(
            "▼ Advanced options" if visible else "▶ Advanced options"
        )


    def _peer1_machine_selected(self, value):
        self._selected_peer1_machine = value
        self.peer1_name = value.get('name', '')
        self._peer1_allowed_ips.setText(f"{value.get('ipaddress', '').rstrip('/24')}/32")
        self._peer1_listen_port.setText(f":{str(value.get('listen_port', 51820))}")
        self._conn_name.setText(f"{self.peer1_name}_{self.peer2_name}")
        self._peer1_endpoint.setFocus()

    def _peer2_machine_selected(self, value):
        self._selected_peer2_machine = value
        self.peer2_name = value.get('name', '')
        self._peer2_allowed_ips.setText(f"{value.get('ipaddress', '').rstrip('/24')}/32")
        self._peer2_endpoint.setFocus()
        self._peer2_listen_port.setText(f":{str(value.get('listen_port', 51820))}")
        self._conn_name.setText(f"{self.peer1_name}_{self.peer2_name}")
        self._peer2_endpoint.setFocus()

    def showEvent(self, event):
        """Override showEvent to load data when dialog is shown."""
        super().showEvent(event)
        self.clear_error()
        if self._should_reset_on_show:
            self._peer1_machine.setFocus()
            self._reset_form()
        else:
            # Reset flag for next time dialog is opened
            self._peer1_endpoint.setFocus()
            self._should_reset_on_show = True

    def set_peer1_data(self, peer1_data: dict):
        """
        Pre-populate Peer 1 fields with machine data.

        Args:
            peer1_data: Dictionary with keys:
                - name: Machine name
                - server_aid: Machine AID
                - credential_said: Interface credential SAID
                - ipaddress: IP address for allowed IPs
                - listen_port: Listen port
                - tags: List of tags
        """
        # Skip reset on next show to preserve pre-populated data
        self._should_reset_on_show = False

        # Set the selected value in the autocomplete
        self._peer1_machine.set_selected_value(peer1_data)

        # Set the machine name in the autocomplete text field
        machine_name = peer1_data.get('name', '')
        self._peer1_machine.setText(machine_name)

        # Trigger the selection handler to populate other fields
        self._peer1_machine_selected(peer1_data)

    def _reset_form(self):
        """Reset form fields to default values."""
        self._peer1_machine.clear()
        self._peer2_machine.clear()
        self._peer1_allowed_ips.clear()
        self._peer1_endpoint.clear()
        self._peer1_keepalive.clear()
        self._peer1_advanced.setVisible(False)
        self._peer1_advanced_toggle.setText("▶ Advanced options")
        self._peer2_allowed_ips.clear()
        self._peer2_endpoint.clear()
        self._peer2_keepalive.clear()
        self._peer2_peer_name.clear()
        self._peer2_advanced_toggle.setText("▶ Advanced options")
        self._conn_name.clear()
        self._purpose.clear()
        self._environment.setCurrentIndex(0)
        self._bandwidth_class.setCurrentIndex(0)
        self.issue_button.setEnabled(True)
        self.issue_button.setText("Connect Devices")

    def _validate_form(self) -> bool:
        """
        Validate form fields.

        Returns:
            bool: True if validation passes, False otherwise
        """
        # Validate peer 1 machine selection
        if not self._selected_peer1_machine:
            self.show_error("Please select a machine for Peer 1.")
            return False
        peer1_said = self._selected_peer1_machine.get("credential_said")

        # Validate peer 2 machine selection
        if not self._selected_peer2_machine:
            self.show_error("Please select a machine for Peer 2.")
            return False
        peer2_said = self._selected_peer2_machine.get("credential_said")

        # Check that the same machine isn't selected for both peers
        if peer1_said == peer2_said:
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

    @qasync.asyncSlot()
    async def _on_issue_clicked(self):
        """Handle Connect Devices button click."""
        if not self._validate_form():
            return

        self.issue_button.setEnabled(False)
        self.issue_button.setText("Connecting…")
        self.clear_error()

        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None

        if settings is None:
            raise ValueError("Keriguard settings not found")

        hby = self.app.vault.hby
        hab = hby.habs[settings.issuer_aid]

        auth_dialog = WitnessAuthenticationDialog(
            app=self.app,
            hab=hab,
            witness_ids=hab.kever.wits,
            auth_only=True,
            signals=self.app.vault.signals,
            parent=self
        )
        auth_dialog.open()
        return

    @qasync.asyncSlot()
    async def _on_auth_codes_entered(self, data: dict):
        """
        Handles the processing and issuance of connection credentials. This asynchronous
        slot processes the entered authentication codes, validates the associated data,
        and issues a connection credential based on the provided configurations
        and settings.

        params:
            data (dict): A dictionary containing entered authentication codes mapped to
             witness
        """
        self.app.vault.signals.auth_codes_entered.disconnect(self._on_auth_codes_entered)
        try:
            kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
            settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
            essr = self.app.vault.plugin_state.get("keriguard", {}).get("essr")

            if settings is None:
                raise ValueError("Keriguard settings not found")

            hby = self.app.vault.hby
            rgy = self.app.vault.rgy
            hab = hby.habs[settings.issuer_aid]

            codes = data.get('codes', [])
            logger.info(f"Received {len(codes)} auth codes from WitnessAuthenticationDialog")

            # Get credential SAIDs from selected machines
            iface1_said = self._selected_peer1_machine.get("credential_said")
            if not iface1_said:
                raise ValueError("Peer 1 machine has no credential selected")
            iface2_said = self._selected_peer2_machine.get("credential_said")
            if not iface2_said:
                raise ValueError("Peer 2 machine has no credential selected")

            def _parse_allowed_ips(text: str) -> list:
                return [s.strip() for s in text.split(",") if s.strip()]

            peer1_config: dict = {
                "allowedIps": _parse_allowed_ips(self._peer1_allowed_ips.text())
            }
            if ep := self._peer1_endpoint.text().strip():
                port = str(self._selected_peer1_machine.get('listen_port', 51820))
                peer1_config["endpoint"] = f"{ep}:{port}"
            if ka := self._peer1_keepalive.text().strip():
                peer1_config["persistentKeepalive"] = int(ka)

            peer2_config: dict = {
                "allowedIps": _parse_allowed_ips(self._peer2_allowed_ips.text())
            }
            if ep := self._peer2_endpoint.text().strip():
                port = str(self._selected_peer2_machine.get('listen_port', 51820))
                peer2_config["endpoint"] = f"{ep}:{port}"
            if ka := self._peer2_keepalive.text().strip():
                peer2_config["persistentKeepalive"] = int(ka)

            conn_meta: dict = {"connectionName": self._conn_name.text().strip()}
            if purpose := self._purpose.text().strip():
                conn_meta["purpose"] = purpose
            if env := self._environment.currentText():
                conn_meta["environment"] = env
            if bw := self._bandwidth_class.currentText():
                conn_meta["bandwidthClass"] = bw

            issuer = Issuer(hby=hby, hab=hab, rgy=rgy)

            auths = {}
            if codes:
                code_time = helping.nowIso8601()
                for arg in codes:
                    wit, code = arg.split(":")
                    auths[wit] = f"{code}#{code_time}"

            creder = await issue_connection_credential_by_saids(
                issuer=issuer,
                iface1_said=iface1_said,
                peer1_config=peer1_config,
                iface2_said=iface2_said,
                peer2_config=peer2_config,
                conn_meta=conn_meta,
                auths=auths,
            )


            # Connection credentials have no single recipient AID in attrib;
            # use the first peer's interface credential recipient as the grant addressee.
            iface1_creder, *_ = rgy.reger.cloneCred(said=iface1_said)
            recipient_aid = iface1_creder.attrib.get("i")
            grant = issuer.grant(creder.said, recipient_aid)
            grant_bytes = bytes(grant)

            publish_mode = settings.publish_mode if settings else "registrar"

            if publish_mode == "healthKERI" and essr:
                await push_credential_via_essr(grant_bytes, essr, creder.said)
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

            logger.info(f"Connection credential issued successfully. SAID: {creder.said}")

            # Emit signal with credential SAID
            self.connection_issued.emit(creder.said)

            # Close dialog on success
            self.close()

        except Exception as exc:
            logger.exception(f"IssueConnectionCredentialDialog: issuance failed: {exc}")
            self.show_error(f"Issuance failed: {exc}")
            self.issue_button.setEnabled(True)
            self.issue_button.setText("Connect")
