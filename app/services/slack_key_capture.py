"""
Save API keys pasted in Slack.

A message that is just `KEY_NAME = value` (one or several lines, optionally "@bot save key ...") is handled HERE, before
the message is stored, logged or sent to Claude:
  1. each key is saved to AWS Secrets Manager for the channel's client (or just that channel),
  2. the Slack message is deleted straight away (needs an admin/owner Slack user token, secret SLACK_USER_TOKEN),
  3. the bot confirms by NAME only; the value is never shown, logged or remembered.
Anyone in the client's channel may do this (decided by the JTS owner); every save is written to the activity log.
"""
import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

MAX_KEYS_PER_MESSAGE = 10
SAVE_TIMEOUT_SECONDS = 25.0
_MENTION = re.compile(r"<@[A-Z0-9]+>")
_LEAD = re.compile(r"^\s*(?:please\s+)?(?:save|add|store|set|update|replace)\s+(?:(?:this|the|my|these)\s+)?(?:api\s+)?(?:(?:keys?|secrets?|tokens?)(?![\w.\-])\s*)?[:\-]?\s*", re.I)
_CHANNEL_ONLY = re.compile(r"\b(?:only\s+)?(?:for|in)\s+this\s+channel(?:\s+only)?\b|\bchannel\s+only\b", re.I)
_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_.-]{1,49})\s*[=:]\s*(\S{8,8000})\s*$")
# The key word may be anywhere in the name as its own word: TEST_API_KEY, KEY_FOR_SHOPIFY, MY-SECRET-1 ...
_NAME_LOOKS_LIKE_KEY = re.compile(r"(?:^|[_.\-])(?:keys?|tokens?|secrets?|passwords?|passwd|credentials?)(?:$|[_.\-]|\d)", re.I)
# A value that is clearly an API key counts whatever the name is.
_VALUE_LOOKS_LIKE_KEY = re.compile(r"^(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|shpat_[A-Za-z0-9]{10,})")


def _clean_value(v: str) -> str:
    v = v.strip()
    # Slack may wrap in backticks / quotes / <...>; strip one layer of each
    for _ in range(2):
        v = v.strip("`'\"*_")
        if v.startswith("<") and v.endswith(">"):
            v = v[1:-1]
    return v.strip()


def parse_key_message(text: str) -> Optional[Dict[str, Any]]:
    """
    Returns {"entries": [(name, value), ...], "scope": "client" | "channel"} when the message is nothing but key lines,
    otherwise None (so ordinary chat is never touched).
    """
    if not text:
        return None
    body = _MENTION.sub("", text).strip()
    if not body:
        return None
    scope = "channel" if _CHANNEL_ONLY.search(body) else "client"
    body = _CHANNEL_ONLY.sub("", body)
    body = _LEAD.sub("", body, count=1)
    lines = [ln for ln in re.split(r"[\r\n]+", body) if ln.strip()]
    if not lines or len(lines) > MAX_KEYS_PER_MESSAGE:
        return None
    entries: List[Tuple[str, str]] = []
    for ln in lines:
        m = _LINE.match(ln.strip().strip("`"))
        if not m:
            return None
        name, value = m.group(1), _clean_value(m.group(2))
        if len(value) < 8 or re.search(r"\s", value):
            return None
        if not (_NAME_LOOKS_LIKE_KEY.search(name) or _VALUE_LOOKS_LIKE_KEY.match(value)):
            return None
        entries.append((name, value))
    return {"entries": entries, "scope": scope}


def _store_name(name: str, value: str) -> str:
    """The name to store under. An sk-ant- value named like an Anthropic key becomes the key the assistant uses."""
    if value.startswith("sk-ant-") and "anthropic" in name.lower():
        return "ANTHROPIC_API_KEY"
    return name


def save_entries(
    *, folder_id: Optional[int], channel_id: str, scope: str, entries: List[Tuple[str, str]], actor: str
) -> List[Dict[str, Any]]:
    """Saves each key. Returns [{name, label, ok, message}] with NO values."""
    from fastapi import HTTPException

    from app.client_keys_router import _channel_in_folder, _check_value, _label, _resolve_name
    from app.log_stream import emit_telemetry
    from app.services.channel_secrets_service import get_channel_folder, store_channel_secret, store_folder_api_key

    results: List[Dict[str, Any]] = []
    if not folder_id:
        return [{"name": n, "label": n.upper(), "ok": False,
                 "message": "This channel isn't linked to a client yet, so I can't store keys here. Ask your JTS administrator."}
                for n, _ in entries]
    folder = get_channel_folder(folder_id) or {}
    for raw_name, value in entries:
        shown = raw_name.upper()
        try:
            provider = _resolve_name(_store_name(raw_name, value))
            shown = _label(provider)
            checked = _check_value(provider, value)
            if scope == "channel":
                ch = _channel_in_folder(folder, channel_id)
                store_channel_secret(channel_id=ch["channel_id"], provider=provider, api_key=checked,
                                     channel_name=ch.get("channel_name"), updated_by=actor)
                where = f"channel {ch.get('channel_name') or ch['channel_id']}"
            else:
                store_folder_api_key(folder_id=folder_id, api_key=checked, provider=provider, updated_by=actor)
                where = f"client {folder.get('name') or folder_id}"
            results.append({"name": raw_name, "label": shown, "ok": True,
                            "message": f"*{shown}* saved for {where}" + (" (used for this client's replies)" if provider == "anthropic" else "")})
            try:
                emit_telemetry(action="SLACK_KEY_SAVED", category="SECURITY", level="INFO", user_id=actor,
                               message=f"{actor} saved the {shown} key for {where} from Slack (value never logged).")
            except Exception:
                pass
        except HTTPException as e:
            results.append({"name": raw_name, "label": shown, "ok": False, "message": f"*{shown}* wasn't saved: {e.detail}"})
        except ValueError as e:
            results.append({"name": raw_name, "label": shown, "ok": False, "message": f"*{shown}* wasn't saved: {e}"})
        except Exception as e:
            # AWS error text explains problems like missing permissions; the key value is masked out of it.
            detail = str(e).replace(value, "***")[:300] if isinstance(e, RuntimeError) else ""
            logger.error(f"[SLACK_KEYS] Saving {shown} failed: {type(e).__name__} {detail}".strip())
            results.append({"name": raw_name, "label": shown, "ok": False, "message": f"*{shown}* wasn't saved because of a server error. Please try again."})
    return results


