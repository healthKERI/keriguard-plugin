"""Dialog for disconnecting a connection."""
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING

import qasync
from keri import help
from keri.help import helping
from keriguard.core.kering import Issuer
from locksmith.core.signals import DoerSignalBridge
from locksmith.ui.toolkit.widgets.dialogs import LocksmithResourceDeletionDialog
from locksmith.ui.vault.identifiers.authenticate import WitnessAuthenticationDialog

from keriguard_admin.core.remoting import push_revocation_via_essr, push_revocation_to_registrar

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class DisconnectConnectionDialog(LocksmithResourceDeletionDialog):
    """Dialog for confirming and disconnecting a connection."""

    def __init__(
        self,
        app: "LocksmithApplication",
        connection_name: str,
        connection_said: str,
        on_success: Callable[[str], None] | None = None,
        parent: "VaultPage | None" = None,
    ):
        """Initialize the disconnect connection dialog.

        Args:
            app: The LocksmithApplication instance
            connection_name: Human-readable name of the connection
            connection_said: SAID of the connection credential
            on_success: Callback to invoke on successful disconnect
            parent: Parent VaultPage
        """
        self.app = app
        self.connection_name = connection_name
        self.connection_said = connection_said
        self.on_success = on_success
        self.signals = DoerSignalBridge()


        super().__init__(
            resource_type="connection",
            resource_name=connection_name,
            title_icon=":/assets/material-icons/link-off.svg",
            parent=parent,
        )

        # Override delete button text to "Disconnect"
        self.delete_button.setText("Disconnect")

        # Connect the delete button to our disconnect method
        self.delete_button.clicked.disconnect()
        self.delete_button.clicked.connect(self._do_disconnect)
        self.signals.auth_codes_entered.connect(self._on_auth_codes_entered)

    @qasync.asyncSlot()
    async def _do_disconnect(self):
        """
        STUB: Perform the async disconnect operation.

        Future implementation should:
        1. Revoke the connection credential in the registry
        2. Notify both peers via ESSR or registrar
        3. Trigger sentinels to remove WireGuard peer configuration
        4. Update credential status to 'revoked'
        """
        # Disable buttons while processing
        self.delete_button.setEnabled(False)
        self.delete_button.setText("Disconnecting...")
        self.cancel_button.setEnabled(False)

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
            signals=self.signals,
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
        self.signals.auth_codes_entered.disconnect(self._on_auth_codes_entered)

        try:
            kg_db = self.app.vault.plugin_state.get("keriguard", {}).get("db")
            settings = kg_db.keriguardSettings.get(keys=("settings",)) if kg_db else None
            essr = self.app.vault.plugin_state.get("keriguard", {}).get("essr")

            hby = self.app.vault.hby
            rgy = self.app.vault.rgy
            hab = hby.habs[settings.issuer_aid]

            issuer = Issuer(hby=hby, hab=hab, rgy=rgy)

            codes = data.get('codes', [])
            auths = {}
            if codes:
                code_time = helping.nowIso8601()
                for arg in codes:
                    wit, code = arg.split(":")
                    auths[wit] = f"{code}#{code_time}"


            rev, anc = await issuer.revoke_connection_credential(self.connection_said, auths)

            publish_mode = settings.publish_mode if settings else "registrar"

            if publish_mode == "serviceprovider" and essr:
                await push_revocation_via_essr(self.connection_said, hab.pre, essr, rev, anc)
            elif settings and settings.registrar_url:
                await push_revocation_to_registrar(rev, anc, settings.registrar_url)


            if self.on_success:
                self.on_success(self.connection_said)

            self.accept()

        except Exception as exc:
            logger.exception(f"DisconnectConnectionDialog: revocation failed: {exc}")
            self.show_error(f"Revocation failed: {exc}")
            self.delete_button.setEnabled(True)
            self.delete_button.setText("Disconnect")
