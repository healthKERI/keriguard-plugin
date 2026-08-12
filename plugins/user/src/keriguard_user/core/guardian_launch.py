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

import os
from pathlib import Path

from keri import help

from . import keystore
from .daemon_launch import (
    bootout_agent,
    bootstrap_agent,
    find_daemon_executable,
    find_dev_executable,
    find_plist_template,
    is_dev_daemon_running,
    is_frozen_macos,
    program_args_from_plist,
    render_plist,
    spawn_dev_daemon,
    stop_dev_daemon,
    write_plist,
)

logger = help.ogler.getLogger(__name__)

KG_EXECUTABLE_NAME = "kg-guardian"
KG_DEV_EXECUTABLE_NAME = "kg"
PLIST_TEMPLATE_NAME = "com.healthkeri.keriguard.guardian.plist"


def _guardian_settings_valid(settings) -> bool:
    if not settings or not settings.server_name or not settings.server_alias or not settings.sentinel_aid:
        logger.warning("guardian_launch: settings missing provisioned server identity; skipping")
        return False
    if not settings.config_dir:
        logger.warning("guardian_launch: no WireGuard config directory configured; skipping")
        return False
    return True


def _guardian_values(executable: Path, settings, heartbeat_path: Path | None = None) -> dict[str, str]:
    """`heartbeat_path` defaults to the prod machine-singleton path
    (`keystore.GUARDIAN_HEARTBEAT_PATH`); dev-mode callers pass a
    per-vault path (`keystore.dev_guardian_heartbeat_path`) so two vaults'
    dev guardians don't touch the same heartbeat file."""
    return {
        "KG_EXECUTABLE": str(executable),
        "BASE": settings.server_base or keystore.SERVER_BASE,
        "GUARDIAN_NAME": settings.server_name,
        "GUARDIAN_ALIAS": settings.server_alias,
        "SENTINEL_AID": settings.sentinel_aid,
        "SENTINEL_EXPORT_DIR": settings.export_dir or str(keystore.DEFAULT_EXPORT_DIR),
        "CONFIG_DIR": settings.config_dir,
        # Must match the sentinel daemon's own --socket-dir (sentinel_launch.py)
        # -- otherwise the guardian's peer-AID resolution retries dial the
        # unrelocated /tmp default and never reach the real socket.
        "SOCKET_DIR": str(keystore.SENTINEL_SOCKET_DIR),
        "PASSCODE_FILE": str(keystore.BRAN_PATH),
        "HEARTBEAT_FILE": str(heartbeat_path or keystore.GUARDIAN_HEARTBEAT_PATH),
        "STDOUT_LOG": str(keystore.LOGS_DIR / "guardian.stdout.log"),
        "STDERR_LOG": str(keystore.LOGS_DIR / "guardian.stderr.log"),
        "ARCHIMEDES_ENVIRONMENT": os.environ.get("ARCHIMEDES_ENVIRONMENT", ""),
        "LOCKSMITH_ENVIRONMENT": os.environ.get("LOCKSMITH_ENVIRONMENT", ""),
    }


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

    if not _guardian_settings_valid(settings):
        return False

    executable = find_daemon_executable(KG_EXECUTABLE_NAME)
    if executable is None:
        logger.warning(f"{KG_EXECUTABLE_NAME} not embedded; skipping guardian daemon launch")
        return False

    template = find_plist_template(PLIST_TEMPLATE_NAME)
    if template is None:
        logger.warning(f"{PLIST_TEMPLATE_NAME} not found; skipping guardian daemon launch")
        return False

    try:
        rendered = render_plist(template, _guardian_values(executable, settings))
    except KeyError:
        logger.exception("guardian_launch: plist template rendering failed")
        return False

    plist_path = write_plist(keystore.GUARDIAN_AGENT_LABEL, rendered)
    return bootstrap_agent(plist_path)


def launch_guardian_daemon_dev(settings):
    """Dev-mode equivalent of `launch_guardian_daemon`: spawns `kg guardian
    start` as a plain child subprocess instead of a launchd agent. Renders
    the same plist template/values as prod and extracts its
    `ProgramArguments` (`program_args_from_plist`) so the two paths can
    never drift apart. Returns the `subprocess.Popen`, or `None` on failure
    or if guarded off (see `daemon_launch.should_use_dev_daemons`)."""
    if not _guardian_settings_valid(settings):
        return None

    executable = find_dev_executable(KG_DEV_EXECUTABLE_NAME)
    if executable is None:
        logger.warning(f"{KG_DEV_EXECUTABLE_NAME!r} executable not found; skipping dev guardian launch")
        return None

    template = find_plist_template(PLIST_TEMPLATE_NAME)
    if template is None:
        logger.warning(f"{PLIST_TEMPLATE_NAME} not found; skipping dev guardian launch")
        return None

    heartbeat_path = keystore.dev_guardian_heartbeat_path(settings.server_name)
    try:
        rendered = render_plist(template, _guardian_values(executable, settings, heartbeat_path=heartbeat_path))
        argv = program_args_from_plist(rendered)
    except Exception:
        logger.exception("guardian_launch: dev argv rendering failed")
        return None

    return spawn_dev_daemon(
        keystore.dev_guardian_agent_label(settings.server_name),
        argv,
        keystore.LOGS_DIR / f"guardian.dev.{settings.server_name}.stdout.log",
        keystore.LOGS_DIR / f"guardian.dev.{settings.server_name}.stderr.log",
    )


def stop_guardian_daemon_dev(settings) -> None:
    stop_dev_daemon(keystore.dev_guardian_agent_label(settings.server_name))


def is_guardian_dev_running(settings) -> bool:
    return is_dev_daemon_running(keystore.dev_guardian_agent_label(settings.server_name))


def stop_guardian_daemon() -> bool:
    """Stop the guardian launchd agent via `launchctl bootout`. Frozen macOS
    only -- mirrors `launch_guardian_daemon`'s guard.

    Note the agent label is a machine-wide singleton (DAEMONS.md Phase 3e):
    this stops whichever vault's identity currently owns the daemon slot,
    not necessarily this vault's own.
    """
    if not is_frozen_macos():
        return False
    return bootout_agent(keystore.GUARDIAN_AGENT_LABEL)