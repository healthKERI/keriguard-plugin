# -*- encoding: utf-8 -*-
"""keriguard_user.core.daemon_launch — shared plist-template rendering and
launchctl bootstrap helpers for the guardian/sentinel launchd agents, plus
dev-mode subprocess equivalents.

Prod (frozen macOS): the daemons are plain frozen CLI binaries supervised by
bundled launchd agents (DAEMONS.md Phase 3b/3c) rather than a signed .app
bundle with its own SMAppService registration. Follows the `helper_launch.py`
template (frozen-only, no-op on dev runs and non-macOS).

Dev (unfrozen macOS, opt-in via `KERIGUARD_DEV_DAEMONS=1`): the *same* plist
templates are rendered with the *same* values, but instead of being written
to `~/Library/LaunchAgents` and bootstrapped via `launchctl`, their
`ProgramArguments` are extracted directly (`program_args_from_plist`) and
run as a plain child `subprocess.Popen` of this process -- no launchd
involved, so there's zero risk of the dev argv drifting from what prod
actually runs. See `guardian_launch.launch_guardian_daemon_dev` /
`sentinel_launch.launch_sentinel_daemon_dev`.

Lifecycle: daemons are no longer tied to the vault's open/closed state --
they start once, automatically, on Setup completion (`plugin.py
._on_initialization_done`), and from then on outlive the vault being closed
and reopened (the whole point of daemonizing them). They're stopped only via
an explicit user action (the Settings page's Start/Stop Daemons control,
`plugin.py.start_daemons`/`stop_daemons`) or, for dev-mode subprocesses only,
the `atexit` safety net below when the app process itself exits.
"""
from __future__ import annotations

import atexit
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from keri import help

from . import keystore
from .daemon_app_info import DAEMON_APP_INFO

logger = help.ogler.getLogger(__name__)

_RESOURCES_LAUNCHD_DIR = Path(__file__).resolve().parents[1] / "resources" / "launchd"

_PLACEHOLDER_RE = re.compile(r"\{[A-Z_]+\}")

DEV_DAEMONS_ENV_VAR = "KERIGUARD_DEV_DAEMONS"


def is_frozen_macos() -> bool:
    return platform.system() == "Darwin" and getattr(sys, "frozen", False)


def should_use_dev_daemons() -> bool:
    """True when this is an unfrozen macOS dev run that has opted in to
    spawning real guardian/sentinel daemon subprocesses (see module docs).
    Mutually exclusive with `is_frozen_macos()`."""
    return (
        platform.system() == "Darwin"
        and not is_frozen_macos()
        and os.environ.get(DEV_DAEMONS_ENV_VAR) == "1"
    )


def daemons_supported() -> bool:
    """True when this process is capable of launching guardian/sentinel
    daemons at all -- either a frozen macOS build (launchd path) or an
    unfrozen macOS dev run opted into `KERIGUARD_DEV_DAEMONS=1` (subprocess
    path). Used to gate the Settings page's Start/Stop Daemons control."""
    return is_frozen_macos() or should_use_dev_daemons()


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


def bootout_agent(label: str) -> bool:
    """Run `launchctl bootout gui/$(id -u)/<label>` to unregister a
    bootstrapped launchd agent -- the counterpart to `bootstrap_agent`, used
    by the Settings page's manual "Stop Daemons" control (`plugin.py
    .stop_daemons`) now that vault-close no longer stops daemons.

    Idempotent: a label that isn't currently loaded is treated as success,
    matching `bootstrap_agent`'s "already bootstrapped" idempotence.
    """
    import os

    try:
        result = subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").lower()
            if "no such process" in stderr or "could not find" in stderr:
                logger.info(f"daemon_launch: {label} was not bootstrapped")
                return True
            logger.warning(f"daemon_launch: bootout of {label} failed: {stderr}")
            return False
        logger.info(f"daemon_launch: booted out {label}")
        return True
    except Exception:
        logger.exception(f"daemon_launch: failed to bootout {label}")
        return False


# ----------------------------------------------------------------------
# Dev-mode (unfrozen, `KERIGUARD_DEV_DAEMONS=1`) subprocess equivalents.
# No launchd involved -- daemons are plain child processes of this one,
# terminated only via an explicit user action (Settings page "Stop
# Daemons", `plugin.py.stop_daemons`) or, as a safety net for the normal
# whole-app-quit path (see module docs on `stop_all_dev_daemons` below),
# via `atexit`.
# ----------------------------------------------------------------------

