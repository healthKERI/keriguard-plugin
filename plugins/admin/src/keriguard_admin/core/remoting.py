# -*- encoding: utf-8 -*-
"""keriguard.core.remoting — Remote API calls to the registrar."""
import json
import httpx
from keri import help, kering
from keri.app.httping import CESR_CONTENT_TYPE
from keri.core.serdering import SerderACDC

logger = help.ogler.getLogger(__name__)


async def push_credential_to_registrar(grant: bytes, registrar_url: str) -> None:
    """PUT CESR grant bytes to the registrar. Raises httpx.HTTPError on failure."""
    async with httpx.AsyncClient() as client:
        response = await client.put(
            registrar_url,
            content=grant,
            headers={"Content-Type": CESR_CONTENT_TYPE},
            timeout=30.0,
        )
        response.raise_for_status()
    logger.info(f"Credential pushed to registrar at {registrar_url} (HTTP {response.status_code})")


async def push_introduction_to_registrar(
    introduction: bytes, registrar_url: str
) -> None:
    """PUT CESR introduction bytes to the registrar."""
    async with httpx.AsyncClient() as client:
        response = await client.put(
            registrar_url,
            content=introduction,
            headers={"Content-Type": CESR_CONTENT_TYPE},
            timeout=30.0,
        )
        response.raise_for_status()
    logger.info(f"Introduction pushed to registrar at {registrar_url}")


async def push_credential_via_essr(
    grant: bytes,
    essr,
    credential: SerderACDC,
    introduction: bytes | None = None,
) -> None:
    """ PUT CESR grant bytes to hkweb's /registrar/ endpoint via ESSR with multipart body

    Parameters:
        grant (bytes): signed IPEX grant message
        essr (APIClient): ESSR client
        credential (SerderACDC): Credential to publish
        introduction(bytes): Signed IPEX introduction message

    Returns:

    """
    # Construct multipart body with grant bytes and credential SAID
    metadata_json = json.dumps({
        "said": credential.said,
        "issuer": credential.issuer,
        "schema": credential.schema,
        "publish": False
    })

    files = {
        'doc': ('doc', metadata_json, 'application/json'),
        'data': ('data', grant, CESR_CONTENT_TYPE)
    }

    response = await essr.request(
        path="/registrar",
        method="POST",
        files=files,
        timeout=30,
    )

    print(response.status_code)
    print(response.text)

    if response is None or response.status_code not in (200, 204):
        status = response.status_code if response else "None"
        raise RuntimeError(f"hkweb /registrar/ returned {status}")
    logger.info("Credential pushed to hkweb /registrar/ via ESSR")

    if introduction:
        response = await essr.request(
            path="/registrar/",
            method="PUT",
            data=introduction,
            headers={"Content-Type": CESR_CONTENT_TYPE},
            timeout=30,
        )
        if response is None or response.status_code not in (200, 204):
            status = response.status_code if response else "None"
            raise RuntimeError(f"hkweb /registrar/ introduction returned {status}")
        logger.info("Introduction pushed to hkweb /registrar/ via ESSR")


async def _ensure_issuer_watched(essr, hab, hby, account, team) -> None:
    """Register the issuing identifier as watched on the healthKERI SaaS platform.

    Idempotent — the platform's get_or_create_watched only provisions regional
    watchers once per AID regardless of how many times this is called.  Errors
    are logged but never propagated; a failed registration should not abort
    credential issuance.
    """
    if account is None or team is None or not getattr(team, "id", None):
        logger.debug("_ensure_issuer_watched: missing account or team, skipping")
        return

    # Derive a witness OOBI URL from the issuing hab's first known witness endpoint.
    oobi_url = None
    kever = hby.kevers.get(hab.pre)
    if kever:
        for wit in kever.wits:
            for scheme in (kering.Schemes.https, kering.Schemes.http):
                loc = hby.db.locs.get(keys=(wit, scheme))
                if loc and getattr(loc, "url", None):
                    oobi_url = f"{loc.url}/oobi/{hab.pre}/witness"
                    break
            if oobi_url:
                break

    if not oobi_url:
        logger.warning(
            f"_ensure_issuer_watched: no witness URL for {hab.pre[:16]}…, skipping"
        )
        return

    payload = {
        "aid": hab.pre,
        "name": hab.name,
        "oobi": oobi_url,
        "team_id": team.id,
        "account_id": account.aid,
    }

    try:
        response = await essr.request(
            path="/watched",
            method="POST",
            json=payload,
            timeout=30,
        )
        if response is None or response.status_code not in (200, 201, 204):
            status = response.status_code if response else "None"
            if response and response.status_code == 412:
                logger.warning(
                    f"_ensure_issuer_watched: watcher limit reached for team {team.id}"
                )
            elif response and response.status_code == 409:
                # Already watched by this team — idempotent, not an error.
                logger.debug(f"Issuer {hab.pre[:16]}… already watched by team {team.id}")
            else:
                logger.warning(f"_ensure_issuer_watched: POST /watched returned {status}")
        else:
            logger.info(f"Issuer {hab.pre[:16]}… registered as watched on healthKERI SaaS")
    except Exception as exc:
        logger.warning(f"_ensure_issuer_watched: {exc}")