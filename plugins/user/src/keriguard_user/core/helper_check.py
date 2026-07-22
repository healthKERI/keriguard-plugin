# -*- encoding: utf-8 -*-
"""keriguard_user.core.helper_check -- install-state check and IPC smoke test
for the embedded KERIGuardHelper.app."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import socket
import subprocess
from pathlib import Path

from keri import help

logger = help.ogler.getLogger(__name__)

HELPER_AGENT_LABEL = "com.healthkeri.keriguard.helper.agent"
DEFAULT_SOCKET_PATH = Path.home() / "Library" / "Application Support" / "KERIGuard" / "helper.sock"
PROTOCOL_VERSION = 1


def is_helper_installed() -> bool:
    """Return True if KERIGuardHelper has successfully launched at least once
    and self-registered as a login item (SMAppService agent).

    Bundle presence on disk is deliberately not used as the signal -- a
    helper that can't actually execute (e.g. missing the executable bit)
    never reaches the point of registering itself, so this reports "not
    installed" for exactly that failure mode instead of masking it.
    """
    if platform.system() != "Darwin":
        return False

    try:
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{HELPER_AGENT_LABEL}"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("KERIGuardUserPlugin: failed to query launchctl for helper registration")
        return False


def _blocking_ipc_status(
    interface: str, socket_path: Path, timeout: float
) -> tuple[bool, dict | None, str | None]:
    payload = {"version": PROTOCOL_VERSION, "action": "status", "interface": interface}

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(socket_path))
        s.sendall((json.dumps(payload) + "\n").encode())
        line = s.recv(65536)
    except OSError as exc:
        return False, None, str(exc)
    finally:
        s.close()

    if not line:
        return False, None, "connection closed with no response"

    try:
        response = json.loads(line)
    except json.JSONDecodeError as exc:
        return False, None, f"invalid response: {exc}"

    return bool(response.get("ok")), response, None


async def smoke_test_ipc(
    interface: str = "wg0",
    socket_path: Path | None = None,
    timeout: float = 2.0,
) -> tuple[bool, dict | None, str | None]:
    """Round-trip a `status` request against the running helper's IPC socket.

    Mirrors keriguard-helper/Scripts/check_ipc.py's wire protocol, but as an
    importable async check that returns a result instead of a CLI that
    sys.exit()s.
    """
    return await asyncio.to_thread(
        _blocking_ipc_status, interface, socket_path or DEFAULT_SOCKET_PATH, timeout
    )