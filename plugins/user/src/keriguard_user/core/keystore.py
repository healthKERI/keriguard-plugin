# -*- encoding: utf-8 -*-
"""keriguard_user.core.keystore — filesystem locations for the headless
machine identity's KERI keystore and passcode.

Pulls forward the minimal parts of PLAN.md Phase 5a/5b needed for Phase 1's
provisioning flow to actually create/reopen a Habery. Full Keychain-backed
storage and the upstream --passcode-file CLI flags for `kg guardian start` /
`sentinel start` remain a Phase 5b follow-up -- this is a plain 0600 file,
same trust boundary as the helper's own IPC socket.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from keri import help
from keri.core import coring

logger = help.ogler.getLogger(__name__)

APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "KERIGuard"
BRAN_PATH = APP_SUPPORT_DIR / "server.bran"

# Relative `base` segment (not an absolute headDirPath) for the headless
# machine identity's Haberies -- lands them at <default_head>/keri/ks/plugins
# /keriguard-user/{name,name-sentinel}, alongside the human vault's own
# Haberies (which also use the default headDirPath), under a folder
# locksmith.ui.vaults.drawer's base-navigation shows as a distinct entry
# rather than mixing daemon keystores into the root vault list. hio's
# `Filer.__init__` only rejects an *absolute* base, so this relative,
# multi-segment value is legal -- no headDirPath override needed.
SERVER_BASE = "plugins/keriguard-user"

# DAEMONS.md Phase 3 -- daemon socket/config/log locations, all siblings of
# the keystore tree above under the same App-Support root.
SENTINEL_DIR = APP_SUPPORT_DIR / "sentinel"
SENTINEL_SOCKET_DIR = SENTINEL_DIR
SENTINEL_CONFIG_PATH = SENTINEL_DIR / "config.yaml"
GUARDIAN_HEARTBEAT_PATH = APP_SUPPORT_DIR / "guardian.heartbeat"
LOGS_DIR = APP_SUPPORT_DIR / "logs"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

GUARDIAN_AGENT_LABEL = "com.healthkeri.keriguard.guardian"
SENTINEL_AGENT_LABEL = "com.healthkeri.keriguard.sentinel"

# Shared with the in-process Watcher (plugin.py) -- the daemon and the vault
# process must agree on where exported CESR files land.
DEFAULT_EXPORT_DIR = Path.home() / ".keri" / "keriguard-kel"


def generate_bran() -> str:
    """Generate a fresh 21-character passcode.

    Matches the convention already used elsewhere in this codebase for
    generating salts/passcodes (e.g. locksmith's identifier-creation flow).
    """
    return coring.randomNonce()[2:23]


def load_or_create_bran() -> str:
    """Read the machine identity's passcode from disk, generating and
    persisting a new one (mode 0600, owner-only) on first use."""
    if BRAN_PATH.exists():
        return BRAN_PATH.read_text().strip()

    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    bran = generate_bran()
    BRAN_PATH.write_text(bran)
    os.chmod(BRAN_PATH, stat.S_IRUSR | stat.S_IWUSR)
    logger.info(f"keystore: generated new server passcode at {BRAN_PATH}")
    return bran