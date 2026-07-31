# -*- encoding: utf-8 -*-
"""keriguard_user.core.daemon_launch — shared plist-template rendering and
launchctl bootstrap helpers for the guardian/sentinel launchd agents.

Follows the `helper_launch.py` template (frozen-only, no-op on dev runs and
non-macOS), but the daemons here are plain frozen CLI binaries supervised by
bundled launchd agents (DAEMONS.md Phase 3b/3c) rather than a signed .app
bundle with its own SMAppService registration.
"""
from __future__ import annotations

import platform
import re
import subprocess
import sys
from pathlib import Path

from keri import help

from . import keystore

logger = help.ogler.getLogger(__name__)

_RESOURCES_LAUNCHD_DIR = Path(__file__).resolve().parents[1] / "resources" / "launchd"

_PLACEHOLDER_RE = re.compile(r"\{[A-Z_]+\}")

# The daemon executables are embedded wrapped in a minimal .app bundle
# (Contents/MacOS/<executable>, Contents/Info.plist) rather than as bare
# Mach-O binaries directly under Contents/Resources. A bare signed
# executable has no CFBundleName/CFBundleDisplayName for macOS's Background
# Items / Login Items UI to show, so it falls back to the code signature's
# Common Name -- the developer's own name on the Developer ID Application
# cert -- as the displayed name. Wrapping in a bundle with its own
# CFBundleName fixes that. `locksmith/scripts/embed_daemons.py` builds these
# bundles at package time using this same mapping.
DAEMON_APP_INFO = {
    "kg-guardian": {
        "app_name": "KERIGuardGuardian.app",
        "bundle_name": "KERIGuard Guardian",
        "bundle_id": "com.healthkeri.keriguard.guardian",
    },
    "sentinel-daemon": {
        "app_name": "KERIGuardSentinel.app",
        "bundle_name": "KERIGuard Sentinel",
        "bundle_id": "com.healthkeri.keriguard.sentinel",
    },
}


def is_frozen_macos() -> bool:
    return platform.system() == "Darwin" and getattr(sys, "frozen", False)


def _frozen_resources_dir() -> Path | None:
    """Resolve `Contents/Resources` from the frozen bundle, mirroring
    `helper_launch.py`'s lookup."""
    meipass = Path(sys._MEIPASS)
    contents = next((p for p in meipass.parents if p.name == "Contents"), None)
    if contents is None:
        return None
    return contents / "Resources"


def find_daemon_executable(name: str) -> Path | None:
    """Locate a frozen daemon executable (e.g. "kg-guardian",
    "sentinel-daemon") embedded next to KERIGuardHelper.app under
    Contents/Resources (DAEMONS.md Phase 4). Returns None if not embedded
    (dev/unfrozen runs have no frozen daemon binaries at all).

    The executable is wrapped in its own minimal .app bundle (see
    `DAEMON_APP_INFO`) so it has a CFBundleName for macOS's Background
    Items UI -- this resolves the nested Contents/MacOS/<name> path, not a
    bare Contents/Resources/<name> path.
    """
    resources = _frozen_resources_dir()
    if resources is None:
        return None
    app_info = DAEMON_APP_INFO.get(name)
    if app_info is None:
        candidate = resources / name
        return candidate if candidate.exists() else None
    candidate = resources / app_info["app_name"] / "Contents" / "MacOS" / name
    return candidate if candidate.exists() else None


def find_plist_template(filename: str) -> Path | None:
    """Locate a bundled launchd plist template, whether running from an
    installed/source package (`keriguard_user/resources/launchd/`, shipped as
    package data — see pyproject.toml) or frozen (`Contents/Resources/launchd/`)."""
    if is_frozen_macos():
        resources = _frozen_resources_dir()
        if resources is not None:
            candidate = resources / "launchd" / filename
            if candidate.exists():
                return candidate
    candidate = _RESOURCES_LAUNCHD_DIR / filename
    return candidate if candidate.exists() else None


def render_plist(template_path: Path, values: dict[str, str]) -> str:
    """Substitute `{TOKEN}` placeholders in the template with `values`.

    Whole-token substitution only (never partial/string-interpolated) so a
    value containing a literal "{" or "}" (an unlikely but not impossible
    filesystem path) can't be mistaken for another placeholder.
    """
    text = template_path.read_text()

    def _sub(match: re.Match) -> str:
        token = match.group(0)[1:-1]
        if token not in values:
            raise KeyError(f"render_plist: no value supplied for placeholder {{{token}}}")
        return values[token]

    return _PLACEHOLDER_RE.sub(_sub, text)


def write_plist(label: str, rendered: str) -> Path:
    keystore.LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    keystore.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    plist_path = keystore.LAUNCH_AGENTS_DIR / f"{label}.plist"
    plist_path.write_text(rendered)
    return plist_path


def bootstrap_agent(plist_path: Path) -> bool:
    """Run `launchctl bootstrap gui/$(id -u) <plist>`.

    Idempotent in practice: a re-bootstrap of an already-loaded label fails
    with "already bootstrapped" — callers should `launchctl bootout` first if
    they need to force-reload a changed plist (not needed for first-launch).
    """
    import os

    try:
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            if "already bootstrapped" in stderr.lower():
                logger.info(f"daemon_launch: {plist_path.name} already bootstrapped")
                return True
            logger.warning(f"daemon_launch: bootstrap of {plist_path.name} failed: {stderr}")
            return False
        logger.info(f"daemon_launch: bootstrapped {plist_path.name}")
        return True
    except Exception:
        logger.exception(f"daemon_launch: failed to bootstrap {plist_path.name}")
        return False