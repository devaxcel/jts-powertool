import os
import time
from typing import Any, Dict

import httpx


_token = None
_token_expires_at = 0.0


async def _get_token(app_id: str, app_password: str) -> str:
    global _token, _token_expires_at
    if _token and time.monotonic() < _token_expires_at:
        return _token

    # "botframework.com" for a MultiTenant bot, your tenant guid for SingleTenant.
    tenant = os.environ.get("MICROSOFT_APP_TENANT_ID", "botframework.com")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_password,
                "scope": "https://api.botframework.com/.default",
            },
        )
        response.raise_for_status()
        payload = response.json()

    _token = payload["access_token"]
    _token_expires_at = time.monotonic() + payload.get("expires_in", 3600) - 60
    return _token


async def send_activity(
    service_url: str, conversation_id: str, activity: Dict[str, Any]
) -> None:
    app_id = os.environ.get("MICROSOFT_APP_ID", "")
    app_password = os.environ.get("MICROSOFT_APP_PASSWORD", "")

    headers = {"Authorization": f"Bearer {await _get_token(app_id, app_password)}"}
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=activity, headers=headers)
        response.raise_for_status()
