# -*- encoding: utf-8 -*-
"""keriguard_user.core.sentinel_check — install-state check for the sentinel
launchd agent.

Follows `helper_check.py`'s `is_helper_installed()` pattern (`launchctl
print gui/<uid>/<label>`). Unlike guardian, sentinel has no heartbeat file --
its liveness is instead exercised via the relocated watcher socket
(`sentinel_{sentinel_hab.pre}.sock` under `keystore.SENTINEL_SOCKET_DIR`,
DAEMONS.md Phase 1a/3d): a `LocalWatcherConnector.watch()` round-trip is
itself the smoke test, mirrored by `daemon_watch.py`.
"""
from __future__ import annotations

import os
import platform
import subprocess

from keri import help

from . import keystore

logger = help.ogler.getLogger(__name__)


def is_sentinel_installed() -> bool:
    """Return True if the sentinel launchd agent is bootstrapped and known
    to launchd."""
    if platform.system() != "Darwin":
        return False

    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{keystore.SENTINEL_AGENT_LABEL}"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("sentinel_check: failed to query launchctl for sentinel registration")
        return False