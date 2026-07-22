#!/usr/bin/env python3
"""
KERIGuard + Locksmith development environment launcher for iTerm2.

Opens a new window split into 6 panes and starts all services in dependency
order, using sentinel files to detect command completion before proceeding.

Final layout
------------
  ┌─────────────┬─────────────┬─────────────┐
  │  Witnesses  │  SaaS/hkweb │   Locksmith │
  ├─────────────┼─────────────┼─────────────┤
  │ Setup/Wtchr │  Reg. Sent. │  Registrar  │
  └─────────────┴─────────────┴─────────────┘

Usage
-----
    python scripts/dev_env.py

Requirements
------------
    pip install iterm2          # iTerm2 Python API client
    iTerm2 >= 3.4 with Python API enabled
        (Preferences → General → Magic → Enable Python API)
"""

import asyncio
import os
import sys
from pathlib import Path

try:
    import iterm2
except ImportError:
    print("ERROR: iterm2 module not found.\n  Install with: pip install iterm2")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_DIRS = {
    "keripy":      "~/healthkeri/keripy",
    "keriguard":   "~/healthkeri/keriguard",
    "nightingale": "~/healthkeri/keriopnet/nightingale",
    "sentinel":    "~/healthkeri/sentinel",
    "registrar":   "~/healthkeri/registrar",
    "locksmith":   "~/healthkeri/locksmith",
}

VENVS = {
    "keripy":      "~/healthkeri/keripy/venv",
    "keriguard":   "~/healthkeri/keriguard/venv",
    "hkweb":       "~/healthkeri/keriopnet/nightingale/venv/hkweb",
    "sentinel":    "~/healthkeri/sentinel/venv",
    "registrar":   "~/healthkeri/registrar/venv",
    "locksmith":   "~/healthkeri/locksmith/venv",
}

ADMIN_AID  = "EI6-tTwfonE2nKknuUkhkwRe-Op7kTYIeCUJcuuMUFUr"
ADMIN_OOBI = f"http://127.0.0.1:5642/oobi/{ADMIN_AID}/witness"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

NIGHTINGALE_ENV_SH = "/Users/arilieb/healthkeri/keriopnet/nightingale/scripts/env.sh"
KERIGUARD_ENV_SH   = "/Users/arilieb/healthkeri/keriguard/scripts/env.sh"

# Sentinel files used to synchronize steps
_SENTINEL_DIR    = Path("/tmp")
_SUDO_RC_FILE    = _SENTINEL_DIR / "keriguard_sudo_rc"
_SETUP_RC_FILE   = _SENTINEL_DIR / "keriguard_setup_rc"
_WATCHER_RC_FILE = _SENTINEL_DIR / "keriguard_watcher_rc"