def find_dev_executable(name: str) -> Path | None:
    """Locate a dev-mode console-script executable (e.g. "kg", "sentinel" --
    the real script names; not the frozen-renamed "kg-guardian"/
    "sentinel-daemon", which only exist in a PyInstaller build). Checks next
    to the running interpreter first (matches an editable-venv install),
    falling back to PATH."""
    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return candidate
    which = shutil.which(name)
    return Path(which) if which else None


def program_args_from_plist(rendered: str) -> list[str]:
    """Extract `ProgramArguments` from an already-rendered plist (see
    `render_plist`). Reusing the real plist template as the argv source
    means dev mode can never drift from what the frozen/launchd path
    actually runs."""
    import plistlib

    return plistlib.loads(rendered.encode())["ProgramArguments"]


_dev_daemons: dict[str, subprocess.Popen] = {}


def _dev_pid_path(label: str) -> Path:
    return keystore.APP_SUPPORT_DIR / f"{label}.dev.pid"


def _read_dev_pid(label: str) -> int | None:
    try:
        return int(_dev_pid_path(label).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_dev_pid(label: str, pid: int) -> None:
    keystore.APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    _dev_pid_path(label).write_text(str(pid))


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # Any other errno (e.g. EPERM, a different-owner process reusing
        # this pid) means the process exists, just isn't ours to signal.
        return True
    return True


def is_dev_daemon_running(label: str) -> bool:
    """True if a dev-mode daemon spawned under `label` is still alive.

    Checks the tracked `Popen` first (this process's own child); falls back
    to the PID file for one spawned by a prior session. Used by the
    Settings page to render current daemon status."""
    proc = _dev_daemons.get(label)
    if proc is not None:
        return proc.poll() is None
    pid = _read_dev_pid(label)
    return pid is not None and _is_pid_alive(pid)


def _terminate_pid(pid: int) -> None:
    import signal
    import time

    if not _is_pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not _is_pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _terminate_popen(proc: subprocess.Popen) -> None:
    """Terminate a `Popen` child of *this* process via `.wait()` (proper
    reap) rather than raw `os.kill` probing -- `kill(pid, 0)` reports a
    zombie as alive until its parent (us) reaps it, which never happens on
    its own since nothing else calls `.wait()`/`.poll()` on these."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2.0)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        logger.warning(f"daemon_launch: process {proc.pid} did not exit after SIGKILL")


def stop_dev_daemon(label: str) -> None:
    """Terminate a dev daemon process and clear its PID file. Used both for
    an intentional stop (vault close, atexit) and defensively before a
    fresh spawn to reap a process orphaned by a hard `kill -9` of Locksmith
    in a prior session -- without this, two processes would fight over the
    same machine-singleton socket/keystore files (`core/keystore.py`).

    Prefers the tracked `Popen` (this process's own child -- `.wait()`
    properly reaps it) when available; falls back to signaling by PID
    (`_terminate_pid`) for a process only known via its PID file, e.g. one
    orphaned by a prior session that this process never spawned."""
    proc = _dev_daemons.pop(label, None)
    if proc is not None:
        _terminate_popen(proc)
    else:
        pid = _read_dev_pid(label)
        if pid is not None:
            _terminate_pid(pid)
    _dev_pid_path(label).unlink(missing_ok=True)


def spawn_dev_daemon(
    label: str, argv: list[str], stdout_path: Path, stderr_path: Path
) -> subprocess.Popen | None:
    """Spawn a dev-mode daemon subprocess (not launchd-supervised)."""
    stop_dev_daemon(label)  # reap anything stale under this label first
    keystore.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        stdout_f = open(stdout_path, "ab")
        stderr_f = open(stderr_path, "ab")
        proc = subprocess.Popen(
            argv, stdout=stdout_f, stderr=stderr_f, start_new_session=True
        )
    except Exception:
        logger.exception(f"daemon_launch: failed to spawn dev daemon {label}")
        return None
    _dev_daemons[label] = proc
    _write_dev_pid(label, proc.pid)
    logger.info(f"daemon_launch: spawned dev daemon {label} (pid {proc.pid})")
    return proc


def stop_all_dev_daemons() -> None:
    """`atexit` safety net: Locksmith has no `aboutToQuit`/`closeEvent`
    handling today, and dev daemons are intentionally no longer stopped on
    vault close (they're meant to outlive it -- see module docs), so this is
    what actually guarantees dev daemons don't outlive a normal app exit or
    Ctrl+C. A hard `kill -9` of Locksmith itself bypasses even this --
    `spawn_dev_daemon`'s stale-PID reap on the next run is the mitigation
    for that case."""
    for label in list(_dev_daemons.keys()):
        stop_dev_daemon(label)


atexit.register(stop_all_dev_daemons)