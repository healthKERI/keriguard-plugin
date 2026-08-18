"""Dialog for deleting a revoked connection credential."""

from collections.abc import Callable
from typing import TYPE_CHECKING

import qasync
from keri import help
from keri.core import coring
from keriguard.core.wireguarding import Schema
from locksmith.core.credentialing import delete_credential
from locksmith.ui.toolkit.widgets.dialogs import LocksmithResourceDeletionDialog

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication
    from locksmith.ui.vault.page import VaultPage

logger = help.ogler.getLogger(__name__)


class DeleteConnectionDialog(LocksmithResourceDeletionDialog):
    """Dialog for confirming and deleting a revoked connection credential."""

    def __init__(
        self,
        app: "LocksmithApplication",
        connection_name: str,
        connection_said: str,
        on_success: Callable[[str], None] | None = None,
        parent: "VaultPage | None" = None,
    ):
        """Initialize the delete connection dialog.

        Args:
            app: The LocksmithApplication instance
            connection_name: Human-readable name of the connection
            connection_said: SAID of the connection credential
            on_success: Callback to invoke on successful deletion
            parent: Parent VaultPage
        """
        self.app = app
        self.connection_name = connection_name
        self.connection_said = connection_said
        self.on_success = on_success

        super().__init__(
            resource_type="connection",
            resource_name=connection_name,
            title_icon=":/assets/material-icons/delete.svg",
            parent=parent,
        )

        # Connect the delete button to our delete method
        self.delete_button.clicked.disconnect()
        self.delete_button.clicked.connect(self._do_delete)

    @qasync.asyncSlot()
    async def _do_delete(self):
        """
        Perform the async delete operation.

        Removes the revoked connection credential from the local KERI databases
        by removing it from the schema index. This effectively hides it from
        the connections list while preserving the credential data for audit purposes.
        """
        # Disable buttons while processing
        self.delete_button.setEnabled(False)
        self.delete_button.setText("Deleting...")
        self.cancel_button.setEnabled(False)

        try:
            rgy = self.app.vault.rgy

            # Verify the credential exists and is revoked
            creder, *_ = rgy.reger.cloneCred(said=self.connection_said)
            regk = creder.regi
            status = rgy.tevers[regk].vcState(creder.said)

            # Only allow deletion of revoked credentials
            from keri.kering import Ilks
            if status.et not in [Ilks.rev, Ilks.brv]:
                self.show_error("Only revoked (disconnected) credentials can be deleted.")
                self.delete_button.setEnabled(True)
                self.delete_button.setText("Delete")
                self.cancel_button.setEnabled(True)
                return

            # Remove the SAID from the schema index
            result = delete_credential(rgy.reger, self.connection_said)
            if result:
                logger.info(f"Connection credential {self.connection_said} removed from schema index")
            else:
                logger.warning(f"Connection credential {self.connection_said} was not found in schema index")

            if self.on_success:
                self.on_success(self.connection_said)

            self.accept()

        except Exception as exc:
            logger.exception(f"DeleteConnectionDialog: deletion failed: {exc}")
            self.show_error(f"Deletion failed: {exc}")
            self.delete_button.setEnabled(True)
            self.delete_button.setText("Delete")
            self.cancel_button.setEnabled(True)
