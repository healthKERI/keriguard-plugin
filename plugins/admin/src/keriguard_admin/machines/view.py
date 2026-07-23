# -*- encoding: utf-8 -*-
"""
locksmith.ui.vault.healthKERI.machines.view module

Dialog for viewing healthKERI machine details.
"""
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import qasync
from PySide6.QtGui import QIcon, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QFrame
)
from keri import help
from keri.app import connecting
from keri.help import helping
from keri.peer import exchanging
from keriguard.core.kering import Issuer
from locksmith.ui import colors
from locksmith.ui.toolkit.widgets import (
    LocksmithInvertedButton
)
from locksmith.ui.toolkit.widgets.buttons import LocksmithCopyButton, LocksmithButton
from locksmith.ui.toolkit.widgets.dialogs import LocksmithDialog
from locksmith.ui.toolkit.widgets.fields import LocksmithLineEdit
from locksmith.ui.toolkit.widgets.labels import build_tag
from locksmith.ui.vault.healthKERI.core import remoting
from locksmith.ui.vault.identifiers.authenticate import WitnessAuthenticationDialog
from qasync import asyncSlot

from keriguard_admin.core.remoting import push_credential_via_essr, _ensure_issuer_watched, \
    push_credential_to_registrar, push_introduction_to_registrar

logger = help.ogler.getLogger(__name__)


