# -*- encoding: utf-8 -*-
"""keriguard.machines.list — Machines list page."""
from typing import Dict, Any, TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PySide6.QtGui import QPalette, QColor
from keri import help

from locksmith.ui import colors
from locksmith.ui.toolkit.tables import PaginatedTableWidget
from keriguard.core.wireguarding import Schema

if TYPE_CHECKING:
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class MachinesListPage(QWidget):
    """Paginated list of KERIGuard machines."""
    view_machine = Signal(str)  # emits interface credential SAID when View is triggered

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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.table = PaginatedTableWidget(
            columns=["Name", "AID", "Address", "Port", "Environment", "Status"],
            column_widths={"Name": 180, "Address": 130, "Port": 65, "Environment": 110, "Status": 90, "Actions": 90},
            title="Machines",
            icon_path=":/assets/material-icons/devices.svg",
            show_add_button=False,
            row_actions=["View"],
            row_action_icons={"View": ":/assets/material-icons/visibility.svg"},
            items_per_page=10,
            show_search=True,
            parent=self,
        )

        layout.addWidget(self.table)
        self.table.row_action_triggered.connect(self._on_row_action)
        self.table.row_clicked.connect(self._on_row_clicked)

    def _transform_machine_to_row(self, machine: dict[str, Any]) -> dict[str, Any]:
        said = machine.get("said", "")
        self._machines_cache[said] = machine
        return {
            "Name": machine.get("name", ""),
            "AID": machine.get("aid", ""),
            "Address": machine.get("address", ""),
            "Port": machine.get("port", ""),
            "Environment": machine.get("environment", ""),
            "Status": machine.get("status", "").capitalize(),
            "_said": said,
        }

    def _load_rows(self) -> list[dict[str, Any]]:
        if not self.app or not self.app.vault:
            return []

        kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
        settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
        if not settings or not settings.registry_name:
            return []

        registry = self.app.vault.rgy.registryByName(settings.registry_name)
        if registry is None:
            logger.info(f"KERIGuard: registry {settings.registry_name} not found")
            return []

        rgy = self.app.vault.rgy
        rows: list[dict[str, Any]] = []
        try:
            for saider in (rgy.reger.schms.get(keys=Schema.INTERFACE_SCHEMA) or []):
                try:
                    creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                    if creder.regi != registry.regk:
                        continue
                    payload = creder.attrib
                    iface = payload.get("interface", {})
                    meta = payload.get("interfaceMetadata", {})
                    rows.append(self._transform_machine_to_row({
                        "said": creder.said,
                        "name": meta.get("interfaceName", ""),
                        "aid": payload.get("i", ""),
                        "address": ", ".join(iface.get("address", [])),
                        "port": str(iface.get("listenPort", "")),
                        "environment": meta.get("environment", ""),
                        "status": "issued",
                    }))
                except Exception as exc:
                    logger.warning(f"Skipping credential {saider.qb64}: {exc}")
        except Exception as exc:
            logger.exception(f"Error iterating credentials: {exc}")

        return rows

    def _on_row_clicked(self, row_data: object) -> None:
        if isinstance(row_data, dict):
            data: Dict[str, Any] = {str(k): v for k, v in row_data.items()}
            self._on_row_action(data, "View")

    def _on_row_action(self, row_data: Dict[str, Any], action: str):
        if action == "View":
            said = row_data.get("_said", "")
            if said:
                self.view_machine.emit(said)
                
    def set_vault_name(self, vault_name: str):
        self.vault_name = vault_name

    def on_show(self):
        self._machines_cache.clear()
        self.table.set_static_data(self._load_rows())