ALL_SENTINELS = [
    _SUDO_RC_FILE,
    _SETUP_RC_FILE, _WATCHER_RC_FILE,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send(session: "iterm2.Session", text: str) -> None:
    """Type a line into a session and press Enter."""
    await session.async_send_text(text + "\n")


async def _send_ctrl_c(session: "iterm2.Session") -> None:
    """Send Ctrl-C (SIGINT) to a session."""
    await session.async_send_text("\x03")


async def _activate(session: "iterm2.Session", venv_key: str,
                    project_key: str | None = None) -> None:
    """Activate a virtualenv and optionally cd to the project root."""
    if project_key and project_key in PROJECT_DIRS:
        await _send(session, f"cd {PROJECT_DIRS[project_key]}")
    path = VENVS[venv_key]
    await _send(session, f"source {path}/bin/activate")


async def _wait_for_sentinel(sentinel: Path, timeout: int = 180,
                             label: str = "command") -> int:
    """
    Block until a sentinel file appears, then read and return the exit code
    written inside it.  Raises TimeoutError if the file never shows up.
    """
    for elapsed in range(timeout):
        if sentinel.exists():
            rc = int(sentinel.read_text().strip() or "0")
            sentinel.unlink(missing_ok=True)
            print(f"      {label} finished (exit code {rc}, {elapsed}s elapsed)")
            return rc
        await asyncio.sleep(1)
    raise TimeoutError(
        f"{label} did not complete within {timeout}s (sentinel: {sentinel})"
    )


def _clean_sentinels() -> None:
    """Remove all sentinel files from previous runs."""
    for f in ALL_SENTINELS:
        f.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(connection: "iterm2.Connection") -> None:
    _clean_sentinels()

    # ------------------------------------------------------------------
    # Build window and pane layout
    # ------------------------------------------------------------------
    app = await iterm2.async_get_app(connection)
    window = await iterm2.Window.async_create(connection)
    if window is None:
        window = app.current_terminal_window
    if window is None:
        print("ERROR: Could not create or locate an iTerm2 window. "
              "Is iTerm2 running with the Python API enabled?")
        return

    # Wait for the tab/session to become available (race-condition workaround)
    tab = None
    for _ in range(20):
        tab = window.current_tab
        if tab is not None:
            break
        await asyncio.sleep(0.5)
    if tab is None:
        print("ERROR: New window's tab never became available.")
        return

    s_wit = tab.current_session                                      # top-left
    if s_wit is None:
        print("ERROR: New tab's session never became available.")
        return

    s_saas  = await s_wit.async_split_pane(vertical=True)            # top-middle
    s_lock  = await s_saas.async_split_pane(vertical=True)           # top-right
    s_setup = await s_wit.async_split_pane(vertical=False)           # bottom-left
    s_sent  = await s_saas.async_split_pane(vertical=False)          # bottom-middle
    s_reg   = await s_lock.async_split_pane(vertical=False)          # bottom-right

    panes = [
        (s_wit,   "Witnesses"),
        (s_saas,  "SaaS (hkweb)"),
        (s_lock,  "Locksmith"),
        (s_setup, "Setup / Watcher"),
        (s_sent,  "Registrar Sentinel"),
        (s_reg,   "Registrar"),
    ]
    for session, name in panes:
        await session.async_set_name(name)

    # ==================================================================
    # Step 1 — Cache sudo, wipe state, start witnesses
    # ==================================================================
    print("[1/8] Caching sudo credentials and starting witnesses …")

    await _activate(s_wit, "keripy", project_key="keripy")
    await _send(s_wit, f"sudo -v; echo $? > {_SUDO_RC_FILE}")

    try:
        await _wait_for_sentinel(_SUDO_RC_FILE, timeout=60, label="sudo -v")
    except TimeoutError:
        print("      WARNING: sudo credential caching timed out.")

    await _send(s_wit, "rm -rf /usr/local/var/keri/*")
    await _send(s_wit, "sudo rm -f /var/run/wireguard/wg0.name /var/run/wireguard/utun*.sock")
    await _send(s_wit, "sudo rm -rf /usr/local/var/wireguard/keriguard/*")
    await _send(s_wit, "rm -rf ~/.keri/*")
    await _send(s_wit, "rm -rf /usr/local/var/sentinel/registrar/*")
    await _send(s_wit, "kli witness demo")

    # kli witness demo is long-running; wait for ports to bind
    print("      Pausing 5 s for witnesses to bind …")
    await asyncio.sleep(5)

    # ==================================================================
    # Step 2 — SaaS local deployment (cd to nightingale first)
    # ==================================================================
    print("[2/8] Starting SaaS local deployment …")

    await _send(s_saas, f"source {NIGHTINGALE_ENV_SH}")
    if ANTHROPIC_API_KEY:
        await _send(s_saas, f"export ANTHROPIC_API_KEY={ANTHROPIC_API_KEY}")
    else:
        print("      WARNING: ANTHROPIC_API_KEY not set in environment; skipping export.")
    await _activate(s_saas, "hkweb", project_key="nightingale")
    await _send(
        s_saas,
        "/Users/arilieb/healthkeri/keriopnet/nightingale/scripts/local/restart.sh",
    )

    # restart.sh starts supervisord (long-running); give it time to initialize
    print("      Pausing 30 s for SaaS services to start …")
    await asyncio.sleep(30)

    # ==================================================================
    # Step 3 — Restart witnesses (SaaS restart.sh restores a KERI db
    #          checkpoint that overwrites witness state)
    # ==================================================================
    print("[3/8] Restarting witnesses after SaaS deployment …")

    await _send_ctrl_c(s_wit)
    await asyncio.sleep(2)  # let the process terminate
    await _send(s_wit, "kli witness demo")

    print("      Pausing 5 s for witnesses to rebind …")
    await asyncio.sleep(5)

    # ==================================================================
    # Step 4 — setup.sh (source env.sh, cd to keriguard; must finish
    #          before step 6)
    # ==================================================================
    print("[4/8] Running setup.sh (waiting for completion) …")

    await _send(s_setup, f"source {KERIGUARD_ENV_SH}")
    await _activate(s_setup, "keriguard", project_key="keriguard")
    await _send(
        s_setup,
        f"/Users/arilieb/healthkeri/keriguard/scripts/setup.sh"
        f"; echo $? > {_SETUP_RC_FILE}",
    )

    try:
        rc = await _wait_for_sentinel(_SETUP_RC_FILE, timeout=180, label="setup.sh")
        if rc != 0:
            print(f"      ✗ setup.sh FAILED with exit code {rc}.")
            print("        Cannot continue — steps 5–8 depend on setup.sh.")
            print("        Check the Setup / Watcher pane for details.")
            return
    except TimeoutError:
        print("      ✗ setup.sh timed out after 180 s. Cannot continue.")
        return

    # # ==================================================================
    # # Step 5 — Start registrar sentinel
    # # ==================================================================
    # print("[5/8] Starting registrar sentinel …")
    #
    # await _activate(s_sent, "sentinel", project_key="sentinel")
    # await _send(
    #     s_sent,
    #     "sentinel start --name registrar-sentinel --alias registrar-sentinel"
    #     " --uxd --local"
    #     " --export-dir /usr/local/var/sentinel/registrar",
    # )
    #
    # # sentinel start is long-running; wait for it to initialise
    # print("      Pausing 3 s for sentinel to initialize …")
    # await asyncio.sleep(3)
    #
    # # ==================================================================
    # # Step 6 — Add sentinel watcher for admin (reuses setup pane)
    # #
    # # NOTE: The command + sentinel suffix is too long for a single
    # #       iTerm2 send, so we break it into separate sends.
    # # ==================================================================
    # print("[6/8] Adding sentinel watcher for admin …")
    #
    # await _activate(s_setup, "sentinel", project_key="sentinel")
    # await _send(
    #     s_setup,
    #     "sentinel watcher add"
    #     " --name registrar"
    #     " --alias registrar"
    #     " --watcher registrar-sentinel"
    #     " --watched admin"
    #     f" --oobi {ADMIN_OOBI}",
    # )
    # # Write the exit code of the preceding command on a separate line.
    # # The shell evaluates $? as the exit code of the last command, so
    # # this must be sent immediately after (no intervening commands).
    # await _send(s_setup, f"echo $? > {_WATCHER_RC_FILE}")
    #
    # try:
    #     rc = await _wait_for_sentinel(_WATCHER_RC_FILE, timeout=60,
    #                                   label="sentinel watcher add")
    #     if rc != 0:
    #         print(f"      ✗ sentinel watcher add FAILED with exit code {rc}.")
    #         print("        Registrar may not work correctly.")
    # except TimeoutError:
    #     print("      WARNING: sentinel watcher add timed out. Continuing anyway.")
    #
    # # ==================================================================
    # # Step 7 — Start registrar
    # # ==================================================================
    # print("[7/8] Starting registrar …")
    #
    # await _activate(s_reg, "registrar", project_key="registrar")
    # await _send(
    #     s_reg,
    #     "registrar start --name registrar --alias registrar"
    #     f" --sentinel-export-dir /usr/local/var/sentinel/registrar/"
    #     f" -I {ADMIN_AID}",
    # )
    #
    # # registrar start is long-running; wait for HTTP port
    # print("      Pausing 5 s for registrar to bind …")
    # await asyncio.sleep(5)

    # ==================================================================
    # Step 8 — Start Locksmith
    # ==================================================================
    print("[8/8] Starting Locksmith …")

    await _activate(s_lock, "locksmith", project_key="locksmith")
    await _send(s_lock, "export LOCKSMITH_ENVIRONMENT=development")
    await _send(s_lock, "export ARCHIMEDES_ENVIRONMENT=development")
    await _send(
        s_lock,
        'export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")',
    )

    # await _send(s_lock, "cd /Users/arilieb/healthkeri/locksmith/src/locksmith")
    # await _send(s_lock, "python main.py")

    await _send(s_lock, "cd /Users/arilieb/healthkeri/locksmith/")
    await _send(s_lock, "./scripts/build_test.sh")
    await asyncio.sleep(20)
    await _send(s_lock, "open /Applications/Locksmith.app --stdout /tmp/locksmith.log --stderr /tmp/locksmith.log")

    # Re-use keriguard window to examine locksmith logs
    await _activate(s_setup, "keriguard", project_key="keriguard")
    await _send(s_setup, "tail -f /tmp/locksmith.log")

    print("✓ All services started.")
    print("  Top:    Witnesses | SaaS (hkweb) | Locksmith")
    print("  Bottom: Setup / Watcher | Registrar Sentinel | Registrar")


if __name__ == "__main__":
    iterm2.run_until_complete(main)