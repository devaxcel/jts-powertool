import asyncio
import logging
import os

import httpx
import jwt


logger = logging.getLogger(__name__)


OPENID_CONFIG = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"


_jwks_client: jwt.PyJWKClient | None = None


async def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(OPENID_CONFIG)
            response.raise_for_status()
            jwks_uri = response.json()["jwks_uri"]
        _jwks_client = jwt.PyJWKClient(jwks_uri, cache_keys=True)
    return _jwks_client


async def verify_activity(auth_header: str, service_url: str) -> None:
    app_id = os.environ.get("MICROSOFT_APP_ID", "")
    if not app_id:
        raise ValueError("MICROSOFT_APP_ID is not set")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Missing bearer token")

    token = auth_header[len("Bearer ") :]
    jwks_client = await _get_jwks_client()
    signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=ISSUER,
        audience=app_id,
        leeway=300,
    )
    if claims.get("serviceurl", "").rstrip("/") != service_url.rstrip("/"):
        raise ValueError("serviceUrl does not match the token")
