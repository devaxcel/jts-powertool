"""
"Keys & Connections": each client manages its own API keys, the same way JTS admins do on the API keys page:
a key name (free text) and a value, for the whole client or for one Slack channel.

Values go straight to AWS Secrets Manager and are never returned. JTS admins can manage any client;
Client Admins only their own; Team Members see the list only.
Only the "anthropic" key is used by the assistant itself (it replaces the JTS key and usage isn't billed).
"""
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth_router import require_session
from app.log_stream import emit_telemetry
from app.services.channel_secrets_service import (
    INTERNAL_FOLDER_PROVIDERS,
    channel_id_variants,
    delete_channel_secret,
    delete_folder_api_key,
    get_channel_folder,
    list_channel_secrets,
    list_folder_api_keys,
    store_channel_secret,
    store_folder_api_key,
    validate_anthropic_key,
)

logger = logging.getLogger(__name__)
client_keys_router = APIRouter(prefix="/api/client-keys", tags=["Client keys"])

_RESERVED = INTERNAL_FOLDER_PROVIDERS | {"github"}  # GitHub is connected with the GitHub card, not a pasted key
MAX_VALUE_LEN = 8000
MAX_NAME_LEN = 50  # provider columns are VARCHAR(50)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def _label(provider: str) -> str:
    return "ANTHROPIC_API_KEY" if provider == "anthropic" else provider.upper()


def _resolve_name(name: str) -> str:
    slug = _slug(name)
    if slug in ("anthropic_api_key", "anthropic"):
        slug = "anthropic"
    if not slug:
        raise HTTPException(status_code=400, detail="Give the key a name, e.g. OPENAI_API_KEY.")
    if slug in _RESERVED or slug.startswith("github"):
        raise HTTPException(status_code=400, detail="GitHub and Jira are connected with their own cards above, not with a key.")
    if len(slug) > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"Keep the key name under {MAX_NAME_LEN} characters.")
    return slug


def _scope(request: Request, folder_id: Optional[int], write: bool) -> Dict[str, Any]:
    """Returns {ctx, folder_id, can_edit}. Client users are always pinned to their own client."""
    ctx = require_session(request)
    role = ctx.get("role")
    if role == "jts_admin":
        if not folder_id:
            raise HTTPException(status_code=400, detail="Choose a client.")
        return {"ctx": ctx, "folder_id": int(folder_id), "can_edit": True}
    if role in ("client_admin", "client_standard"):
        own = ctx.get("client_folder_id")
        if not own or int(own) < 1:
            raise HTTPException(status_code=403, detail="Your account isn't linked to a client yet. Ask your JTS administrator.")
        if write and role != "client_admin":
            raise HTTPException(status_code=403, detail="Only your Client Admin can add or remove keys.")
        return {"ctx": ctx, "folder_id": int(own), "can_edit": role == "client_admin"}
    raise HTTPException(status_code=403, detail="Not allowed.")


def _folder_or_404(folder_id: int) -> Dict[str, Any]:
    folder = get_channel_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="That client was not found.")
    return folder


