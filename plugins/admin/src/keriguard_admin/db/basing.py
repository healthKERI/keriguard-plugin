# -*- encoding: utf-8 -*-
"""keriguard.db.basing — KERIGuard dataclasses and LMDB database."""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from keri import help
from keri.db import dbing, koming

if TYPE_CHECKING:
    from locksmith.core.apping import LocksmithApplication

logger = help.ogler.getLogger(__name__)


@dataclass
class KERIGuardAccount:
    """HealthKERI account information shared into the keriguard plugin."""
    aid: str
    alias: str
    email: str
    receiveEmail: bool
    cellPhone: str
    receiveText: bool
    firstName: str
    lastName: str
    username: str
    default_team: str | None = None


@dataclass
class KERIGuardTeam:
    """HealthKERI team information shared into the keriguard plugin."""
    name: str
    email: str
    id: str | None = None
    members: list[dict[str, str]] | None = None
    projects: list[str] | None = None
    paymentMethods: str | None = None
    stripeCustomerId: str | None = None
    identifierLimit: int | None = None
    watcherLimit: int | None = None
    witnessLimit: int | None = None
    mailboxLimit: int | None = None

@dataclass
class KERIGuardSettings:
    """Persisted settings for the KERIGuard plugin."""
    registry_name: str = ""
    registrar_url: str = ""
    export_dir: str = ""
    publish_mode: str = "registrar"  # "registrar" | "hkweb"


@dataclass
class KERIGuardMachineNote:
    """Local user-editable note stored against a machine's interface credential SAID."""
    description: str = ""


@dataclass
class KERIGuardConnectionNote:
    """Local user-editable note stored against a connection credential SAID."""
    description: str = ""


class KERIGuardBaser(dbing.LMDBer):
    """Plugin-owned LMDB for KERIGuard state."""
    TailDirPath = "keri/kg"
    AltTailDirPath = ".keri/kg"
    TempPrefix = "kg"

    def __init__(self, name="keriguard", headDirPath=None, reopen=True, **kwa):
        self.keriguardAccounts = None
        self.keriguardTeams = None
        self.keriguardSettings = None
        self.keriguardMachineNotes = None
        self.keriguardConnectionNotes = None
        super(KERIGuardBaser, self).__init__(name=name, headDirPath=headDirPath, reopen=reopen, **kwa)

    def reopen(self, **kwa):
        super(KERIGuardBaser, self).reopen(**kwa)
        self.keriguardAccounts = koming.Komer(
            db=self, subkey='hkAccounts.', schema=KERIGuardAccount, seperator='>'
        )
        self.keriguardTeams = koming.Komer(
            db=self, subkey='hkTeams.', schema=KERIGuardTeam, seperator='>'
        )
        self.keriguardSettings = koming.Komer(
            db=self, subkey='kgSettings.', schema=KERIGuardSettings, seperator='>'
        )
        self.keriguardMachineNotes = koming.Komer(
            db=self, subkey='kgMachineNotes.', schema=KERIGuardMachineNote, seperator='>'
        )
        self.keriguardConnectionNotes = koming.Komer(
            db=self, subkey='kgConnectionNotes.', schema=KERIGuardConnectionNote, seperator='>'
        )
        return self.env


def sync_account_to_keriguard(app: "LocksmithApplication") -> bool:
    """
    Sync the current healthKERI account into the keriguard plugin state.

    Reads from plugin_state["healthkeri"], builds KERIGuardAccount/Team,
    persists to KERIGuardBaser, and updates plugin_state["keriguard"].
    Called on "hk_team_created" doer event (same pattern as whisper).
    """
    if not app.vault:
        return False

    hk_state = app.vault.plugin_state.get("healthkeri", {})
    kg_state = app.vault.plugin_state.get("keriguard", {})

    hk_account = hk_state.get("account")
    kg_db = kg_state.get("db")

    if hk_account is None or kg_db is None:
        logger.warning("sync_account_to_keriguard: missing healthkeri account or db")
        return False

    kg_account = KERIGuardAccount(
        aid=hk_account.aid,
        alias=hk_account.alias,
        email=hk_account.email,
        receiveEmail=hk_account.receiveEmail,
        cellPhone=hk_account.cellPhone,
        receiveText=hk_account.receiveText,
        firstName=hk_account.firstName,
        lastName=hk_account.lastName,
        username=hk_account.username,
        default_team=hk_account.default_team,
    )
    kg_db.keriguardAccounts.pin(keys=(hk_account.aid,), val=kg_account)
    kg_state["account"] = kg_account

    hk_team = hk_state.get("team")
    if hk_team is None:
        logger.warning("sync_account_to_keriguard: missing healthkeri team")
        return False

    kg_team = KERIGuardTeam(
        name=hk_team.name,
        email=hk_team.email,
        id=hk_team.id,
        members=hk_team.members,
        projects=hk_team.projects,
        paymentMethods=hk_team.paymentMethods,
        stripeCustomerId=hk_team.stripeCustomerId,
        identifierLimit=hk_team.identifierLimit,
        watcherLimit=hk_team.watcherLimit,
        witnessLimit=hk_team.witnessLimit,
        mailboxLimit=hk_team.mailboxLimit,
    )
    kg_db.keriguardTeams.pin(keys=(hk_team.name,), val=kg_team)
    kg_state["team"] = kg_team

    logger.info(f"sync_account_to_keriguard: synced account {hk_account.aid}")
    return True