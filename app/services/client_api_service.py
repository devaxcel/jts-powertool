"""
Generic client keys: any key a client saves in "Keys & Connections" can be used by the bot when the client also
says WHERE the key may be sent (the service URL) and HOW (bearer token, a header, or a query parameter).

Safety:
  - the key value is added here at request time and never shown to the model, Slack or the dashboard
  - requests only go to the saved service URL (https, same host, same base path, no redirects, no private IPs)
  - reading (GET) runs directly; anything that changes data (POST/PUT/PATCH/DELETE) needs a human approval
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from app.db.session import get_db_connection
from app.services.channel_secrets_service import (
    channel_id_variants,
    get_channel_secret_value,
    get_folder_api_key_value,
    get_folder_id_for_channel,
    list_channel_secrets,
    list_folder_api_keys,
)

logger = logging.getLogger(__name__)

AUTH_TYPES = {"bearer", "header", "query"}
READ_METHODS = {"GET"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_RESPONSE_CHARS = 12000
REQUEST_TIMEOUT = 20.0
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,80}$")
_PARAM_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")

_table_ready = False


class ClientApiError(Exception):
    pass


def _ensure_table(cur) -> None:
    global _table_ready
    if _table_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_key_connections (
            folder_id INTEGER NOT NULL REFERENCES channel_folders(id) ON DELETE CASCADE,
            channel_id VARCHAR(64) NOT NULL DEFAULT '',
            provider VARCHAR(50) NOT NULL,
            base_url TEXT,
            auth_type VARCHAR(20) NOT NULL DEFAULT 'bearer',
            auth_name VARCHAR(80),
            description TEXT,
            updated_by VARCHAR(255),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (folder_id, channel_id, provider)
        );
    """)
    _table_ready = True


def normalize_base_url(raw: Optional[str]) -> str:
    """'' when not given. Otherwise a clean https URL without credentials, query or fragment."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise ClientApiError("The service URL must start with https://")
    if not parts.hostname or parts.username or parts.password:
        raise ClientApiError("That service URL isn't valid.")
    if parts.query or parts.fragment:
        raise ClientApiError("Leave out anything after '?' or '#' in the service URL.")
    from app.tools.web_tools import is_safe_url
    ok, reason = is_safe_url(raw)
    if not ok:
        raise ClientApiError(f"That service URL can't be used: {reason}")
    netloc = parts.hostname.lower() + (f":{parts.port}" if parts.port else "")
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), "", ""))


def check_auth(auth_type: Optional[str], auth_name: Optional[str]) -> Tuple[str, str]:
    auth_type = (auth_type or "bearer").strip().lower()
    auth_name = (auth_name or "").strip()
    if auth_type not in AUTH_TYPES:
        raise ClientApiError("Choose how the key is sent: bearer token, a header, or a query parameter.")
    if auth_type == "bearer":
        return auth_type, ""
    if not auth_name:
        raise ClientApiError("Give the header or parameter name the service expects, e.g. X-API-Key or api_key.")
    pattern = _HEADER_NAME if auth_type == "header" else _PARAM_NAME
    if not pattern.match(auth_name):
        raise ClientApiError("That header or parameter name has characters that aren't allowed.")
    if auth_type == "header" and auth_name.lower() in ("host", "content-length", "cookie", "transfer-encoding"):
        raise ClientApiError("That header name can't be used for a key.")
    return auth_type, auth_name


def save_connection(*, folder_id: int, channel_id: str, provider: str, base_url: str, auth_type: str,
                    auth_name: str, description: str, updated_by: str) -> None:
    """Saves where/how a key is used. An empty URL and description removes the details."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            if not base_url and not description:
                cur.execute(
                    "DELETE FROM client_key_connections WHERE folder_id = %s AND channel_id = %s AND provider = %s;",
                    (folder_id, channel_id or "", provider),
                )
            else:
                cur.execute("""
                    INSERT INTO client_key_connections
                        (folder_id, channel_id, provider, base_url, auth_type, auth_name, description, updated_by, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (folder_id, channel_id, provider) DO UPDATE SET
                        base_url = EXCLUDED.base_url, auth_type = EXCLUDED.auth_type, auth_name = EXCLUDED.auth_name,
                        description = EXCLUDED.description, updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP;
                """, (folder_id, channel_id or "", provider, base_url or None, auth_type, auth_name or None,
                      description or None, updated_by))
            conn.commit()
    finally:
        conn.close()


def delete_connection(folder_id: int, channel_id: str, provider: str) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            cur.execute(
                "DELETE FROM client_key_connections WHERE folder_id = %s AND channel_id = %s AND provider = %s;",
                (folder_id, channel_id or "", provider),
            )
            conn.commit()
    finally:
        conn.close()


