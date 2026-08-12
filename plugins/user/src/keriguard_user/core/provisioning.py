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
import random
from typing import Any

from keri import help, kering
from keri.app import connecting, habbing
from keri.core import parsing

from keriguard.core.initializing import load_oobi, load_schema
from keriguard.core.wireguarding import SCHEMA_OOBIS, Schema
from keriguard.db.basing import KERIGuardBaser

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
    issuer_oobi: str,
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

    `issuer_oobi` is resolved into both Haberies before either identity is
    registered with healthKERI -- required for the guardian's own Verifier
    and, more acutely, for the sentinel daemon's watch of the issuer AID to
    ever succeed at all (see the `add_watched_identifier` note inline).

    On success the returned dict also carries `witness_aid` and
    `guardian_oobi` -- the guardian's witness-mediated self-OOBI, built by
    hand after `connect_to_healthkeri` returns (that call itself returns
    nothing; see `kg guardian up`'s identical tail, up.py:251-280). Callers
    resolve `guardian_oobi` into the *vault's own* `hby` (mirroring the
    existing `issuer_aid`/`issuer_oobi` pattern) so the vault can
    independently track the guardian AID as its "interface" identity. It has
    already been resolved into `sentinel_hby` internally by this function --
    that's a separate Habery from the vault's own.

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

        # Resolve the issuer's OOBI into *both* Haberies up front
        issuer_aid = load_oobi(hby=server_hby, oobi=issuer_oobi, alias="issuer")
        load_oobi(hby=sentinel_hby, oobi=issuer_oobi, alias="issuer")

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

        # Mirrors `kg guardian up`'s tail exactly (up.py:251-280): pick one
        # of the guardian's now-rotated-in witnesses, fetch its HTTP
        # endpoint, and build the guardian's own witness-mediated OOBI by hand
        if not server_hab.kever.wits:
            raise kering.ConfigurationError(
                f"Server alias {alias!r} has no witnesses"
            )

        witness_aid = random.choice(server_hab.kever.wits)
        urls = server_hab.fetchUrls(
            eid=witness_aid, scheme=kering.Schemes.http
        ) or server_hab.fetchUrls(eid=witness_aid, scheme=kering.Schemes.https)
        if not urls:
            raise kering.ConfigurationError(
                f"unable to query witness {witness_aid}, no http endpoint"
            )
        url = (
            urls[kering.Schemes.https]
            if kering.Schemes.https in urls
            else urls[kering.Schemes.http]
        )
        guardian_oobi = f"{url.rstrip('/')}/oobi/{server_hab.pre}/witness"

        load_oobi(hby=sentinel_hby, oobi=guardian_oobi, alias=alias)
        connecting.Organizer(hby=sentinel_hby).update(
            pre=server_hab.pre, data=dict(alias=alias, oobi=guardian_oobi)
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

    # Mirrors `kg guardian up`'s tail (up.py:306,319): populate the issuer
    # record in the *guardian's own* KERIGuardBaser -- the same
    # name/base scope `kg guardian start` reopens (`guardian_launch.py`'s
    # BASE=settings.server_base or keystore.SERVER_BASE) -- not the vault's
    # kgb, which `setup/page.py` already populates separately for its own
    # (different-scoped) reads.
    try:
        kgb = KERIGuardBaser(name=name, base=keystore.SERVER_BASE)
        kgb.set_issuer(aid=issuer_aid, oobi=issuer_oobi)
        kgb.close()
    except Exception:
        logger.exception("bootstrap_server_identity: failed to set issuer on guardian KERIGuardBaser")

    return {
        "success": True,
        "server_aid": server_hab.pre,
        "sentinel_name": sentinel_name,
        "sentinel_alias": sentinel_alias,
        "sentinel_aid": sentinel_hab.pre,
        "witness_aid": witness_aid,
        "guardian_oobi": guardian_oobi,
    }
