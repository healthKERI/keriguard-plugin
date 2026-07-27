# -*- encoding: utf-8 -*-
"""keriguard_user.db.basing — KERIGuard user plugin dataclasses and LMDB database."""
from dataclasses import dataclass, field

from keri import help
from keri.db import dbing, koming

logger = help.ogler.getLogger(__name__)


@dataclass
class KERIGuardUserSettings:
    """Persisted settings for the KERIGuard user plugin."""
    credential_source: str = "registrar"  # "registrar" | "healthKERI"
    registrar_url: str = ""
    issuer_aid: str = ""
    issuer_oobi: str = ""
    watcher_alias: str = ""
    config_dir: str = ""
    export_dir: str = ""
    poll_interval: int = 30       # credential poll cadence (seconds)
    kel_watch_interval: int = 30  # issuer KEL watch cadence via witnesses (seconds)
    is_initialized: bool = False
    # Headless machine identity pair (PLAN.md Phase 1) -- two dedicated,
    # non-delegated AIDs, each in its own Habery under server_base_dir,
    # provisioned via the healthKERI auth-code flow (PLAN.md Phase 1d: a
    # shared Habery silently drops writes under concurrent access, so the
    # guardian (WireGuard-peer) and sentinel (KEL-watcher) roles each get
    # their own AID). Not yet consumed by the embedded polling loop (still
    # on watcher_alias above); this is provisioning-only until Phase 2/3
    # land the daemons that will actually run as these identities.
    server_name: str = ""
    server_alias: str = ""
    server_base_dir: str = ""
    server_aid: str = ""
    sentinel_name: str = ""
    sentinel_alias: str = ""
    sentinel_aid: str = ""


@dataclass
class KERIGuardMachineNote:
    """Local user-editable note stored against a machine's interface credential SAID."""
    description: str = ""


@dataclass
class KERIGuardConnectionNote:
    """Local user-editable note stored against a connection credential SAID."""
    description: str = ""


class KERIGuardUserBaser(dbing.LMDBer):
    """Plugin-owned LMDB for KERIGuard user state."""
    TailDirPath = "keri/kgu"
    AltTailDirPath = ".keri/kgu"
    TempPrefix = "kgu"

    def __init__(self, name="keriguard_user", headDirPath=None, reopen=True, **kwa):
        self.keriguardUserSettings = None
        self.keriguardMachineNotes = None
        self.keriguardConnectionNotes = None
        super(KERIGuardUserBaser, self).__init__(
            name=name, headDirPath=headDirPath, reopen=reopen, **kwa
        )

    def reopen(self, **kwa):
        super(KERIGuardUserBaser, self).reopen(**kwa)
        self.keriguardUserSettings = koming.Komer(
            db=self, subkey='kguSettings.', schema=KERIGuardUserSettings, seperator='>'
        )
        self.keriguardMachineNotes = koming.Komer(
            db=self, subkey='kguMachineNotes.', schema=KERIGuardMachineNote, seperator='>'
        )
        self.keriguardConnectionNotes = koming.Komer(
            db=self, subkey='kguConnectionNotes.', schema=KERIGuardConnectionNote, seperator='>'
        )
        return self.env