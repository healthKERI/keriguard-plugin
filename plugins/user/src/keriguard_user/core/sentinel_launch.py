# -*- encoding: utf-8 -*-
"""keriguard_user.core.sentinel_launch — generates the SaaS `sentinel start`
config, writes the launchd agent, and bootstraps it.

DAEMONS.md Phase 3a: sentinel runs in SaaS mode (`credential_source =
"healthKERI"` decision), so `sentinel start` hard-requires `--config <yaml>`
-- server_name/server_alias can't be passed as flags in that mode. The YAML
must exist on disk *before* the plist is written, since the plist's
`--config` argument points at it.
"""
from __future__ import annotations

import os
import stat

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

SENTINEL_EXECUTABLE_NAME = "sentinel-daemon"
SENTINEL_DEV_EXECUTABLE_NAME = "sentinel"
PLIST_TEMPLATE_NAME = "com.healthkeri.keriguard.sentinel.plist"


def _write_saas_config(settings, config_path: object | None = None) -> None:
    """Render and write the SaaS `sentinel start --config` YAML.

    Deliberately omits `bran`/`passcode` -- the daemon is launched with
    `--passcode-file`, which resolves `args.bran` before the config-file
    merge runs (sentinel/app/cli/commands/start.py:merge_config_and_args),
    so embedding the passcode in this YAML would be both redundant and a
    needless plaintext-secret-on-disk exposure.

    `config_path` defaults to the prod machine-singleton path
    (`keystore.SENTINEL_CONFIG_PATH`); dev-mode callers pass a per-vault
    path (`keystore.dev_sentinel_config_path`) so two vaults' dev sentinels
    don't clobber each other's config on disk.
    """
    from sentinel.core.initializing import SentinelConfig

    config = SentinelConfig()
    config.name = settings.sentinel_name
    config.alias = settings.sentinel_alias
    config.server_name = settings.server_name
    config.server_alias = settings.server_alias
    config.local = False
    config.uxd = True
    config.export_dir = settings.export_dir or str(keystore.DEFAULT_EXPORT_DIR)
    config.issuer.aid = settings.issuer_aid
    config.issuer.oobi = settings.issuer_oobi

    path = config_path or keystore.SENTINEL_CONFIG_PATH
    keystore.SENTINEL_DIR.mkdir(parents=True, exist_ok=True)
    config.save(str(path))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _sentinel_settings_valid(settings) -> bool:
    if not settings or not settings.sentinel_name or not settings.sentinel_alias:
        logger.warning("sentinel_launch: settings missing provisioned sentinel identity; skipping")
        return False
    return True


def _sentinel_values(executable, settings, config_path: object | None = None) -> dict[str, str]:
    return {
        "SENTINEL_EXECUTABLE": str(executable),
        "CONFIG_YAML": str(config_path or keystore.SENTINEL_CONFIG_PATH),
        "BASE": settings.server_base or keystore.SERVER_BASE,
        "SOCKET_DIR": str(keystore.SENTINEL_SOCKET_DIR),
        "SENTINEL_NAME": settings.sentinel_name,
        "SENTINEL_ALIAS": settings.sentinel_alias,
        "EXPORT_DIR": settings.export_dir or str(keystore.DEFAULT_EXPORT_DIR),
        "PASSCODE_FILE": str(keystore.BRAN_PATH),
        "STDOUT_LOG": str(keystore.LOGS_DIR / "sentinel.stdout.log"),
        "STDERR_LOG": str(keystore.LOGS_DIR / "sentinel.stderr.log"),
        "ARCHIMEDES_ENVIRONMENT": os.environ.get("ARCHIMEDES_ENVIRONMENT", ""),
        "LOCKSMITH_ENVIRONMENT": os.environ.get("LOCKSMITH_ENVIRONMENT", ""),
    }