async def delete_message(channel_id: str, ts: str) -> Tuple[bool, str]:
    """Deletes the user's message with an admin/owner user token. Returns (deleted, reason_if_not)."""
    token = (get_secret("SLACK_USER_TOKEN", "") or "").strip()
    if not token:
        return False, "no_user_token"
    if not channel_id or not ts:
        return False, "no_message"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://slack.com/api/chat.delete",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel_id, "ts": ts},
            )
        data = resp.json()
        if data.get("ok"):
            return True, ""
        err = str(data.get("error") or "unknown")
        # Scope names are not secret: log them so a wrong token is easy to diagnose.
        logger.warning(
            f"[SLACK_KEYS] chat.delete refused: error={err} needed={data.get('needed')} provided={data.get('provided')} "
            f"token_scopes={resp.headers.get('x-oauth-scopes')} token_type={token[:5]}"
        )
        return False, err
    except Exception as e:
        return False, type(e).__name__


async def post_reply(token: str, channel_id: str, thread_ts: Optional[str], text: str) -> None:
    if not token or not channel_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel_id, "thread_ts": thread_ts, "text": text},
            )
    except Exception as e:
        logger.warning(f"[SLACK_KEYS] Could not post the confirmation: {type(e).__name__}")


async def handle_key_message(
    *, parsed: Dict[str, Any], channel_id: str, slack_channel_id: str, message_ts: str, thread_ts: Optional[str],
    bot_token: str, folder_id: Optional[int], actor: str,
) -> Dict[str, Any]:
    """Saves + deletes + confirms. Returns {"names": [...], "deleted": bool} (no values)."""
    names = [n.upper() for n, _ in parsed["entries"]]
    logger.info(f"[SLACK_KEYS] Start: {len(names)} key(s) {names} scope={parsed['scope']} folder={folder_id} by {actor}")

    async def _save():
        # AWS can be slow or unreachable: never hang the message. The thread can't be cancelled, but we stop waiting.
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(save_entries, folder_id=folder_id, channel_id=channel_id, scope=parsed["scope"],
                                  entries=parsed["entries"], actor=actor),
                timeout=SAVE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(f"[SLACK_KEYS] Saving timed out after {SAVE_TIMEOUT_SECONDS}s (AWS Secrets Manager or the database is slow)")
            return [{"name": n, "label": n.upper(), "ok": False,
                     "message": f"*{n.upper()}* wasn't saved: the secret store didn't answer in time. Please try again."}
                    for n, _ in parsed["entries"]]

    # Delete in parallel with saving: the value should leave the channel as quickly as possible.
    (results, (deleted, why)) = await asyncio.gather(_save(), delete_message(slack_channel_id, message_ts))
    logger.info(f"[SLACK_KEYS] Saved={sum(1 for r in results if r['ok'])}/{len(results)} deleted={deleted} reason={why or '-'}")

    lines = [("✅ " if r["ok"] else "⚠️ ") + r["message"] for r in results]
    if deleted:
        lines.append("_Your message with the key was deleted. The values are hidden._")
    else:
        hint = {
            "no_user_token": "I can't delete messages yet (ask your JTS administrator to set that up)",
            "cant_delete_message": "I'm not allowed to delete that message",
            "message_not_found": "I couldn't find your message to delete",
        }.get(why, "I couldn't delete your message")
        lines.append(f"🔒 *Please delete your message with the key now.* {hint}.")
        logger.warning(f"[SLACK_KEYS] Message with a key was NOT deleted (reason={why}).")
    await post_reply(bot_token, slack_channel_id, thread_ts, "\n".join(lines))
    logger.info("[SLACK_KEYS] Confirmation posted")
    return {"names": [r["label"] for r in results], "deleted": deleted, "saved": sum(1 for r in results if r["ok"])}
