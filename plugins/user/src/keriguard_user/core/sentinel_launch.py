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
    bootstrap_agent,
    find_daemon_executable,
    find_plist_template,
    is_frozen_macos,
    render_plist,
    write_plist,
)

logger = help.ogler.getLogger(__name__)

SENTINEL_EXECUTABLE_NAME = "sentinel-daemon"
PLIST_TEMPLATE_NAME = "com.healthkeri.keriguard.sentinel.plist"


def _write_saas_config(settings) -> None:
    """Render and write the SaaS `sentinel start --config` YAML.

    Deliberately omits `bran`/`passcode` -- the daemon is launched with
    `--passcode-file`, which resolves `args.bran` before the config-file
    merge runs (sentinel/app/cli/commands/start.py:merge_config_and_args),
    so embedding the passcode in this YAML would be both redundant and a
    needless plaintext-secret-on-disk exposure.
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

    keystore.SENTINEL_DIR.mkdir(parents=True, exist_ok=True)
    config.save(str(keystore.SENTINEL_CONFIG_PATH))
    os.chmod(keystore.SENTINEL_CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)


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

    if not settings or not settings.sentinel_name or not settings.sentinel_alias:
        logger.warning("sentinel_launch: settings missing provisioned sentinel identity; skipping")
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

    values = {
        "SENTINEL_EXECUTABLE": str(executable),
        "CONFIG_YAML": str(keystore.SENTINEL_CONFIG_PATH),
        "BASE": settings.server_base or keystore.SERVER_BASE,
        "SOCKET_DIR": str(keystore.SENTINEL_SOCKET_DIR),
        "SENTINEL_NAME": settings.sentinel_name,
        "SENTINEL_ALIAS": settings.sentinel_alias,
        "EXPORT_DIR": settings.export_dir or str(keystore.DEFAULT_EXPORT_DIR),
        "PASSCODE_FILE": str(keystore.BRAN_PATH),
        "STDOUT_LOG": str(keystore.LOGS_DIR / "sentinel.stdout.log"),
        "STDERR_LOG": str(keystore.LOGS_DIR / "sentinel.stderr.log"),
    }

    try:
        rendered = render_plist(template, values)
    except KeyError:
        logger.exception("sentinel_launch: plist template rendering failed")
        return False

    plist_path = write_plist(keystore.SENTINEL_AGENT_LABEL, rendered)
    return bootstrap_agent(plist_path)