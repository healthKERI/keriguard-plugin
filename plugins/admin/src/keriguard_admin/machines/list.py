# -*- encoding: utf-8 -*-
"""
locksmith.ui.vault.healthKERI.machines.list module

Machines list page for healthKERI machine management.
"""
import time
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Any

import qasync
from PySide6.QtGui import QPalette, QColor, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from keri import help
from keri.core.serdering import SerderACDC
from keriguard.core.wireguarding import Schema
from locksmith.ui import colors
from locksmith.ui.toolkit.tables import PaginatedTableWidget
from locksmith.ui.toolkit.widgets.labels import build_tag
from locksmith.ui.vault.healthKERI.core import remoting
from locksmith.ui.vault.healthKERI.machines.delete import DeleteMachineDialog
from locksmith.ui.vault.healthKERI.machines.filter import FilterMachinesDialog

from keriguard_admin.machines.add import AddKERIGuardDeviceDialog
from keriguard_admin.machines.view import ViewKERIGuardDeviceDialog

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class MachinesListPage(QWidget):
    """
    Machines list page for healthKERI machine management.

    Features:
    - Filter dialog to switch between Live and Pending machine views
    - Live: Machines from /account/teams/netmap
    - Pending: Machines from /account/teams/machines
    - Async data loading with pagination
    - Machine-side sorting and filtering
    - View/Remove actions per machine
    """

    def __init__(self, app: "LocksmithApplication", parent: "VaultPage | None" = None):
        super().__init__(parent)

        self.app = app
        self.vault_name = None
        self.parent = parent
        self._machines_cache: dict[str, dict[str, Any]] = {}  # Cache for row actions
        self._credentials: dict[str, SerderACDC] = {}  # Cache for row actions
        self.current_machine_filter = None  # Default to Live view

        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Set background using palette
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors.BACKGROUND_CONTENT))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        # Create the machines table
        self.machine_table = PaginatedTableWidget(
            columns=["Machine Name", "Address", "Tags", "Status", "Expiration", "Actions"],
            column_widths={"Address": 150, "Tags": 175, "Status": 175, "Expiration": 175, "Actions": 50},
            title="Machines",
            icon_path=":/assets/material-icons/devices.svg",
            show_search=True,
            show_add_button=True,
            add_button_text="Add Machine",
            row_actions=["View", "Remove"],
            row_action_icons={
                "View": ":/assets/material-icons/visibility.svg",
                "Remove": ":/assets/material-icons/delete.svg"
            },
            items_per_page=10,
            # Async loading configuration
            column_sort_mapping={
                "Machine Name": "name",
                "Status": "status"
            },
            row_actions_callback=self._get_row_actions,
            transform_func=self._transform_machine_to_row,
            filter_func=self._open_filter_dialog,
            parent=self
        )

        # Connect signals
        self.machine_table.add_clicked.connect(self._on_add_machine)
        self.machine_table.row_action_triggered.connect(self._on_machine_action)
        self.machine_table.row_clicked.connect(self._on_machine_row_clicked)
        self.machine_table.load_requested.connect(self._on_load_requested)
        self.machine_table.load_error.connect(self._on_load_error)

        layout.addWidget(self.machine_table)

    def _open_filter_dialog(self):
        """Open the filter dialog for machines."""
        logger.info("Opening filter dialog")

        # Create dialog
        dialog = FilterMachinesDialog(parent=self.parent)

        # Set current filter state
        dialog.set_current_filter(self.current_machine_filter)

        # Connect to filter applied signal
        dialog.filter_applied.connect(self._on_filter_applied)

        # Open dialog
        dialog.open()

    def _on_filter_applied(self, filter_data: dict):
        """
        Handle filter applied from dialog.

        Args:
            filter_data: Dictionary with filter settings {"machine_type": "live"|"pending"}
        """
        status = filter_data.get("status", None)
        logger.info(f"Filter applied: status={status}")

        # Update current filter state
        self.current_machine_filter = status

        # Reload data with filter
        self.machine_table.request_load()

    def _transform_machine_to_row(self, machine: dict[str, Any]) -> dict[str, Any]:
        """
        Transform machine data from API to table row format.

        Args:
            machine: Raw machine data from API

        Returns:
            Dict with column names as keys
        """
        # Extract machine fields (adjust based on actual API response)
        machine_id = machine.get('id', '')
        aid = machine.get('aid', '')
        name = machine.get('name', '')
        tags = ' '.join(machine.get('tags', []))
        status = machine.get('status', '')
        expiration = machine.get('expiration', 0)
        server_aid = machine.get('server_aid', None)

        creder = self._credentials.get(server_aid, None)
        if creder:
            interface = creder.attrib.get("interface", {})
            address = interface.get("address", "")
            addy = address[0].rstrip("/32")
            machine["address"] = addy
        else:
            machine["address"] = ""

        # Cache full machine object for row actions
        self._machines_cache[machine_id] = machine

        if status == 'pending_registration':
            if expiration < int(time.time()):
                status_text = "Expired"
                status_color = colors.DANGER
            else:
                status_text = "Pending Registration"
                status_color = colors.WARNING_YELLOW
        elif status == 'live':
            if machine["address"]:
                status_text = "Live"
                status_color = colors.SUCCESS_INDICATOR
            else:
                status_text = "Pending Interface"
                status_color = colors.WARNING_YELLOW
        else:
            status_text = "Invalid"
            status_color = colors.DANGER


        expiration_text = datetime.fromtimestamp(expiration).strftime("%Y-%m-%d %I:%M %p") if expiration else "Never"

        row_data = {
            'Machine Name': name,
            'Tags': tags,
            "Address": machine["address"] if machine["address"] else " - ",
            'Tags_func': self.tags_column_widget,
            'Status': status_text,
            'Status_color': status_color,
            'Expiration': expiration_text,
            '_id': machine_id,  # Hidden metadata for actions
            '_aid': aid,
            '_status': status,
            '_server_aid': server_aid,
        }

        return row_data

    def tags_column_widget(self, cell_value):
        from PySide6.QtWidgets import QHBoxLayout

        # Parse tags from the cell_value string
        tags = cell_value.split() if cell_value else []

        # Create container widget
        container = QWidget()
        container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if len(tags) == 0:
            return container

        first_tag_widget = build_tag(tags[0])
        layout.addWidget(first_tag_widget)

        # If there are more tags, add the "+ X" label
        if len(tags) > 1:
            remaining_count = len(tags) - 1
            remaining_label = QLabel(f"+ {remaining_count}")
            remaining_label.setStyleSheet('''
                background: transparent;
                color: #1976D2;
            ''')
            layout.addWidget(remaining_label)

        # Add stretch to keep everything left-aligned
        layout.addStretch()

        return container

    @qasync.asyncSlot(dict)
    async def _on_load_requested(self, params: dict):
        """
        Handle load_requested signal from table.

        Args:
            params: {
                "page": int (0-indexed),
                "page_size": int,
                "filter_term": str | None,
                "order": list[str] | None
            }
        """
        if not self.app:
            self.machine_table.load_error.emit("No app instance")
            return

        # Clear cache when loading new page
        self._machines_cache.clear()

        try:
            # Create map of Interface credentials
            self._credentials.clear()
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

                            self._credentials[creder.issuee] = creder

                        except Exception as exc:
                            logger.warning(f"Skipping credential {saider.qb64}: {exc}")


            # Call appropriate API based on filter state
            response = await remoting.fetch_live_machines(
                app=self.app,
                page=params["page"],
                page_size=params["page_size"],
                filter_term=params.get("filter_term"),
                machine_type="keriguard",
                status=self.current_machine_filter,
                order=params.get("order")
            )

            if not response.get('success'):
                error_msg = response.get('error', 'Unknown error')
                logger.error(f"Failed to load machines: {error_msg}")
                self.machine_table.load_error.emit(error_msg)
                return

            # Pass data to table widget for display
            # The transform_func is applied automatically by set_page_data
            self.machine_table.set_page_data(response, data_key="machines")

        except Exception as e:
            logger.exception(f"Error loading machines: {e}")
            self.machine_table.load_error.emit(str(e))

    def _on_load_error(self, error_msg: str):
        """Handle load errors from table widget."""
        logger.error(f"Machine load error: {error_msg}")

    def _get_row_actions(self, row_data: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
        """
        Determine which row actions to show based on row data.

        - Shows "Update" action when local keystate is ahead of remote
        - Shows "Make Public" action when identifier is not already public

        Args:
            row_data: The row data dict

        Returns:
            Tuple of (actions list, action icons dict)
        """
        all_icons = {
            "View": ":/assets/material-icons/visibility.svg",
            "Remove": ":/assets/material-icons/delete.svg"
        }

        # Start with base actions
        actions = ["View"]

        # Add remaining actions
        actions.extend(["Remove"])

        # Filter icons to only include those for displayed actions
        icons = {action: all_icons[action] for action in actions}

        return actions, icons

    def _on_add_machine(self):
        """Handle Add Machine button click."""
        logger.info("Add Machine button clicked")

        # Open Add Machine dialog
        dialog = AddKERIGuardDeviceDialog(app=self.app, parent=self.parent)
        dialog.machine_added.connect(self._on_machine_added)
        dialog.open()

    def _on_machine_row_clicked(self, row_data: object):
        """Handle machine row click to open View page."""
        if isinstance(row_data, dict):
            data: Dict[str, Any] = {str(k): v for k, v in row_data.items()}
            self._on_machine_action(data, "View")

    def _on_machine_action(self, row_data: Dict[str, Any], action: str):
        """Handle machine row actions."""
        machine_id = row_data.get("_id", "")
        machine_name = row_data.get("Machine Name", "Unknown")

        # Retrieve full machine data from cache
        machine = self._machines_cache.get(machine_id)
        if not machine:
            logger.error(f"Machine {machine_id} not found in cache")
            return

        logger.info(f"Machine action '{action}' triggered for {machine_name}")

        if action == "View":
            self._on_view_machine(machine)
        elif action == "Remove":
            self._on_remove_machine(machine_id, machine)

    def _on_view_machine(self, machine_data: dict):
        """Handle View machine action."""
        logger.info(f"Viewing machine: {machine_data.get('name', 'Unknown')}")

        # Open machine details dialog
        dialog = ViewKERIGuardDeviceDialog(
            icon_path=":/assets/material-icons/devices.svg",
            app=self.app,
            machine=machine_data,
            on_refresh=lambda: self.machine_table.request_load(),
            parent=self.parent
        )
        dialog.open()

    def _on_remove_machine(self, machine_id: str, machine_data: dict):
        """Handle Remove machine action."""
        machine_name = machine_data.get('name', 'Unknown')

        logger.info(f"Removing machine: {machine_id}")

        # Open confirmation dialog
        dialog = DeleteMachineDialog(
            app=self.app,
            machine_id=machine_id,
            name=machine_name,
            on_success=self._on_machine_deleted,
            parent=self.parent
        )
        dialog.open()

    def _on_machine_deleted(self, machine_id: str):
        """Handle successful machine deletion."""
        logger.info(f"Machine {machine_id} deleted, reloading list")
        # Reload the machines list
        self.machine_table.request_load()

    def set_vault_name(self, vault_name: str):
        """Set the vault name and load machines data."""
        self.vault_name = vault_name
        logger.info(f"MachinesListPage: Loading data for vault {vault_name}")

    def on_show(self):
        """Called when the machines page becomes visible."""
        logger.info("MachinesListPage shown, loading data")
        self.machine_table.request_load()

    def _on_machine_added(self, auth_code: str):
        """Handle machine addition from AddMachineDialog."""
        self.machine_table.request_load()

