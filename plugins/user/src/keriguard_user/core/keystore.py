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
KEYSTORES_DIR = APP_SUPPORT_DIR / "keystores"
BRAN_PATH = APP_SUPPORT_DIR / "server.bran"


def keystores_dir() -> Path:
    """Directory holding the headless machine identity's LMDB keystore(s).

    A sibling of the human vault's own LMDB tree (not nested inside it) --
    deleting the Locksmith vault must not orphan/break the still-running
    daemons this identity will eventually back, and vice versa.
    """
    KEYSTORES_DIR.mkdir(parents=True, exist_ok=True)
    return KEYSTORES_DIR


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