def _channel_in_folder(folder: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
    wanted = set(channel_id_variants(channel_id))
    for ch in folder.get("channels") or []:
        if set(channel_id_variants(ch.get("channel_id", ""))) & wanted:
            return ch
    raise HTTPException(status_code=404, detail="That channel doesn't belong to this client.")


def _audit(ctx: Dict[str, Any], action: str, message: str) -> None:
    try:
        emit_telemetry(action=action, category="SECURITY", level="INFO", user_id=ctx.get("username"), message=message)
    except Exception as e:
        logger.debug(f"[CLIENT_KEYS] telemetry skipped: {e}")
    logger.info(f"[CLIENT_KEYS] {message}")


def _check_value(provider: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Paste the key value.")
    if len(value) > MAX_VALUE_LEN:
        raise HTTPException(status_code=400, detail="That value is too long.")
    if provider == "anthropic":
        if not value.startswith("sk-ant-"):
            raise HTTPException(status_code=400, detail="This doesn't look like an Anthropic API key (it should start with 'sk-ant-').")
        ok, msg = validate_anthropic_key(value)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    return value


class SaveClientKey(BaseModel):
    folder_id: Optional[int] = None
    name: str = Field(..., max_length=80)  # free text, e.g. OPENAI_API_KEY
    value: str


class SaveChannelKey(SaveClientKey):
    channel_id: str = Field(..., max_length=64)


@client_keys_router.get("")
def get_keys(request: Request, folder_id: Optional[int] = None):
    sc = _scope(request, folder_id, write=False)
    folder = _folder_or_404(sc["folder_id"])
    keys = [
        {
            "provider": k["provider"], "label": _label(k["provider"]), "applies_to": "client", "channel_id": None,
            "channel_name": None, "key_hint": k.get("key_hint"), "updated_by": k.get("updated_by"),
            "updated_at": k.get("updated_at"), "status": k.get("status", "ok"), "last_error": k.get("last_error"),
        }
        for k in list_folder_api_keys(sc["folder_id"])
    ]
    for ch in folder.get("channels") or []:
        for k in list_channel_secrets(ch["channel_id"]):
            if k.get("status") != "active" or k.get("provider") in _RESERVED:
                continue
            keys.append({
                "provider": k["provider"], "label": _label(k["provider"]), "applies_to": "channel",
                "channel_id": ch["channel_id"], "channel_name": ch.get("channel_name") or ch["channel_id"],
                "key_hint": None, "updated_by": k.get("updated_by"), "updated_at": k.get("updated_at"), "status": "ok",
                "last_error": None,
            })
    return {
        "folder": {"id": folder["id"], "name": folder["name"]},
        "can_edit": sc["can_edit"],
        "channels": [{"channel_id": c["channel_id"], "channel_name": c.get("channel_name") or c["channel_id"]}
                     for c in folder.get("channels") or []],
        "keys": keys,
    }


@client_keys_router.put("/client")
def save_client_key(payload: SaveClientKey, request: Request):
    sc = _scope(request, payload.folder_id, write=True)
    _folder_or_404(sc["folder_id"])
    provider = _resolve_name(payload.name)
    value = _check_value(provider, payload.value)
    actor = sc["ctx"].get("username") or "admin"
    try:
        store_folder_api_key(folder_id=sc["folder_id"], api_key=value, provider=provider, updated_by=actor)
    except Exception as e:
        logger.error(f"[CLIENT_KEYS] Saving {provider} for client {sc['folder_id']} failed: {e}")
        raise HTTPException(status_code=500, detail="The key couldn't be saved. Please try again.")
    _audit(sc["ctx"], "CLIENT_KEY_SAVED", f"{actor} saved the {_label(provider)} key for client {sc['folder_id']}")
    return {"ok": True, "provider": provider, "message": f"{_label(provider)} saved."}


@client_keys_router.delete("/client/{provider}")
def delete_client_key(provider: str, request: Request, folder_id: Optional[int] = None):
    sc = _scope(request, folder_id, write=True)
    provider = provider.strip().lower()
    if provider in _RESERVED:
        raise HTTPException(status_code=400, detail="GitHub and Jira are managed with their own cards.")
    if not delete_folder_api_key(sc["folder_id"], provider):
        raise HTTPException(status_code=404, detail="That key wasn't found.")
    actor = sc["ctx"].get("username") or "admin"
    _audit(sc["ctx"], "CLIENT_KEY_REMOVED", f"{actor} removed the {_label(provider)} key for client {sc['folder_id']}")
    extra = " Replies now use the JTS key and are billed." if provider == "anthropic" else ""
    return {"ok": True, "message": f"{_label(provider)} removed.{extra}"}


@client_keys_router.put("/channel")
def save_channel_key(payload: SaveChannelKey, request: Request):
    sc = _scope(request, payload.folder_id, write=True)
    folder = _folder_or_404(sc["folder_id"])
    ch = _channel_in_folder(folder, payload.channel_id)
    provider = _resolve_name(payload.name)
    value = _check_value(provider, payload.value)
    actor = sc["ctx"].get("username") or "admin"
    try:
        store_channel_secret(channel_id=ch["channel_id"], provider=provider, api_key=value,
                             channel_name=ch.get("channel_name"), updated_by=actor)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CLIENT_KEYS] Saving channel {provider} key failed: {e}")
        raise HTTPException(status_code=500, detail="The key couldn't be saved. Please try again.")
    where = ch.get("channel_name") or ch["channel_id"]
    _audit(sc["ctx"], "CHANNEL_KEY_SAVED", f"{actor} saved the {_label(provider)} key for channel {where}")
    return {"ok": True, "message": f"{_label(provider)} saved for {where}."}


@client_keys_router.delete("/channel/{channel_id}/{provider}")
def delete_channel_key(channel_id: str, provider: str, request: Request, folder_id: Optional[int] = None):
    sc = _scope(request, folder_id, write=True)
    folder = _folder_or_404(sc["folder_id"])
    ch = _channel_in_folder(folder, channel_id)
    provider = provider.strip().lower()
    if provider in _RESERVED:
        raise HTTPException(status_code=400, detail="GitHub is managed with the GitHub card.")
    actor = sc["ctx"].get("username") or "admin"
    if not delete_channel_secret(ch["channel_id"], provider, updated_by=actor):
        raise HTTPException(status_code=404, detail="That key wasn't found.")
    _audit(sc["ctx"], "CHANNEL_KEY_REMOVED", f"{actor} removed the {_label(provider)} key for channel {ch.get('channel_name') or ch['channel_id']}")
    return {"ok": True, "message": f"{_label(provider)} removed."}