def list_connections(folder_id: int) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """{(channel_id or '', provider): {base_url, auth_type, auth_name, description}}"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            conn.commit()
            cur.execute("""
                SELECT channel_id, provider, base_url, auth_type, auth_name, description
                FROM client_key_connections WHERE folder_id = %s;
            """, (folder_id,))
            rows = cur.fetchall() or []
    finally:
        conn.close()
    return {
        ((r.get("channel_id") or "").upper(), r["provider"]): {
            "base_url": r.get("base_url") or "",
            "auth_type": r.get("auth_type") or "bearer",
            "auth_name": r.get("auth_name") or "",
            "description": r.get("description") or "",
        }
        for r in rows
    }


def services_for_channel(channel_id: str) -> List[Dict[str, Any]]:
    """Services the bot may call from this channel: keys with a service URL. A channel key wins over the client key."""
    folder_id = get_folder_id_for_channel(channel_id)
    if not folder_id:
        return []
    conns = list_connections(folder_id)
    if not conns:
        return []
    variants = set(channel_id_variants(channel_id))
    channel_keys = set()
    if any(cid in variants for cid, _ in conns):
        for cid in variants:
            try:
                channel_keys |= {s["provider"] for s in list_channel_secrets(cid) if s.get("status") == "active"}
            except Exception as e:
                logger.debug(f"[CLIENT_API] Could not list channel keys for {cid}: {e}")
    client_keys = {k["provider"] for k in list_folder_api_keys(folder_id)}

    services: Dict[str, Dict[str, Any]] = {}
    for (cid, provider), conn in conns.items():
        if not conn["base_url"]:
            continue
        if cid and cid in variants and provider in channel_keys:
            services[provider] = {**conn, "provider": provider, "scope": "channel", "folder_id": folder_id}
        elif not cid and provider in client_keys and provider not in services:
            services[provider] = {**conn, "provider": provider, "scope": "client", "folder_id": folder_id}
    return sorted(services.values(), key=lambda s: s["provider"])


def find_service(channel_id: str, name: str) -> Dict[str, Any]:
    name = (name or "").strip().lower()
    for s in services_for_channel(channel_id):
        if s["provider"] == name:
            return s
    raise ClientApiError(
        f"'{name}' isn't a connected service for this channel. A Client Admin can add the key and its service URL "
        "in the dashboard under Keys & Connections."
    )


def build_url(service: Dict[str, Any], path: str, query: Optional[Dict[str, Any]] = None) -> str:
    """Joins the path to the service URL and refuses anything that would leave it."""
    base = urlsplit(service["base_url"])
    path = (path or "").strip()
    if "://" in path or path.startswith("//") or "\\" in path:
        raise ClientApiError("Give only the path after the service URL, e.g. /v1/contacts.")
    path_part, _, path_query = path.partition("?")
    segments = [s for s in path_part.split("/") if s]
    if any(s in (".", "..") for s in segments):
        raise ClientApiError("That path isn't allowed.")
    full_path = base.path.rstrip("/") + ("/" + "/".join(segments) if segments else "")
    params: List[Tuple[str, str]] = []
    if path_query:
        from urllib.parse import parse_qsl
        params.extend(parse_qsl(path_query, keep_blank_values=True))
    for k, v in (query or {}).items():
        params.append((str(k), v if isinstance(v, str) else json.dumps(v)))
    if service.get("auth_type") == "query":
        params = [(k, v) for k, v in params if k != service.get("auth_name")]  # the model can't supply the key slot
    return urlunsplit(("https", base.netloc, full_path or "/", urlencode(params), ""))


def _key_value(service: Dict[str, Any], channel_id: str) -> str:
    if service["scope"] == "channel":
        value = get_channel_secret_value(channel_id, service["provider"])
    else:
        value = get_folder_api_key_value(service["folder_id"], service["provider"])
    if not value:
        raise ClientApiError(f"The {service['provider']} key couldn't be read. Ask the Client Admin to save it again.")
    return value


async def send_request(channel_id: str, service_name: str, method: str, path: str,
                       query: Optional[Dict[str, Any]] = None, body: Any = None) -> Tuple[str, bool]:
    """Performs the call with the stored key. Returns (text for the model / approval card, is_error)."""
    method = (method or "GET").strip().upper()
    if method not in READ_METHODS | WRITE_METHODS:
        return (f"Method {method} isn't allowed. Use GET, POST, PUT, PATCH or DELETE.", True)
    try:
        service = find_service(channel_id, service_name)
        url = build_url(service, path, query)
        from app.tools.web_tools import is_safe_url
        ok, reason = is_safe_url(url)
        if not ok:
            return (f"Blocked: {reason}", True)
        key = _key_value(service, channel_id)
    except ClientApiError as e:
        return (str(e), True)

    headers = {"Accept": "application/json, text/plain;q=0.9, */*;q=0.5", "User-Agent": "JTS-PowerTool"}
    params = None
    if service["auth_type"] == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif service["auth_type"] == "header":
        headers[service["auth_name"]] = key
    else:
        params = {service["auth_name"]: key}

    kwargs: Dict[str, Any] = {"headers": headers, "params": params}
    if body is not None and method in WRITE_METHODS:
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["content"] = str(body)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
            resp = await client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        return (f"{service_name} didn't answer within {int(REQUEST_TIMEOUT)} seconds.", True)
    except Exception as e:
        return (f"Couldn't reach {service_name}: {str(e).replace(key, '***')}", True)

    text = resp.text or ""
    if key:
        text = text.replace(key, "***")
    if len(text) > MAX_RESPONSE_CHARS:
        text = text[:MAX_RESPONSE_CHARS] + f"\n... [cut, {len(resp.text)} characters in total]"
    shown_url = url.split("?", 1)[0]
    if 300 <= resp.status_code < 400:
        return (f"{method} {shown_url} -> {resp.status_code} redirect (not followed for safety). Check the path.", True)
    is_error = resp.status_code >= 400
    logger.info(f"[CLIENT_API] {method} {shown_url} for channel {channel_id} -> {resp.status_code}")
    return (f"{method} {shown_url} -> HTTP {resp.status_code}\n\n{text or '(empty response)'}", is_error)


async def execute_approved_call(args: Dict[str, Any], channel_id: str) -> Tuple[str, bool]:
    """Runs an approved change request. Uses the stored service + path again (never a stored full URL)."""
    return await send_request(
        channel_id, args.get("service", ""), args.get("method", "POST"), args.get("path", ""),
        args.get("query") or None, args.get("body"),
    )
