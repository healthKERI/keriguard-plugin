# -*- encoding: utf-8 -*-
"""keriguard_user.core.daemon_watch — register the issuer AID with the
running sentinel daemon over its relocated Unix socket.

DAEMONS.md Phase 3a: "Have the plugin call `LocalWatcherConnector` directly
... rather than shelling out to `sentinel watcher add`". Mirrors the tail of
`kg guardian up` (keriguard/.../guardian/up.py:344-357), which does the same
`LocalWatcherConnector(hby, hab, sentinel_aid).watch(...)` round-trip after
starting the daemons, retrying because the daemon's uxd listener may not be
up yet immediately after `launchctl bootstrap` returns.
"""
from __future__ import annotations

import time

from keri import help

from . import keystore

logger = help.ogler.getLogger(__name__)

CONNECT_RETRIES = 12
CONNECT_RETRY_DELAY_S = 3.0
# The daemon is a PyInstaller onefile bootloader: every launch self-extracts
# its bundled Python.framework to a fresh temp dir before any application
# code (including the socket listener) runs. Observed cold-start latency
# during Phase 4.5 manual verification was ~17s before the socket appeared;
# the retry budget above (36s) keeps margin above that on slower disks.


def register_issuer_watch(settings) -> bool:
    """Open the guardian Habery, connect to the sentinel daemon's watcher  
    socket, and register the issuer AID (and the guardian's own AID) for
    watching. Blocking (KERI keystore open + socket I/O) -- callers must invoke via
    `loop.run_in_executor(None, ...)`. Safe to call repeatedly: watch
    requests are idempotent server-side (`sentinel/framework/watching.py`).
    """
    from keri.app import habbing
    from sentinel.framework.watching import LocalWatcherConnector

    if not settings or not settings.sentinel_aid or not settings.issuer_aid:
        logger.warning("daemon_watch: settings missing sentinel/issuer AID; skipping watch registration")
        return False

    base = settings.server_base or keystore.SERVER_BASE

    try:
        hby = habbing.Habery(
            name=settings.server_name, base=base, bran=keystore.load_or_create_bran(), temp=False
        )
    except Exception:
        logger.exception("daemon_watch: could not open guardian Habery")
        return False

    try:
        hab = hby.habByName(settings.server_alias)
        if hab is None:
            logger.warning(f"daemon_watch: guardian hab {settings.server_alias!r} not found")
            return False

        connector = LocalWatcherConnector(
            hby, hab, settings.sentinel_aid, socket_dir=str(keystore.SENTINEL_SOCKET_DIR)
        )

        for attempt in range(CONNECT_RETRIES):
            try:
                connector.watch(settings.issuer_aid, settings.issuer_oobi)
                connector.watch(hab.pre, None)
                logger.info(f"daemon_watch: registered watch for issuer {settings.issuer_aid[:16]}…")
                return True
            except ConnectionError as exc:
                logger.info(
                    f"daemon_watch: sentinel socket not ready (attempt {attempt + 1}/"
                    f"{CONNECT_RETRIES}): {exc}"
                )
                time.sleep(CONNECT_RETRY_DELAY_S)

        logger.warning("daemon_watch: sentinel socket never became reachable")
        return False
    except Exception:
        logger.exception("daemon_watch: watch registration failed")
        return False
    finally:
        hby.close()