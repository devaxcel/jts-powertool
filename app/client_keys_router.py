"""
"Keys & Connections": each client manages its own API keys (whole client or per Slack channel).

Key names are free text, so any service works; PROVIDERS below are only suggestions. A key the bot doesn't use
natively becomes usable when the client also gives its service URL and how the key is sent
(see app/services/client_api_service.py). Values go straight to AWS Secrets Manager and are never returned. JTS admins can manage any client;
Client Admins only their own; Team Members see status only.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth_router import require_session
from app.log_stream import emit_telemetry
from app.services import client_api_service as capi
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

# Suggestions only (any name is accepted). used_by_bot: the assistant uses this key natively.
# "connection" pre-fills the service URL and how the key is sent, so the assistant can call the service.
PROVIDERS: List[Dict[str, Any]] = [
    {"id": "anthropic", "label": "Anthropic (Claude)", "group": "AI models", "used_by_bot": True,
     "hint": "Starts with sk-ant-. Replies made with this key aren't billed by JTS."},
    {"id": "openai", "label": "OpenAI (ChatGPT)", "group": "AI models", "used_by_bot": False, "hint": "Starts with sk-"},
    {"id": "google_gemini", "label": "Google Gemini", "group": "AI models", "used_by_bot": False, "hint": "Google AI Studio key"},
    {"id": "mistral", "label": "Mistral AI", "group": "AI models", "used_by_bot": False, "hint": ""},
    {"id": "groq", "label": "Groq", "group": "AI models", "used_by_bot": False, "hint": ""},
    {"id": "elevenlabs", "label": "ElevenLabs (voice)", "group": "Voice & audio", "used_by_bot": False, "hint": ""},
    {"id": "deepgram", "label": "Deepgram (transcription)", "group": "Voice & audio", "used_by_bot": False, "hint": ""},
    {"id": "assemblyai", "label": "AssemblyAI (transcription)", "group": "Voice & audio", "used_by_bot": False, "hint": ""},
    {"id": "jira", "label": "Jira (API token)", "group": "Work tools", "used_by_bot": False, "hint": "Atlassian API token"},
    {"id": "google_api", "label": "Google API key", "group": "Work tools", "used_by_bot": False, "hint": ""},
    {"id": "hubspot", "label": "HubSpot", "group": "Work tools", "used_by_bot": False, "hint": "Private app token"},
    {"id": "sendgrid", "label": "SendGrid", "group": "Work tools", "used_by_bot": False, "hint": ""},
    {"id": "mailchimp", "label": "Mailchimp", "group": "Work tools", "used_by_bot": False, "hint": ""},
    {"id": "formspree", "label": "Formspree (website forms)", "group": "Work tools", "used_by_bot": False, "hint": ""},
]
_SUGGESTED_CONNECTIONS: Dict[str, Dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "auth_type": "bearer"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "auth_type": "bearer"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "auth_type": "bearer"},
    "google_gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta", "auth_type": "header", "auth_name": "x-goog-api-key"},
    "elevenlabs": {"base_url": "https://api.elevenlabs.io/v1", "auth_type": "header", "auth_name": "xi-api-key"},
    "assemblyai": {"base_url": "https://api.assemblyai.com/v2", "auth_type": "header", "auth_name": "Authorization"},
    "hubspot": {"base_url": "https://api.hubapi.com", "auth_type": "bearer"},
    "sendgrid": {"base_url": "https://api.sendgrid.com/v3", "auth_type": "bearer"},
}
for _p in PROVIDERS:
    _p["connection"] = _SUGGESTED_CONNECTIONS.get(_p["id"], {})
_RESERVED = INTERNAL_FOLDER_PROVIDERS | {"github"}  # GitHub is connected with the GitHub card, not a pasted key
MAX_VALUE_LEN = 8000
MAX_NAME_LEN = 50  # provider columns are VARCHAR(50)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


_LABEL_TO_ID = {_slug(p["label"]): p["id"] for p in PROVIDERS}


def _provider_label(provider: str) -> str:
    for p in PROVIDERS:
        if p["id"] == provider:
            return p["label"]
    if provider.startswith("custom_"):  # keys saved before names were free text
        provider = provider[len("custom_"):]
    return provider.replace("_", " ").strip().title() or provider


def _resolve_provider(provider: str, custom_name: Optional[str] = None) -> str:
    """Any name works. Known services (by id or label) keep their id so the bot recognises them."""
    raw = (provider or "").strip()
    if raw.lower() == "custom":  # older dashboard form
        raw = (custom_name or "").strip()
    slug = _slug(raw)
    if not slug:
        raise HTTPException(status_code=400, detail="Give the key a name, e.g. 'OpenAI' or 'CRM key'.")
    slug = _LABEL_TO_ID.get(slug, slug)
    if slug in _RESERVED or slug.startswith("github"):
        raise HTTPException(status_code=400, detail="GitHub is connected with the GitHub card, not with a key.")
    if len(slug) > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail=f"Keep the key name under {MAX_NAME_LEN} characters.")
    return slug


def _connection_details(payload: "SaveClientKey") -> Dict[str, str]:
    try:
        base_url = capi.normalize_base_url(payload.base_url)
        auth_type, auth_name = capi.check_auth(payload.auth_type, payload.auth_name)
    except capi.ClientApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "base_url": base_url,
        "auth_type": auth_type,
        "auth_name": auth_name,
        "description": (payload.description or "").strip()[:300],
    }


def _with_connection(item: Dict[str, Any], conn: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    conn = conn or {}
    return {
        **item,
        "base_url": conn.get("base_url") or "",
        "auth_type": conn.get("auth_type") or "bearer",
        "auth_name": conn.get("auth_name") or "",
        "description": conn.get("description") or "",
        # native: the bot uses it directly (Anthropic); api: callable via call_client_api; stored: no service URL yet
        "bot_use": "native" if item["provider"] == "anthropic" else ("api" if conn.get("base_url") else "stored"),
    }


def _save_details(folder_id: int, channel_id: str, provider: str, details: Dict[str, str], actor: str) -> None:
    try:
        capi.save_connection(folder_id=folder_id, channel_id=channel_id, provider=provider, updated_by=actor, **details)
    except Exception as e:
        logger.error(f"[CLIENT_KEYS] Saving connection details for {provider} failed: {e}")
        raise HTTPException(status_code=500, detail="The service details couldn't be saved. Please try again.")


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
    provider: str = Field(..., max_length=80)  # the key name, free text
    custom_name: Optional[str] = Field(None, max_length=80)
    value: Optional[str] = None  # empty = keep the saved key and only update the service details
    base_url: Optional[str] = Field(None, max_length=500)
    auth_type: Optional[str] = Field(None, max_length=20)
    auth_name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=300)


class SaveChannelKey(SaveClientKey):
    channel_id: str = Field(..., max_length=64)


@client_keys_router.get("")
def get_keys(request: Request, folder_id: Optional[int] = None):
    sc = _scope(request, folder_id, write=False)
    folder = _folder_or_404(sc["folder_id"])
    try:
        conns = capi.list_connections(sc["folder_id"])
    except Exception as e:
        logger.warning(f"[CLIENT_KEYS] Could not load service details: {e}")
        conns = {}
    client_keys = [
        _with_connection({**k, "label": _provider_label(k["provider"]), "scope": "client"}, conns.get(("", k["provider"])))
        for k in list_folder_api_keys(sc["folder_id"])
    ]
    channels = []
    for ch in folder.get("channels") or []:
        keys = []
        for k in list_channel_secrets(ch["channel_id"]):
            if k.get("status") != "active" or k.get("provider") in _RESERVED:
                continue
            conn = next((conns[(cid, k["provider"])] for cid in channel_id_variants(ch["channel_id"])
                         if (cid, k["provider"]) in conns), None)
            keys.append(_with_connection({
                "provider": k["provider"], "label": _provider_label(k["provider"]), "scope": "channel",
                "updated_by": k.get("updated_by"), "updated_at": k.get("updated_at"), "status": "ok",
            }, conn))
        channels.append({"channel_id": ch["channel_id"], "channel_name": ch.get("channel_name") or ch["channel_id"], "keys": keys})
    return {
        "folder": {"id": folder["id"], "name": folder["name"]},
        "can_edit": sc["can_edit"],
        "providers": PROVIDERS,  # suggestions; any name may be used
        "client_keys": client_keys,
        "channels": channels,
    }


@client_keys_router.put("/client")
def save_client_key(payload: SaveClientKey, request: Request):
    sc = _scope(request, payload.folder_id, write=True)
    _folder_or_404(sc["folder_id"])
    provider = _resolve_provider(payload.provider, payload.custom_name)
    details = _connection_details(payload)
    actor = sc["ctx"].get("username") or "admin"
    if (payload.value or "").strip():
        value = _check_value(provider, payload.value)
        try:
            store_folder_api_key(folder_id=sc["folder_id"], api_key=value, provider=provider, updated_by=actor)
        except Exception as e:
            logger.error(f"[CLIENT_KEYS] Saving {provider} for client {sc['folder_id']} failed: {e}")
            raise HTTPException(status_code=500, detail="The key couldn't be saved. Please try again.")
    elif not any(k["provider"] == provider for k in list_folder_api_keys(sc["folder_id"])):
        raise HTTPException(status_code=400, detail="Paste the key value.")
    _save_details(sc["folder_id"], "", provider, details, actor)
    _audit(sc["ctx"], "CLIENT_KEY_SAVED", f"{actor} saved the {_provider_label(provider)} key for client {sc['folder_id']}")
    return {"ok": True, "provider": provider, "message": f"{_provider_label(provider)} key saved."}


@client_keys_router.delete("/client/{provider}")
def delete_client_key(provider: str, request: Request, folder_id: Optional[int] = None):
    sc = _scope(request, folder_id, write=True)
    provider = provider.strip().lower()
    if provider in _RESERVED:
        raise HTTPException(status_code=400, detail="GitHub is managed with the GitHub card.")
    removed = delete_folder_api_key(sc["folder_id"], provider)
    if not removed:
        raise HTTPException(status_code=404, detail="That key wasn't found.")
    try:
        capi.delete_connection(sc["folder_id"], "", provider)
    except Exception as e:
        logger.warning(f"[CLIENT_KEYS] Could not remove service details for {provider}: {e}")
    actor = sc["ctx"].get("username") or "admin"
    _audit(sc["ctx"], "CLIENT_KEY_REMOVED", f"{actor} removed the {_provider_label(provider)} key for client {sc['folder_id']}")
    extra = " Replies now use the JTS key and are billed." if provider == "anthropic" else ""
    return {"ok": True, "message": f"{_provider_label(provider)} key removed.{extra}"}


@client_keys_router.put("/channel")
def save_channel_key(payload: SaveChannelKey, request: Request):
    sc = _scope(request, payload.folder_id, write=True)
    folder = _folder_or_404(sc["folder_id"])
    ch = _channel_in_folder(folder, payload.channel_id)
    provider = _resolve_provider(payload.provider, payload.custom_name)
    details = _connection_details(payload)
    actor = sc["ctx"].get("username") or "admin"
    if (payload.value or "").strip():
        value = _check_value(provider, payload.value)
        try:
            store_channel_secret(channel_id=ch["channel_id"], provider=provider, api_key=value,
                                 channel_name=ch.get("channel_name"), updated_by=actor)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error(f"[CLIENT_KEYS] Saving channel {provider} key failed: {e}")
            raise HTTPException(status_code=500, detail="The key couldn't be saved. Please try again.")
    elif not any(k.get("provider") == provider and k.get("status") == "active" for k in list_channel_secrets(ch["channel_id"])):
        raise HTTPException(status_code=400, detail="Paste the key value.")
    _save_details(sc["folder_id"], ch["channel_id"].upper(), provider, details, actor)
    _audit(sc["ctx"], "CHANNEL_KEY_SAVED",
           f"{actor} saved the {_provider_label(provider)} key for channel {ch.get('channel_name') or ch['channel_id']}")
    return {"ok": True, "message": f"{_provider_label(provider)} key saved for {ch.get('channel_name') or ch['channel_id']}."}


@client_keys_router.delete("/channel/{channel_id}/{provider}")
def delete_channel_key(channel_id: str, provider: str, request: Request, folder_id: Optional[int] = None):
    sc = _scope(request, folder_id, write=True)
    folder = _folder_or_404(sc["folder_id"])
    ch = _channel_in_folder(folder, channel_id)
    provider = provider.strip().lower()
    if provider in _RESERVED:
        raise HTTPException(status_code=400, detail="GitHub is managed with the GitHub card.")
    actor = sc["ctx"].get("username") or "admin"
    removed = delete_channel_secret(ch["channel_id"], provider, updated_by=actor)
    if not removed:
        raise HTTPException(status_code=404, detail="That key wasn't found.")
    try:
        capi.delete_connection(sc["folder_id"], ch["channel_id"].upper(), provider)
    except Exception as e:
        logger.warning(f"[CLIENT_KEYS] Could not remove service details for {provider}: {e}")
    _audit(sc["ctx"], "CHANNEL_KEY_REMOVED",
           f"{actor} removed the {_provider_label(provider)} key for channel {ch.get('channel_name') or ch['channel_id']}")
    return {"ok": True, "message": f"{_provider_label(provider)} key removed."}
