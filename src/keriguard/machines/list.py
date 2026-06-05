# -*- encoding: utf-8 -*-
"""keriguard.machines.list — Machines list page."""
from typing import Any, TYPE_CHECKING

import qasync
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QPalette, QColor
from keri import help

from locksmith.ui import colors
from locksmith.ui.toolkit.tables import PaginatedTableWidget

if TYPE_CHECKING:
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class MachinesListPage(QWidget):
    """Paginated list of KERIGuard machines."""

    def __init__(self, app, parent: "VaultPage | None" = None):
        super().__init__(parent)
        self._parent = parent
        self.app = app
        self.vault_name = ""
        self._machines_cache: dict[str, dict[str, Any]] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors.BACKGROUND_CONTENT))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.table = PaginatedTableWidget(
            columns=["Name", "AID", "Status"],
            column_widths={"Name": 220, "Status": 110, "Actions": 50},
            title="Machines",
            icon_path=":/assets/material-icons/settings-hover.svg",
            show_add_button=False,
            row_actions=[],
            items_per_page=10,
            show_search=True,
            transform_func=self._transform_machine_to_row,
            parent=self,
        )

        self.table.load_requested.connect(self._on_load_requested)
        self.table.load_error.connect(self._on_load_error)

        layout.addWidget(self.table)

    def _transform_machine_to_row(self, machine: dict[str, Any]) -> dict[str, Any]:
        said = machine.get("said", "")
        self._machines_cache[said] = machine
        return {
            "Name": machine.get("name", ""),
            "AID": machine.get("aid", ""),
            "Status": machine.get("status", "").capitalize(),
            "_said": said,
        }

    @qasync.asyncSlot(dict)
    async def _on_load_requested(self, params: dict):
        self._machines_cache.clear()
        self.table.set_page_data({"machines": [], "total": 0}, data_key="machines")

    @staticmethod
    def _on_load_error(error_msg: str):
        logger.error(f"Table load error: {error_msg}")

    def set_vault_name(self, vault_name: str):
        self.vault_name = vault_name

    def on_show(self):
        self._machines_cache.clear()
        self.table.request_load()