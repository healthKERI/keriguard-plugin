# -*- encoding: utf-8 -*-
"""keriguard_user.core.helper_launch — launches the embedded KERIGuardHelper.app."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from keri import help

logger = help.ogler.getLogger(__name__)

HELPER_APP_NAME = "KERIGuardHelper.app"


def launch_helper_app() -> None:
    """Launch the embedded KERIGuardHelper.app once, if present.

    Safe to call repeatedly -- `open -a` and the helper's own SMAppService
    login-item registration are both idempotent. No-ops on non-macOS builds
    and on unfrozen (dev) runs, where there is no embedded helper.
    """
    if platform.system() != "Darwin" or not getattr(sys, "frozen", False):
        logger.info("Not a frozen macOS build; skipping KERIGuardHelper launch")
        return

    meipass = Path(sys._MEIPASS)
    contents = next((p for p in meipass.parents if p.name == "Contents"), None)
    if contents is None:
        logger.warning(f"Could not resolve Contents/ from {meipass}; skipping helper launch")
        return

    app_path = contents / "Resources" / HELPER_APP_NAME
    if not app_path.exists():
        logger.warning(f"{HELPER_APP_NAME} not embedded at {app_path}; skipping launch")
        return

    try:
        subprocess.Popen(["open", "-a", str(app_path)])
        logger.info(f"Launched {app_path}")
    except Exception:
        logger.exception(f"Failed to launch {HELPER_APP_NAME}")