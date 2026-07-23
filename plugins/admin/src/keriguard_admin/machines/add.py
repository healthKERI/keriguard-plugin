# -*- encoding: utf-8 -*-
"""
locksmith.ui.vault.healthKERI.servers.add module

Dialog for adding new servers to healthKERI.
"""
import re
import yaml
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QApplication, QFileDialog
from keri import kering
from keri import help
from locksmith.ui import colors
from locksmith.ui.toolkit.widgets import (
    LocksmithDialog,
    LocksmithButton,
    LocksmithInvertedButton, LocksmithIconButton
)
from locksmith.ui.toolkit.widgets.fields import FloatingLabelLineEdit
from locksmith.ui.vault.healthKERI.core import remoting
from qasync import asyncSlot

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class AddKERIGuardDeviceDialog(LocksmithDialog):
    """Dialog for adding new machines to healthKERI.

    Allows users to add machines to the healthKERI network.
    """

    machine_added = Signal(str)

    def __init__(
            self,
            app: "LocksmithApplication",
            parent: "VaultPage | None" = None
    ):
        """
        Initialize the AddMachineDialog.

        Args:
            app: Application instance
            parent: Parent widget (VaultPage)
        """
        self.app = app
        self._is_saving = False
        self._identifiers_data = {}  # Cache for identifier data
        self._generated_auth_code = ""
        self._machine_name = ""
        self._machine_tags = []

        # Create content widget
        content_widget = QWidget()
        content_widget.setStyleSheet(f"background-color: {colors.BACKGROUND_CONTENT};")
        self.layout = QVBoxLayout(content_widget)
        self.layout.setContentsMargins(20, 20, 20, 20)
        self.layout.setSpacing(15)

        # Instructions section
        instructions_label = QLabel("Add New Machine")
        instructions_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.layout.addWidget(instructions_label)

        description = QLabel(
            "Generate an authentication key for connecting a new machine to your healthKERI network."
        )
        description.setStyleSheet(f"color: {colors.TEXT_SECONDARY}; font-size: 13px;")
        description.setWordWrap(True)
        self.layout.addWidget(description)

        self.layout.addSpacing(10)

        # Name Input Field
        name_label = QLabel("Name")
        name_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.layout.addWidget(name_label)

        self.name_field = FloatingLabelLineEdit(label_text="Name")
        self.name_field.setFixedWidth(400)
        self.name_field.setPlaceholderText("e.g., Production Machine")
        self.layout.addWidget(self.name_field)

        self.layout.addSpacing(10)

        # Tags Input Field
        tags_label = QLabel("Tags")
        tags_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.layout.addWidget(tags_label)

        self.labels_field = FloatingLabelLineEdit(label_text="Tags")
        self.labels_field.setFixedWidth(400)
        self.labels_field.setPlaceholderText("e.g. Sentinel, OAuth")
        self.layout.addWidget(self.labels_field)

        self.layout.addSpacing(10)

        subtitle = QLabel(
            "You need to authorize your new machine with a auth key.  Click Generate to create an auth key.  Be sure to copy the key and save it, it will not be displayed again.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.layout.addWidget(subtitle)

        self.layout.addSpacing(10)

        # Generate Button
        generate_button_row = QHBoxLayout()
        generate_button_row.addStretch()
        self.generate_button = LocksmithButton("Generate")
        self.generate_button.clicked.connect(self._generate_auth_key)
        generate_button_row.addWidget(self.generate_button)
        self.layout.addLayout(generate_button_row)

        self.layout.addSpacing(15)

        # Auth Code Display Section (initially hidden)
        auth_code_section_label = QLabel("Generated Authentication Key")
        auth_code_section_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        auth_code_section_label.hide()
        self.layout.addWidget(auth_code_section_label)
        self.auth_code_section_label = auth_code_section_label

        # Auth Code Display with Copy Button
        auth_code_row = QHBoxLayout()

        self.machine_auth_code_label = QLabel("")
        self.machine_auth_code_label.setStyleSheet(f"""
            QLabel {{
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 12px;
                font-size: 13px;
                background-color: #f9f9f9;
                color: #000;
                font-family: monospace;
                min-height: 20px;
            }}
        """)
        self.machine_auth_code_label.setWordWrap(True)
        self.machine_auth_code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.machine_auth_code_label.hide()
        auth_code_row.addWidget(self.machine_auth_code_label)

        self.copy_button = LocksmithIconButton(
            icon_path=":/assets/material-icons/content_copy.svg",
            tooltip="Copy authentication key to clipboard",
            icon_size=24
        )
        self.copy_button.setFixedHeight(48)
        self.copy_button.setFixedWidth(48)
        self.copy_button.clicked.connect(self._copy_to_clipboard)
        self.copy_button.hide()
        auth_code_row.addWidget(self.copy_button)

        self.layout.addLayout(auth_code_row)

        self.layout.addSpacing(15)

        # Generate configuration button (shown after auth code is generated)
        self.generate_config_button = LocksmithButton("Generate configuration")
        self.generate_config_button.setFixedWidth(225)
        self.generate_config_button.clicked.connect(self._generate_config_file)
        self.generate_config_button.hide()  # Initially hidden
        self.layout.addWidget(self.generate_config_button)

        self.layout.addSpacing(20)

        # Command Instructions (initially hidden)
        self.machine_instructions = QLabel("Use this command to start your machine:")
        self.machine_instructions.setStyleSheet(f"color: {colors.TEXT_SECONDARY}; font-size: 13px; margin-top: 10px;")
        self.machine_instructions.hide()
        self.layout.addWidget(self.machine_instructions)

        # Command display with copy button
        command_row = QHBoxLayout()

        self.sample_command = QLabel("")
        self.sample_command.setStyleSheet(f"""
            QLabel {{
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 12px;
                font-size: 13px;
                background-color: #f9f9f9;
                color: #000;
                font-family: monospace;
                margin-top: 5px;
            }}
        """)
        self.sample_command.setWordWrap(True)
        self.sample_command.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sample_command.hide()
        command_row.addWidget(self.sample_command)

        self.copy_command_button = LocksmithIconButton(
            icon_path=":/assets/material-icons/content_copy.svg",
            tooltip="Copy command to clipboard",
            icon_size=24
        )
        self.copy_command_button.setFixedHeight(48)
        self.copy_command_button.setFixedWidth(48)
        self.copy_command_button.clicked.connect(self._copy_command_to_clipboard)
        self.copy_command_button.hide()
        command_row.addWidget(self.copy_command_button)

        self.layout.addLayout(command_row)

        self.layout.addStretch()

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.cancel_button = LocksmithInvertedButton("Cancel")
        button_row.addWidget(self.cancel_button)

        button_row.addSpacing(10)

        self.save_button = LocksmithButton("Finished")
        button_row.addWidget(self.save_button)

        # Initialize parent dialog
        super().__init__(
            parent=parent,
            title="Add Machine",
            title_icon=":/assets/material-icons/hive.svg",
            content=content_widget,
            buttons=button_row,
            show_overlay=False
        )

        self.setFixedSize(600, 850)

        # Connect signals
        self.cancel_button.clicked.connect(self.close)
        self.save_button.clicked.connect(self._on_finished)

        logger.info("AddMachineDialog initialized")

    def _copy_to_clipboard(self):
        """Copy the generated authentication key to clipboard."""
        try:
            auth_code = self.machine_auth_code_label.text()
            if not auth_code:
                self.show_error("No authentication key to copy")
                return

            clipboard = QApplication.clipboard()
            clipboard.setText(auth_code)

            # Change icon to green checkmark
            self.copy_button.setIcon(QIcon(":/assets/material-icons/green_check_circle.svg"))

            # Revert to copy icon after 3.5 seconds
            QTimer.singleShot(3500, lambda: self.copy_button.setIcon(
                QIcon(":/assets/material-icons/content_copy.svg")
            ))

        except Exception as e:
            logger.exception(f"Error copying to clipboard: {e}")
            self.show_error(f"Failed to copy: {str(e)}")

    def _copy_command_to_clipboard(self):
        """Copy the sample command to clipboard."""
        try:
            command = self.sample_command.text()
            if not command:
                self.show_error("No command to copy")
                return

            clipboard = QApplication.clipboard()
            clipboard.setText(command)

            # Change icon to green checkmark
            self.copy_command_button.setIcon(QIcon(":/assets/material-icons/green_check_circle.svg"))

            # Revert to copy icon after 3.5 seconds
            QTimer.singleShot(3500, lambda: self.copy_command_button.setIcon(
                QIcon(":/assets/material-icons/content_copy.svg")
            ))

        except Exception as e:
            logger.exception(f"Error copying command to clipboard: {e}")
            self.show_error(f"Failed to copy: {str(e)}")

    def _generate_config_file(self) -> None:
        """Open save dialog and generate YAML configuration file."""
        # Open file save dialog
        default_filename = "sentinel-config.yaml"
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Sentinel Configuration",
            default_filename,
            "YAML files (*.yaml *.yml);;All files (*)"
        )

        if not file_path:
            # User cancelled
            return

        try:
            # Get KERIGuard settings
            kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
            settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None

            if not settings or not settings.issuer_aid:
                self.show_error("KERIGuard settings not configured. Please complete setup first.")
                return

            # Get issuer hab and derive OOBI
            hby = self.app.vault.hby
            issuer_aid = settings.issuer_aid
            hab = hby.habs.get(issuer_aid)

            issuer_oobi = None
            if hab:
                kever = hby.kevers.get(hab.pre)
                if kever and kever.wits:
                    # Try to find a witness endpoint
                    for wit in kever.wits:
                        for scheme in (kering.Schemes.https, kering.Schemes.http):
                            loc = hby.db.locs.get(keys=(wit, scheme))
                            if loc and getattr(loc, 'url', None):
                                issuer_oobi = f"{loc.url.rstrip('/')}/oobi/{hab.pre}/witness"
                                break
                        if issuer_oobi:
                            break

            if not issuer_oobi:
                logger.warning("Could not derive issuer OOBI from witness endpoints")
                # Continue anyway - OOBI might not be required in all modes

            # Determine local mode based on publish_mode
            # local: false for SaaS (serviceprovider), true for registrar (opensource)
            local_mode = settings.publish_mode != "serviceprovider"

            # Build YAML configuration structure
            config = {
                'local': local_mode,
                'server': {
                    'auth_key': self._generated_auth_code
                },
                'issuer': {
                    'aid': issuer_aid,
                }
            }

            # Add OOBI if available
            if issuer_oobi:
                config['issuer']['oobi'] = issuer_oobi

            # Write YAML to file
            with open(file_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            logger.info(f"Sentinel configuration saved to: {file_path}")

            # Now show the command section with the config file path
            self.machine_instructions.show()
            self.sample_command.setText(f"kg guardian up --config {file_path}")
            self.sample_command.show()
            self.copy_command_button.show()

            # Hide the generate config button (task complete)
            self.generate_config_button.hide()

        except Exception as e:
            logger.error(f"Failed to generate configuration file: {e}")
            self.show_error(f"Failed to save configuration: {str(e)}")

    @asyncSlot()
    async def _generate_auth_key(self):
        """Handle Generate button click to create authentication key."""
        # Clear any previous errors
        self.clear_error()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Close")

        # Validate name field
        name = self.name_field.text().strip()
        if not name:
            self.name_field.setProperty("error", True)
            self.name_field.style().unpolish(self.name_field)
            self.name_field.style().polish(self.name_field)
            self.show_error("Please enter a machine name")
            self.cancel_button.setEnabled(True)
            return

        # Clear error styling if previously set
        self.name_field.setProperty("error", False)
        self.name_field.style().unpolish(self.name_field)
        self.name_field.style().polish(self.name_field)

        # Validate machine type field
        tags_text = self.labels_field.text().strip()
        tags = []
        if tags_text:
            tags = [w for w in re.split(r"[,\s]+", tags_text.strip()) if w]


        # Clear error styling if previously set
        self.labels_field.setProperty("error", False)
        self.labels_field.style().unpolish(self.labels_field)
        self.labels_field.style().polish(self.labels_field)

        # Disable generate button during operation
        self.generate_button.setEnabled(False)
        self.generate_button.setText("Generating...")

        try:
            # Call API to generate auth code
            response = await remoting.generate_auth_code(
                self.app,
                name,
                tags=tags,
                machine_type="keriguard"
            )

            if response and response.get('success'):
                auth_code = response.get('code')
                logger.info(f"Authentication key generated successfully for {name}")

                # Display the generated code
                self.machine_auth_code_label.setText(auth_code)
                self.machine_auth_code_label.show()
                self.auth_code_section_label.show()
                self.copy_button.show()

                # Show the generate config button instead of immediately showing command
                self.generate_config_button.show()

                # Store auth code and name for later use in config generation
                self._generated_auth_code = auth_code
                self._machine_name = name
                self._machine_tags = tags

                self.generate_button.setVisible(False)

            else:
                error_msg = response.get('error', 'Unknown error') if response else 'No response from machine'
                logger.error(f"Failed to generate authentication key: {error_msg}")
                self.show_error(f"Failed to generate key: {error_msg}")
                self.generate_button.setEnabled(True)
                self.generate_button.setText("Generate")

        except Exception as e:
            logger.exception(f"Error generating authentication key: {e}")
            self.show_error(f"Error generating key: {str(e)}")
            self.generate_button.setEnabled(True)
            self.generate_button.setText("Generate")

        finally:
            self.cancel_button.setEnabled(True)

    def _validate_fields(self) -> bool:
        """
        Validate form fields.

        Returns:
            bool: True if validation passes, False otherwise
        """
        # Clear any previous errors
        self.clear_error()

        # Check name
        name = self.name_field.text().strip()
        if not name:
            self.name_field.setProperty("error", True)
            self.name_field.style().unpolish(self.name_field)
            self.name_field.style().polish(self.name_field)
            self.show_error("Please enter a machine name")
            return False

        # Check machine type
        machine_type = self.labels_field.text().strip()
        if not machine_type:
            self.labels_field.setProperty("error", True)
            self.labels_field.style().unpolish(self.labels_field)
            self.labels_field.style().polish(self.labels_field)
            self.show_error("Please enter a machine type")
            return False

        # Check that auth key has been generated
        auth_code = self.machine_auth_code_label.text()
        if not auth_code:
            self.show_error("Please generate an authentication key first")
            return False

        return True

    def _reset_button(self):
        """Reset save button to enabled state."""
        self._is_saving = False
        self.save_button.setEnabled(True)
        self.save_button.setText("Save")

    def _on_finished(self):
        auth_code = self.machine_auth_code_label.text()
        self.machine_added.emit(auth_code)
        self.close()

    def showEvent(self, event):
        """Override showEvent to set focus on the text field when dialog is shown."""
        super().showEvent(event)
        self.name_field.setFocus()