class ViewKERIGuardDeviceDialog(LocksmithDialog):
    """Dialog for viewing healthKERI machine details."""

    def __init__(
        self,
        icon_path: str,
        app,
        machine: dict[str, Any],
        on_refresh: Callable[[], None] | None = None,
        parent=None
    ):
        """
        Initialize the ViewMachineDialog.

        Args:
            icon_path: Path to the machine icon
            app: Application instance
            machine: Machine data dict from the API
            on_refresh: Callback to refresh the machine list after updates
            parent: Parent widget (typically VaultPage)
        """
        self.app = app
        self.machine = machine
        self.on_refresh = on_refresh

        self.name = machine.get('name', '')
        self.aid = machine.get('aid', '')
        self.address = machine.get('address', '')
        self.server_aid = machine.get('server_aid', '')
        self.machine_type = machine.get('type', '')
        self.status = machine.get('status', '')
        self.expiration = machine.get('expiration', 0)
        self.tags = machine.get('tags', [])

        # Create title content FIRST (before super().__init__)
        title_content_widget = QWidget()
        title_content = QHBoxLayout()
        icon = QIcon(icon_path)
        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(32, 32))
        icon_label.setFixedSize(32, 32)
        title_content.addWidget(icon_label)

        title_label = QLabel(f"  {self.name}")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_content.addWidget(title_label)
        title_content_widget.setLayout(title_content)

        # Create content widget
        content_widget = QWidget()
        content_widget.setStyleSheet(f"background-color: {colors.BACKGROUND_CONTENT};")
        self.main_layout = QVBoxLayout(content_widget)
        self.main_layout.setContentsMargins(0, 10, 0, 0)
        self.main_layout.setSpacing(15)

        # Create button row
        button_row = QHBoxLayout()
        self.close_button = LocksmithInvertedButton("Close")
        button_row.addWidget(self.close_button)

        # Initialize parent dialog EARLY
        super().__init__(
            parent=parent,
            title_content=title_content_widget,
            show_close_button=True,
            content=content_widget,
            buttons=button_row,
            show_overlay=False
        )

        self.setFixedSize(580, 565)

        # NOW build sections (after super().__init__ has been called)
        self._build_error_section(self.main_layout)
        self._build_aid_section(self.main_layout)
        self._build_machine_info_section(self.main_layout)
        self._build_tags_section(self.main_layout)
        self._build_issue_ip_section(self.main_layout)

        self.main_layout.addStretch()

        self.close_button.clicked.connect(self.close)
        self.app.vault.signals.auth_codes_entered.connect(self._on_auth_codes_entered)


    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_error_section(self, layout: QVBoxLayout):
        """Build the error message display section."""
        self._error_frame = QFrame()
        self._error_frame.setStyleSheet(f"""
            QFrame {{
                background-color: #fee;
                border: 1px solid #fcc;
                border-radius: 6px;
                padding: 12px;
            }}
        """)
        self._error_frame.setVisible(False)

        error_layout = QVBoxLayout()
        error_layout.setContentsMargins(0, 0, 0, 0)
        error_layout.setSpacing(4)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #c00; font-size: 13px; font-weight: 500; border: none;")
        self._error_label.setWordWrap(True)
        error_layout.addWidget(self._error_label)

        self._error_frame.setLayout(error_layout)
        layout.addWidget(self._error_frame)

    def _build_aid_section(self, layout: QVBoxLayout):
        """Build the AID section with copy button."""
        if not self.aid:
            return

        aid_label_row = QHBoxLayout()
        aid_label_row.setSpacing(5)
        aid_label = QLabel("Machine AID")
        aid_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        aid_label_row.addWidget(aid_label)

        self.copy_aid_button = LocksmithCopyButton(copy_content=self.aid, icon_size=24)
        self.copy_aid_button.setFixedHeight(36)
        aid_label_row.addWidget(self.copy_aid_button)
        aid_label_row.addStretch()
        layout.addLayout(aid_label_row)

        self.aid_field = LocksmithLineEdit("Machine AID")
        self.aid_field.setText(self.aid)
        self.aid_field.setReadOnly(True)
        self.aid_field.setCursorPosition(0)
        self.aid_field.setMinimumWidth(480)
        layout.addWidget(self.aid_field)

    def _build_machine_info_section(self, layout: QVBoxLayout):
        """Build the machine information section as a bordered, rounded QFrame."""
        info_frame = QFrame()
        info_frame.setStyleSheet(f"""
            QFrame {{
                border: 2px solid {colors.BORDER};
                border-radius: 8px;
                background-color: {colors.BACKGROUND_CONTENT};
            }}
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(10, 10, 10, 10)
        info_layout.setSpacing(10)

        # Title
        info_title = QLabel("Machine Information")
        info_title.setStyleSheet("font-weight: bold; font-size: 14px; border: none;")
        info_layout.addWidget(info_title)

        # Name
        name_row = QHBoxLayout()
        name_label = QLabel("Name:")
        name_label.setStyleSheet("font-weight: 500; font-size: 13px; border: none;")
        name_row.addWidget(name_label)
        name_value = QLabel(self.name or "N/A")
        name_value.setStyleSheet("font-size: 13px; border: none;")
        name_row.addWidget(name_value)
        name_row.addStretch()
        info_layout.addLayout(name_row)

        # Address
        name_row = QHBoxLayout()
        name_label = QLabel("Address:")
        name_label.setStyleSheet("font-weight: 500; font-size: 13px; border: none;")
        name_row.addWidget(name_label)
        name_value = QLabel(self.address or "  -  ")
        name_value.setStyleSheet("font-size: 13px; border: none;")
        name_row.addWidget(name_value)
        name_row.addStretch()
        info_layout.addLayout(name_row)

        # Status
        status_row = QHBoxLayout()
        status_row.setSpacing(1)
        status_label = QLabel("Status:")
        status_label.setStyleSheet("font-weight: 500; font-size: 13px; border: none;")
        status_row.addWidget(status_label)
        status_row.addSpacing(10)

        # Format status display with color
        status_text, status_color = self._get_status_display(self.status)

        # Create colored dot
        dot_label = QLabel("●")
        dot_label.setStyleSheet(f"font-size: 16px; color: {status_color}; border: none;")
        status_row.addWidget(dot_label)

        # Create status text with color
        status_value = QLabel(status_text)
        status_value.setStyleSheet(f"font-size: 13px; color: {status_color}; border: none; margin-left: 4px;")
        status_row.addWidget(status_value)
        status_row.addStretch()
        info_layout.addLayout(status_row)

        # Expiration
        expiration_row = QHBoxLayout()
        expiration_label = QLabel("Expiration:")
        expiration_label.setStyleSheet("font-weight: 500; font-size: 13px; border: none;")
        expiration_row.addWidget(expiration_label)
        expiration_text = datetime.fromtimestamp(self.expiration).strftime("%Y-%m-%d %I:%M %p") if self.expiration else "Never"
        expiration_value = QLabel(expiration_text)
        expiration_value.setStyleSheet("font-size: 13px; border: none;")
        expiration_row.addWidget(expiration_value)
        expiration_row.addStretch()
        info_layout.addLayout(expiration_row)

        layout.addWidget(info_frame)

    def _build_tags_section(self, layout: QVBoxLayout):
        """Build the tags section with wrapping support."""
        tags_frame = QFrame()
        tags_frame.setStyleSheet(f"""
            QFrame {{
                border: 2px solid {colors.BORDER};
                border-radius: 8px;
                background-color: {colors.BACKGROUND_CONTENT};
            }}
        """)
        tags_layout = QVBoxLayout(tags_frame)
        tags_layout.setContentsMargins(10, 10, 10, 10)
        tags_layout.setSpacing(10)

        # Title
        tags_title = QLabel("Tags")
        tags_title.setStyleSheet("font-weight: bold; font-size: 14px; border: none;")
        tags_layout.addWidget(tags_title)

        # Tags container with wrapping
        tags_container = QWidget()
        tags_container.setStyleSheet("background: transparent; border: none;")
        tags_container_layout = QVBoxLayout(tags_container)
        tags_container_layout.setContentsMargins(0, 0, 0, 0)
        tags_container_layout.setSpacing(5)

        # Create rows of tags that wrap
        current_row = None
        current_row_width = 0
        max_width = 520  # Dialog width (580) - margins and padding

        for tag_text in self.tags:
            tag_widget = build_tag(tag_text)
            tag_width = tag_widget.sizeHint().width()

            # Start a new row if needed
            if current_row is None or current_row_width + tag_width + 10 > max_width:
                current_row = QHBoxLayout()
                current_row.setContentsMargins(0, 0, 0, 0)
                current_row.setSpacing(5)
                current_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                tags_container_layout.addLayout(current_row)
                current_row_width = 0

            current_row.addWidget(tag_widget)
            current_row_width += tag_width + 10

        tags_layout.addWidget(tags_container)
        layout.addWidget(tags_frame)

    def _build_issue_ip_section(self, layout: QVBoxLayout):
        """Build the issue IP address section (only visible when status is live and no address)."""
        # Only show this section if status is 'live' and address is empty
        if self.status != 'live' or self.address:
            return

        self.setFixedSize(580, 725)

        issue_frame = QFrame()
        issue_frame.setStyleSheet(f"""
            QFrame {{
                border: 2px solid {colors.WARNING_YELLOW};
                border-radius: 8px;
                background-color: #fffbf0;
            }}
        """)
        issue_layout = QVBoxLayout(issue_frame)
        issue_layout.setContentsMargins(15, 15, 15, 15)
        issue_layout.setSpacing(12)

        # Warning icon and title
        title_row = QHBoxLayout()
        title_label = QLabel("⚠ IP Address Required")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {colors.WARNING_YELLOW}; border: none;")
        title_row.addWidget(title_label)
        title_row.addStretch()
        issue_layout.addLayout(title_row)

        # Explanation text
        explanation = QLabel(
            "This device has been registered but does not have an IP address assigned. "
            "Issue an IP address credential to enable network connectivity."
        )
        explanation.setStyleSheet("font-size: 13px; color: #666; border: none;")
        explanation.setWordWrap(True)
        issue_layout.addWidget(explanation)

        # Issue button
        button_row = QHBoxLayout()
        button_row.addStretch()
        self.issue_ip_button = LocksmithButton("Issue IP Address")
        self.issue_ip_button.setFixedWidth(180)
        self.issue_ip_button.clicked.connect(self._on_issue_ip_address)
        button_row.addWidget(self.issue_ip_button)
        button_row.addStretch()
        issue_layout.addLayout(button_row)

        layout.addWidget(issue_frame)


    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _get_status_display(self, status: str) -> tuple[str, str]:
        """
        Get the status text and color for display using the same logic as MachinesListPage.

        Returns:
            tuple[str, str]: (status_text, status_color)
        """
        if status == 'pending_registration':
            if self.expiration < int(time.time()):
                status_text = "Expired"
                status_color = colors.DANGER
            else:
                status_text = "Pending Registration"
                status_color = colors.WARNING_YELLOW
        elif status == 'live':
            if self.address:
                status_text = "Live"
                status_color = colors.SUCCESS_INDICATOR
            else:
                status_text = "Pending Interface"
                status_color = colors.WARNING_YELLOW
        else:
            status_text = "Invalid"
            status_color = colors.DANGER

        return status_text, status_color

    def _display_error(self, error_message: str):
        """Display an error message in the error section."""
        self._error_label.setText(error_message)
        self._error_frame.setVisible(True)

    def _clear_error(self):
        """Clear the error message display."""
        self._error_frame.setVisible(False)
        self._error_label.setText("")

    @asyncSlot()
    async def _on_issue_ip_address(self):
        """Handle the Issue IP Address button click."""
        logger.info(f"Issue IP Address clicked for machine: {self.name}")

        # Clear any existing errors
        self._clear_error()

        # Disable button during processing
        self.issue_ip_button.setEnabled(False)
        self.issue_ip_button.setText("Issuing...")

        try:
            # Get the ESSR client
            essr = self.app.vault.plugin_state.get("healthkeri", {}).get("essr")
            if not essr:
                raise RuntimeError("ESSR client not available. Please ensure you are connected to healthKERI.")

            # Get device_id (server_aid) from machine data
            device_id = self.machine.get('server_aid')
            if not device_id:
                raise ValueError("Machine does not have a server_aid")

            # Make POST request to issue IP address
            logger.info(f"Issuing IP address for device: {device_id}")
            response = await essr.request(
                path=f"/teams/devices/{device_id}/ip",
                method="POST",
                timeout=30,
            )

            # Check response
            if response is None or response.status_code not in (200, 201, 204):
                status = response.status_code if response else "None"
                error_msg = "Failed to issue IP address"

                # Try to extract error message from response
                if response:
                    try:
                        error_data = response.json()
                        if isinstance(error_data, dict) and "error" in error_data:
                            error_msg = error_data["error"]
                        elif isinstance(error_data, dict) and "message" in error_data:
                            error_msg = error_data["message"]
                    except Exception:
                        pass

                raise RuntimeError(f"{error_msg} (HTTP {status})")

            # Parse response to get the new IP address
            try:
                response_data = response.json()
                new_address = response_data.get("ip", "")
                logger.info(f"IP address issued successfully: {new_address}")

                # Update machine data
                self.machine["address"] = new_address
                self.address = new_address

                team_server = await remoting.sync_team_server(self.app, self.aid)
                if not team_server.get("success", False):
                    raise RuntimeError("Failed to sync team server")

                await self._issue_interface_credential()

                # Refresh the view to show updated data
                await self._refresh_view()

                # Call the on_refresh callback if provided to update the list
                if self.on_refresh:
                    self.on_refresh()

            except Exception as e:
                logger.warning(f"IP issued but failed to parse response: {e}")
                # Still refresh the view even if we couldn't parse the response
                await self._refresh_view()
                if self.on_refresh:
                    self.on_refresh()

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Failed to issue IP address: {e}")
            self._display_error(str(e))
            # Re-enable button on error
            self.issue_ip_button.setEnabled(True)
            self.issue_ip_button.setText("Issue IP Address")

    async def _refresh_view(self):
        """Refresh the dialog view with updated machine data."""
        # Get the content widget and layout

        # Remove all widgets from layout
        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Rebuild sections with updated data
        self._build_error_section(self.main_layout)
        self._build_aid_section(self.main_layout)
        self._build_machine_info_section(self.main_layout)
        self._build_tags_section(self.main_layout)
        self._build_issue_ip_section(self.main_layout)
        self.main_layout.addStretch()

    async def _issue_interface_credential(self):
        hby = self.app.vault.hby
        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None

        issuer_aid = settings.issuer_aid
        hab = hby.habs.get(issuer_aid)

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
        Handle auth codes entered from WitnessAuthenticationDialog.

        Args:
            data: Dictionary containing 'codes' key with list of "witness_id:passcode" strings
        """
        try:
            hby = self.app.vault.hby
            rgy = self.app.vault.rgy
            team = self.app.vault.plugin_state.get("keriguard", {}).get("team")
            kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
            settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None

            issuer_aid = settings.issuer_aid
            hab = hby.habs.get(issuer_aid)

            codes = data.get('codes', [])
            logger.info(f"Received {len(codes)} auth codes from WitnessAuthenticationDialog")

            recipient_aid = self.server_aid

            org = connecting.Organizer(hby=hby)
            contact = org.get(recipient_aid)
            recipient_alias = contact.get("alias", "") if contact else ""
            recipient_oobi = contact.get("oobi", "") if contact else ""

            issuer = Issuer(hby=hby, hab=hab, rgy=rgy)

            interface_config = {
                "listenPort": 51820,
                "address": [f"{self.address.strip()}/32"],
            }

            interface_metadata = {
                "interfaceName": "wg0",
                "interfaceDescription": f"{team.name} KERIGuard managed interface",
                "environment": "production"  # TODO: Make this configurable
            }

            essr = self.app.vault.plugin_state.get("healthkeri", {}).get("essr")

            auths = {}
            if codes:
                code_time = helping.nowIso8601()
                for arg in codes:
                    wit, code = arg.split(":")
                    auths[wit] = f"{code}#{code_time}"

            creder = await issuer.issue_interface_credential(
                recipient=recipient_aid,
                registry_name=settings.registry_name if settings else "",  # type: ignore
                interface=interface_config,
                interface_metadata=interface_metadata,
                auths=auths,
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

            publish_mode = settings.publish_mode if settings else "registrar"  # type: ignore

            if publish_mode == "serviceprovider" and essr:
                await remoting.send_key_state_update(self.app, hab.pre)
                await push_credential_via_essr(grant_bytes, essr, creder, introduction_bytes)
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
