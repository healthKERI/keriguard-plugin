# -*- encoding: utf-8 -*-
"""keriguard_user.core.guardian_launch — writes and bootstraps the guardian
(WireGuard-peer) launchd agent.

DAEMONS.md Phase 3a/3c: the guardian AID is witnessed (Decisions section),
but that shape lives entirely in the AID's KEL from provisioning -- `kg
guardian start`'s flags are the same whether or not the AID has a witness,
so there's no witness-specific argument here. No OTP pre-check either: the
OTP artifact at `~/.keriguard/{server_hab.pre}` (see DAEMONS.md Decisions
correction) is written once during provisioning's `connect_to_healthkeri`
handshake and never read back at daemon start time.
"""
from __future__ import annotations

from keri import help

from . import keystore
from .daemon_launch import (
    bootstrap_agent,
    find_daemon_executable,
    find_plist_template,
    is_frozen_macos,
    render_plist,
    write_plist,
)

logger = help.ogler.getLogger(__name__)

KG_EXECUTABLE_NAME = "kg-guardian"
PLIST_TEMPLATE_NAME = "com.healthkeri.keriguard.guardian.plist"


def launch_guardian_daemon(settings) -> bool:
    """Write the launchd plist for the guardian daemon and bootstrap it via
    `launchctl`.

    No-op (returns False) on non-macOS or unfrozen (dev) runs, and if the
    settings don't yet carry a provisioned guardian/sentinel identity pair --
    mirrors `launch_helper_app()`'s guard pattern. Safe to call repeatedly:
    `launchctl bootstrap` on an already-loaded label is treated as success.
    """
    if not is_frozen_macos():
        logger.info("Not a frozen macOS build; skipping guardian daemon launch")
        return False

    if not settings or not settings.server_name or not settings.server_alias or not settings.sentinel_aid:
        logger.warning("guardian_launch: settings missing provisioned server identity; skipping")
        return False

    if not settings.config_dir:
        logger.warning("guardian_launch: no WireGuard config directory configured; skipping")
        return False

    executable = find_daemon_executable(KG_EXECUTABLE_NAME)
    if executable is None:
        logger.warning(f"{KG_EXECUTABLE_NAME} not embedded; skipping guardian daemon launch")
        return False

    template = find_plist_template(PLIST_TEMPLATE_NAME)
    if template is None:
        logger.warning(f"{PLIST_TEMPLATE_NAME} not found; skipping guardian daemon launch")
        return False

    values = {
        "KG_EXECUTABLE": str(executable),
        "BASE": settings.server_base or keystore.SERVER_BASE,
        "GUARDIAN_NAME": settings.server_name,
        "GUARDIAN_ALIAS": settings.server_alias,
        "SENTINEL_AID": settings.sentinel_aid,
        "SENTINEL_EXPORT_DIR": settings.export_dir or str(keystore.DEFAULT_EXPORT_DIR),
        "CONFIG_DIR": settings.config_dir,
        "PASSCODE_FILE": str(keystore.BRAN_PATH),
        "HEARTBEAT_FILE": str(keystore.GUARDIAN_HEARTBEAT_PATH),
        "STDOUT_LOG": str(keystore.LOGS_DIR / "guardian.stdout.log"),
        "STDERR_LOG": str(keystore.LOGS_DIR / "guardian.stderr.log"),
    }

    try:
        rendered = render_plist(template, values)
    except KeyError:
        logger.exception("guardian_launch: plist template rendering failed")
        return False

    plist_path = write_plist(keystore.GUARDIAN_AGENT_LABEL, rendered)
    return bootstrap_agent(plist_path)