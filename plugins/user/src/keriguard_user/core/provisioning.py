# -*- encoding: utf-8 -*-
"""keriguard_user.core.provisioning — bootstrap the headless machine identity pair.

Registers two new, non-delegated AIDs with healthKERI's team-server
auth-code flow (`POST /account/teams/servers`) by reusing sentinel's proven
`connect_to_healthkeri` call shape -- the same shape `kg guardian up` uses
(`keriguard/.../guardian/up.py:130-238`) -- rather than hand-building the
multipart request ourselves. See PLAN.md Phase 1c.

Two independent Haberies/AIDs, not one shared identity (PLAN.md Phase 1d,
**tested and rejected**): `scripts/habery_concurrency_test.py` drove two OS
processes racing to advance the *same* AID's KEL and found ~half of all
"successful" writes silently lost with zero raised exceptions (`Kevery`
drops stale-sn events from whichever process loses the race). A future
`sentinel start` process (KEL-watcher) and `kg guardian start` process
(WireGuard-peer) will each own one of these AIDs exclusively, so there is
never contention over a single KEL:

- `sentinel_hab` -- the primary/authenticating identity in
  `connect_to_healthkeri`'s call shape (`data["aid"]`, the required `kel`
  part); becomes the KEL-watcher role.
- `server_hab` -- the secondary identity (`data["server_aid"]`, the optional
  `server_kel` part); becomes the WireGuard-peer (guardian) role, and is the
  one that receives a witness when `witness=True`.

Both are non-delegated so key rotation never requires the human vault to
co-sign an interaction event -- the whole point is that these identities
keep working while the vault is closed/locked.
"""
from __future__ import annotations

import asyncio
from typing import Any

from keri import help
from keri.app import connecting, habbing
from keri.core import parsing

from . import keystore

logger = help.ogler.getLogger(__name__)


def _open_or_create_hab(bran: str, name: str, alias: str):
    """Open (or create) a Habery/hab pair under `keystore.SERVER_BASE`.

    Uses the default `headDirPath` -- the same one the human vault's own
    Haberies already use (locksmith's `CreateVaultDialog` never overrides it
    either) -- with `base=keystore.SERVER_BASE` as the differentiating
    segment. hio's `Filer.__init__` only rejects an *absolute* `base`; a
    relative, multi-segment value like this is exactly what
    `locksmith.ui.vaults.drawer`'s base-navigation is built to handle, and it
    keeps these Haberies reachable from the exact same location already
    proven to work under the sandboxed build -- no `--data-dir`/`headDirPath`
    override needed on either upstream CLI.
    """
    hby = habbing.Habery(name=name, base=keystore.SERVER_BASE, bran=bran, temp=False)
    hab = hby.habByName(alias)
    if hab is None:
        hab = hby.makeHab(
            name=alias,
            transferable=True,
            icount=1,
            isith="1",
            ncount=1,
            nsith="1",
            toad=0,
        )
    return hby, hab


def bootstrap_server_identity(
    bran: str,
    name: str,
    alias: str,
    auth_code: str,
    witness: bool = False,
) -> dict[str, Any]:
    """Create (or reopen) two dedicated, non-delegated Haberies/habs -- a
    `{name}` guardian (WireGuard-peer) identity and a `{name}-sentinel`
    KEL-watcher identity -- and register both with healthKERI as a new
    TeamServer via the auth-code flow, matching `kg guardian up`'s proven
    two-Habery call shape (`keriguard/.../guardian/up.py:130-238`).

    Performs blocking KERI/network calls -- callers must invoke this via
    `loop.run_in_executor(None, ...)` from an async context, the same
    pattern setup/page.py already uses for other blocking KERI calls (e.g.
    `load_oobi`). Never raises; failures come back as
    `{"success": False, "error": str}`.

    `auth_code` is used once, in-memory, to authenticate the registration
    request -- callers must not persist, log, or otherwise retain it.
    """
    sentinel_name = f"{name}-sentinel"
    sentinel_alias = f"{alias}-sentinel"

    try:
        server_hby, server_hab = _open_or_create_hab(bran, name, alias)
    except Exception as exc:
        logger.exception(f"bootstrap_server_identity: could not open Habery {name!r}: {exc}")
        return {"success": False, "error": str(exc)}

    try:
        sentinel_hby, sentinel_hab = _open_or_create_hab(bran, sentinel_name, sentinel_alias)
    except Exception as exc:
        logger.exception(f"bootstrap_server_identity: could not open Habery {sentinel_name!r}: {exc}")
        server_hby.close()
        return {"success": False, "error": str(exc)}

    try:
        # Cross-register the sentinel's inception event into the guardian's
        # own KEL view, mirroring `kg guardian up`'s
        # `parsing.Parser().parse(...)` + `Organizer.update(...)` step --
        # without this, the guardian identity's local db has no record that
        # the sentinel identity exists, which the two daemons will need once
        # Phase 3 wires them up to trust each other's signed traffic.
        icp = sentinel_hab.makeOwnEvent(sn=0)
        parsing.Parser().parse(ims=bytearray(icp), kvy=server_hab.kvy)
        connecting.Organizer(hby=server_hby).update(
            pre=sentinel_hab.pre, data=dict(alias=sentinel_alias)
        )

        from sentinel.framework.connecting import connect_to_healthkeri

        asyncio.run(
            connect_to_healthkeri(
                server_name=name,
                sentinel_hby=sentinel_hby,
                sentinel_hab=sentinel_hab,
                auth_key=auth_code,
                server_hby=server_hby,
                server_hab=server_hab,
                witness=witness,
            )
        )
    except Exception as exc:
        logger.exception(f"bootstrap_server_identity: registration with healthKERI failed: {exc}")
        return {"success": False, "error": str(exc)}
    finally:
        sentinel_hby.close()
        server_hby.close()

    logger.info(
        f"bootstrap_server_identity: registered guardian={server_hab.pre} "
        f"sentinel={sentinel_hab.pre}"
    )
    return {
        "success": True,
        "server_aid": server_hab.pre,
        "sentinel_name": sentinel_name,
        "sentinel_alias": sentinel_alias,
        "sentinel_aid": sentinel_hab.pre,
    }
