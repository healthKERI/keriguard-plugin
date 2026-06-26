# -*- encoding: utf-8 -*-
"""keriguard_admin.core.kering — Plugin-level credential issuance helpers."""
import asyncio
from datetime import datetime, UTC

from keri import help, kering as _kering
from keri.core import coring, serdering
from keri.db import dbing

from keriguard.core.kering import Issuer
from keriguard.core.wireguarding import Schema

logger = help.ogler.getLogger(__name__)


async def issue_connection_credential_by_saids(
    issuer: Issuer,
    iface1_said: str,
    peer1_config: dict,
    iface2_said: str,
    peer2_config: dict,
    conn_meta: dict,
    auths: dict | None = None,
):
    """
    Issue a connection credential by directly specifying both interface credential SAIDs.

    Unlike Issuer.issue_connection_credential, this does not resolve machines by
    connectionName alias — the interface SAIDs are provided by the UI. Both peer blocks
    receive the same conn_meta dict.
    """
    auths = auths or {}

    iface1_creder, *_ = issuer.rgy.reger.cloneCred(said=iface1_said)
    iface2_creder, *_ = issuer.rgy.reger.cloneCred(said=iface2_said)

    recipient = iface1_creder.attrib.get("i")
    registry = issuer.rgy.regs.get(iface1_creder.sad.get("ri"))
    if registry is None:
        raise ValueError(
            f"Registry for interface credential {iface1_said[:12]}… not found in local regery"
        )

    dt = datetime.now(UTC).isoformat()

    edges = {
        "d": "",
        "peer1": {
            "n": iface1_said,
            "s": Schema.INTERFACE_SCHEMA,
            "o": "NI2I",
            **peer1_config,
            "connectionMetadata": conn_meta,
        },
        "peer2": {
            "n": iface2_said,
            "s": Schema.INTERFACE_SCHEMA,
            "o": "NI2I",
            **peer2_config,
            "connectionMetadata": conn_meta,
        },
    }
    _, edges = coring.Saider.saidify(sad=edges)

    creder = issuer.credentialer.create(
        regname=registry.name,
        recp=recipient,
        schema=Schema.CONNECTION_SCHEMA,
        data={"dt": dt},
        source=edges,
        rules=None,
        private=True,
    )

    iserder = registry.issue(said=creder.said, dt=dt)

    rseal = dict(i=creder.said, s=iserder.ked["s"], d=iserder.said)
    anc = issuer.hab.interact(data=[rseal])
    aserder = serdering.SerderKERI(raw=anc)

    await issuer.receiptor.receipt(aserder.pre, aserder.sn, auths=auths)

    prefixer = coring.Prefixer(qb64=iserder.pre)
    seqner = coring.Seqner(sn=iserder.sn)

    try:
        issuer.verifier.processCredential(
            creder=creder,
            prefixer=prefixer,
            seqner=seqner,
            saider=coring.Saider(qb64=iserder.said),
        )
    except _kering.MissingRegistryError:
        pass

    issuer.registrar.issue(creder, iserder, aserder, auths=auths)

    snkey = dbing.snKey(creder.said, 0)
    while not issuer.rgy.reger.getTel(key=snkey):
        issuer.hab.kvy.processEscrows()
        issuer.rgy.processEscrows()
        issuer.credentialer.processEscrows()
        issuer.verifier.processEscrows()
        await asyncio.sleep(0.1)

    return creder