def launch_sentinel_daemon(settings) -> bool:
    """Write the SaaS config + launchd plist for the sentinel daemon and
    bootstrap it via `launchctl`.

    No-op (returns False) on non-macOS or unfrozen (dev) runs, and if the
    settings don't yet carry a provisioned sentinel identity -- mirrors
    `launch_helper_app()`'s guard pattern. Safe to call repeatedly:
    `launchctl bootstrap` on an already-loaded label is treated as success.
    """
    if not is_frozen_macos():
        logger.info("Not a frozen macOS build; skipping sentinel daemon launch")
        return False

    if not _sentinel_settings_valid(settings):
        return False

    executable = find_daemon_executable(SENTINEL_EXECUTABLE_NAME)
    if executable is None:
        logger.warning(f"{SENTINEL_EXECUTABLE_NAME} not embedded; skipping sentinel daemon launch")
        return False

    template = find_plist_template(PLIST_TEMPLATE_NAME)
    if template is None:
        logger.warning(f"{PLIST_TEMPLATE_NAME} not found; skipping sentinel daemon launch")
        return False

    try:
        _write_saas_config(settings)
    except Exception:
        logger.exception("sentinel_launch: failed to write SaaS config.yaml")
        return False

    keystore.SENTINEL_SOCKET_DIR.mkdir(parents=True, exist_ok=True)

    try:
        rendered = render_plist(template, _sentinel_values(executable, settings))
    except KeyError:
        logger.exception("sentinel_launch: plist template rendering failed")
        return False

    plist_path = write_plist(keystore.SENTINEL_AGENT_LABEL, rendered)
    return bootstrap_agent(plist_path)


def launch_sentinel_daemon_dev(settings):
    """Dev-mode equivalent of `launch_sentinel_daemon`: spawns `sentinel
    start` as a plain child subprocess instead of a launchd agent. Renders
    the same plist template/values as prod and extracts its
    `ProgramArguments` (`program_args_from_plist`) so the two paths can
    never drift apart. Returns the `subprocess.Popen`, or `None` on failure
    or if guarded off (see `daemon_launch.should_use_dev_daemons`)."""
    if not _sentinel_settings_valid(settings):
        return None

    executable = find_dev_executable(SENTINEL_DEV_EXECUTABLE_NAME)
    if executable is None:
        logger.warning(f"{SENTINEL_DEV_EXECUTABLE_NAME!r} executable not found; skipping dev sentinel launch")
        return None

    template = find_plist_template(PLIST_TEMPLATE_NAME)
    if template is None:
        logger.warning(f"{PLIST_TEMPLATE_NAME} not found; skipping dev sentinel launch")
        return None

    config_path = keystore.dev_sentinel_config_path(settings.server_name)
    try:
        _write_saas_config(settings, config_path=config_path)
    except Exception:
        logger.exception("sentinel_launch: failed to write SaaS config.yaml (dev)")
        return None

    keystore.SENTINEL_SOCKET_DIR.mkdir(parents=True, exist_ok=True)

    try:
        rendered = render_plist(template, _sentinel_values(executable, settings, config_path=config_path))
        argv = program_args_from_plist(rendered)
    except Exception:
        logger.exception("sentinel_launch: dev argv rendering failed")
        return None

    return spawn_dev_daemon(
        keystore.dev_sentinel_agent_label(settings.server_name),
        argv,
        keystore.LOGS_DIR / f"sentinel.dev.{settings.server_name}.stdout.log",
        keystore.LOGS_DIR / f"sentinel.dev.{settings.server_name}.stderr.log",
    )


def stop_sentinel_daemon_dev(settings) -> None:
    stop_dev_daemon(keystore.dev_sentinel_agent_label(settings.server_name))


def is_sentinel_dev_running(settings) -> bool:
    return is_dev_daemon_running(keystore.dev_sentinel_agent_label(settings.server_name))


def stop_sentinel_daemon() -> bool:
    """Stop the sentinel launchd agent via `launchctl bootout`. Frozen macOS
    only -- mirrors `launch_sentinel_daemon`'s guard. See
    `guardian_launch.stop_guardian_daemon` for the machine-wide-singleton
    caveat, which applies identically here."""
    if not is_frozen_macos():
        return False
    return bootout_agent(keystore.SENTINEL_AGENT_LABEL)