# -*- encoding: utf-8 -*-
"""keriguard_user.core.guardian_check — install-state and liveness checks for
the guardian launchd agent.

Follows `helper_check.py`'s `is_helper_installed()` pattern (`launchctl
print gui/<uid>/<label>`), plus a heartbeat-file staleness check: the
guardian has no status socket on macOS (Phase 1d), so `guardian.heartbeat`'s
mtime is the only liveness signal, touched once per poll cycle by the
sentinel-framework runner the guardian embeds. A healthy but *idle* daemon
still touches it every cycle, so staleness must be derived from the poll
interval, not from "an event happened recently".
"""
from __future__ import annotations

import os
import platform
import subprocess
import time

from keri import help

from . import keystore

logger = help.ogler.getLogger(__name__)

# `kg guardian start --poll-interval` default (keriguard/.../guardian/start.py).
DEFAULT_POLL_INTERVAL = 2.0

# How many missed cycles before the heartbeat is considered stale. Generous
# margin above 1 to absorb scheduling jitter and a slow poll cycle, while
# still catching a genuinely wedged/killed daemon well before a human would
# notice on their own.
MAX_MISSED_CYCLES = 5


def is_guardian_installed() -> bool:
    """Return True if the guardian launchd agent is bootstrapped and known
    to launchd (does not by itself mean the process is currently healthy --
    see `is_guardian_alive()`)."""
    if platform.system() != "Darwin":
        return False

    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{keystore.GUARDIAN_AGENT_LABEL}"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("guardian_check: failed to query launchctl for guardian registration")
        return False


def is_guardian_alive(poll_interval: float = DEFAULT_POLL_INTERVAL) -> bool:
    """Return True if `guardian.heartbeat` has been touched within the last
    `MAX_MISSED_CYCLES` poll cycles.

    False if the heartbeat file has never been created (daemon never
    completed a poll cycle -- e.g. still starting up, or dead before its
    first cycle).
    """
    threshold = poll_interval * MAX_MISSED_CYCLES
    try:
        mtime = keystore.GUARDIAN_HEARTBEAT_PATH.stat().st_mtime
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception("guardian_check: failed to stat heartbeat file")
        return False

    return (time.time() - mtime) <= threshold