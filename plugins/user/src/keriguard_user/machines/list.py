# -*- encoding: utf-8 -*-
"""keriguard_user.machines.list — Machines list page (received interface credentials)."""
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


def _wg_status(interface_name: str) -> str:
    try:
        from keriguard.core.systeming import _is_wireguard_up
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _is_wireguard_up(interface_name)).result()
        else:
            result = loop.run_until_complete(_is_wireguard_up(interface_name))
        return "Active" if result else "Inactive"
    except Exception:
        return "Unknown"


class MachinesListPage(QWidget):
    """Paginated list of received KERIGuard interface credentials."""

    view_machine = Signal(str)
    import_clicked = Signal()

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
            columns=["Name", "AID", "Address", "Port", "Status"],
            column_widths={"Name": 180, "Address": 130, "Port": 65, "Status": 120, "Actions": 90},
            title="Machines",
            icon_path=":/assets/material-icons/devices.svg",
            show_add_button=True,
            add_button_text="Import Credential",
            row_actions=["View"],
            row_action_icons={"View": ":/assets/material-icons/visibility.svg"},
            items_per_page=10,
            show_search=True,
            parent=self,
        )
        layout.addWidget(self.table)
        self.table.row_action_triggered.connect(self._on_row_action)
        self.table.row_clicked.connect(self._on_row_clicked)
        self.table.add_clicked.connect(self.import_clicked.emit)

    def _transform_machine_to_row(self, machine: dict[str, Any]) -> dict[str, Any]:
        said = machine.get("said", "")
        self._machines_cache[said] = machine
        return {
            "Name": machine.get("name", ""),
            "AID": machine.get("aid", ""),
            "Address": machine.get("address", ""),
            "Port": machine.get("port", ""),
            "Status": machine.get("wg_status", "Unknown"),
            "_said": said,
        }

    def _load_rows(self) -> list[dict[str, Any]]:
        if not self.app or not self.app.vault:
            return []

        vault = self.app.vault
        rgy = vault.rgy
        user_aids = set(vault.hby.habs.keys())
        rows: list[dict[str, Any]] = []

        try:
            for saider in (rgy.reger.schms.get(keys=Schema.INTERFACE_SCHEMA) or []):
                try:
                    creder, *_ = rgy.reger.cloneCred(said=saider.qb64)
                    if creder.attrib.get("i") not in user_aids:
                        continue
                    payload = creder.attrib
                    iface = payload.get("interface", {})
                    meta = payload.get("interfaceMetadata", {})
                    iface_name = meta.get("interfaceName", "")
                    rows.append(self._transform_machine_to_row({
                        "said": creder.said,
                        "name": iface_name,
                        "aid": payload.get("i", ""),
                        "address": ", ".join(iface.get("address", [])),
                        "port": str(iface.get("listenPort", "")),
                        "wg_status": _wg_status(iface_name),
                        "environment": meta.get("environment", ""),
                    }))
                except Exception as exc:
                    logger.warning(f"Skipping credential {saider.qb64}: {exc}")
        except Exception as exc:
            logger.exception(f"Error iterating credentials: {exc}")

        return rows

    def _on_row_clicked(self, row_data: object) -> None:
        if isinstance(row_data, dict):
            self._on_row_action({str(k): v for k, v in row_data.items()}, "View")

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