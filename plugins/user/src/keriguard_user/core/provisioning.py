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

from keriguard.core.initializing import load_oobi, load_schema
from keriguard.core.wireguarding import SCHEMA_OOBIS, Schema

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

    On success the returned dict also carries `witness_aid`/`witness_name`/
    `witness_oobi` for the guardian's witness (empty strings when
    `witness=False`) -- callers resolve this OOBI into the *vault's own*
    `hby` (mirroring the existing `issuer_aid`/`issuer_oobi` pattern) so the
    vault can independently track the guardian AID as its "interface"
    identity. `connect_to_healthkeri` already resolves this same OOBI into
    `server_hby` internally; that's a separate Habery from the vault's own.

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

    # Pin the credential schemas into both Haberies -- mirroring `kg guardian
    # up`'s `for hby in (sentinel_hby, keriguard_hby): load_schema(...)` step
    # (`up.py:170-180`). Without this, the guardian daemon's own `hby.db`
    # (reopened later via `--base plugins/keriguard-user`, i.e. `server_hby`)
    # never has the schemas the Verifier needs, and credential finalization
    # loops forever on `MissingSchemaError`.
    for hby in (sentinel_hby, server_hby):
        for schema_said in (Schema.INTERFACE_SCHEMA, Schema.CONNECTION_SCHEMA):
            try:
                if not load_schema(
                    hby=hby,
                    schema_oobi=SCHEMA_OOBIS[schema_said],
                    schema_said=schema_said,
                ):
                    logger.warning(
                        f"bootstrap_server_identity: failed to pin schema {schema_said} into {hby.name!r}"
                    )
            except Exception:
                logger.exception(
                    f"bootstrap_server_identity: error pinning schema {schema_said} into {hby.name!r}"
                )

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

        connect_result = asyncio.run(
            connect_to_healthkeri(
                server_name=name,
                sentinel_hby=sentinel_hby,
                sentinel_hab=sentinel_hab,
                auth_key=auth_code,
                server_hby=server_hby,
                server_hab=server_hab,
                witness=witness,
            )
        ) or {}

        # `connect_to_healthkeri` only resolves `witness_oobi` into
        # `server_hby` -- but it's `sentinel_hby` the running sentinel
        # daemon actually queries (`hby.db.locs`, via
        # `add_watched_identifier`, sentinel/core/watching.py:404-417) when
        # self-registering the guardian AID as a watched identifier at
        # startup. Without this, that registration fails forever with
        # "unable to query witness ..., no http endpoint" -- the loc/scheme
        # for the witness was never loaded into the Habery that matters.
        witness_oobi = connect_result.get("witness_oobi")
        if witness and witness_oobi:
            try:
                load_oobi(
                    sentinel_hby, witness_oobi, connect_result.get("witness_name") or "witness"
                )
            except Exception:
                logger.exception(
                    "bootstrap_server_identity: failed to load witness OOBI into sentinel_hby"
                )

        # ... and the reverse: without this, the sentinel identity's local db
        # has no record that the guardian identity exists, so any reply the
        # guardian daemon signs and sends to the sentinel daemon's watcher
        # socket (`daemon_watch.register_issuer_watch`) is unverifiable and
        # escrows forever ("escrowing without key state for signer") -- the
        # one-directional cross-registration above was not enough for the
        # daemons to trust each other's signed traffic in both directions.
        #
        # This must run *after* `connect_to_healthkeri` returns, not before:
        # when `witness=True`, that call rotates `server_hab` (sn 0 -> 1) via
        # `rotate_witness`, so registering only the sn=0 icp beforehand left
        # the sentinel daemon's kvy permanently unaware of the guardian's
        # post-rotation key state -- every reply the guardian signs afterward
        # (sn=1) escrowed forever, since nothing re-synced the rotation event.
        # Replay the guardian's *current* full KEL (icp + any rotation, e.g.
        # from `rotate_witness` above), not just the icp, via `clonePreIter`
        # -- `replyToOobi` only returns end-role reply messages, not KEL
        # events, so it would not actually convey the rotation.
        server_kel = bytearray()
        for msg in server_hab.db.clonePreIter(pre=server_hab.pre):
            server_kel.extend(msg)
        parsing.Parser().parse(ims=server_kel, kvy=sentinel_hab.kvy)
        connecting.Organizer(hby=sentinel_hby).update(
            pre=server_hab.pre, data=dict(alias=alias)
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
        # The guardian's own witness -- resolved into `server_hby` above by
        # `connect_to_healthkeri`. `witness_oobi` is the witness's *own*
        # self-OOBI (cid=witness_aid); it does not resolve the guardian's
        # KEL if loaded elsewhere. `guardian_oobi` is the witness-mediated
        # OOBI for `server_hab.pre` itself (cid=server_aid) -- callers must
        # `load_oobi` *this* one into `vault.hby` (mirroring the existing
        # issuer_aid/issuer_oobi pattern) to get the guardian AID's KEL into
        # the vault's own kevers, a precondition for watching it.
        "witness_aid": connect_result.get("witness_aid", ""),
        "witness_name": connect_result.get("witness_name", ""),
        "witness_oobi": connect_result.get("witness_oobi", ""),
        "guardian_oobi": connect_result.get("guardian_oobi", ""),
    }
