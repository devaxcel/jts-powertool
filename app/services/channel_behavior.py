"""How the assistant behaves in a channel: solo vs group, batch mode and observe mode.

  - Solo channel (1 human + the bot, and every DM): the assistant answers every message, no tag needed.
  - Group channel (2+ humans + the bot): it answers only when tagged; everything else is people talking to each other.
  - Batch mode (solo): messages are collected and answered together as one input when the person sends `go`
    (or attaches a file). Good for multi-part tasks.
  - Observe mode (group): the assistant reads along with the conversation and, when finally tagged, remembers
    far more of what was said than usual.

Per channel an admin can also force the rule: "auto" (default, decided by who is in the channel),
"always" (answer every message) or "tagged" (answer only when tagged).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.db.session import get_db_connection

logger = logging.getLogger("channel_behavior")

RESPONSE_MODES = ("auto", "always", "tagged")
DEFAULTS: Dict[str, Any] = {"response_mode": "auto", "batch_mode": False, "observe_mode": False}
BATCH_TRIGGERS = {"go", "send", "run", "done", "/go"}
OBSERVE_CONTEXT_MESSAGES = 40  # how much conversation the assistant remembers in observe mode (normal: 6)

_BEHAVIOR_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}
_SOLO_CACHE: Dict[str, Tuple[bool, float]] = {}
_BEHAVIOR_TTL = 20.0
_SOLO_TTL = 300.0


def ensure_columns(cur) -> None:
    cur.execute("ALTER TABLE channel_metadata ADD COLUMN IF NOT EXISTS response_mode VARCHAR(10) DEFAULT 'auto';")
    cur.execute("ALTER TABLE channel_metadata ADD COLUMN IF NOT EXISTS batch_mode BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE channel_metadata ADD COLUMN IF NOT EXISTS observe_mode BOOLEAN DEFAULT FALSE;")


def _variants(channel_id: str) -> List[str]:
    from app.services.channel_secrets_service import channel_id_variants

    return channel_id_variants(channel_id)


def get_behavior(channel_id: str) -> Dict[str, Any]:
    """The channel's settings. Never raises: any problem means the defaults."""
    if not channel_id:
        return dict(DEFAULTS)
    key = str(channel_id).upper()
    hit = _BEHAVIOR_CACHE.get(key)
    if hit and time.time() < hit[1]:
        return dict(hit[0])
    result = dict(DEFAULTS)
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                ensure_columns(cur)
                conn.commit()
                cur.execute(
                    "SELECT response_mode, batch_mode, observe_mode FROM channel_metadata WHERE UPPER(channel_id) = ANY(%s) LIMIT 1;",
                    (_variants(channel_id),),
                )
                row = cur.fetchone()
                if row:
                    mode = (row["response_mode"] if isinstance(row, dict) else row[0]) or "auto"
                    result = {
                        "response_mode": mode if mode in RESPONSE_MODES else "auto",
                        "batch_mode": bool(row["batch_mode"] if isinstance(row, dict) else row[1]),
                        "observe_mode": bool(row["observe_mode"] if isinstance(row, dict) else row[2]),
                    }
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[BEHAVIOR] Could not read settings for {channel_id}: {e}")
    _BEHAVIOR_CACHE[key] = (result, time.time() + _BEHAVIOR_TTL)
    return dict(result)


def set_behavior(channel_id: str, *, response_mode: Optional[str] = None, batch_mode: Optional[bool] = None,
                 observe_mode: Optional[bool] = None, actor: str = "admin") -> Dict[str, Any]:
    from app.services.channel_secrets_service import canonical_channel_id, resolve_slack_channel_name

    if not channel_id or not channel_id.strip():
        raise ValueError("channel_id is required")
    if response_mode is not None and response_mode not in RESPONSE_MODES:
        raise ValueError("Reply rule must be automatic, always, or only when tagged.")
    channel_id = canonical_channel_id(channel_id.strip())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            ensure_columns(cur)
            cur.execute("SELECT channel_name FROM channel_metadata WHERE channel_id = %s;", (channel_id,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO channel_metadata (channel_id, channel_name, channel_type, updated_at) VALUES (%s, %s, %s, CURRENT_TIMESTAMP);",
                    (channel_id, resolve_slack_channel_name(channel_id), "dm" if channel_id.startswith("D") else "channel"),
                )
            sets, params = [], []
            for col, val in (("response_mode", response_mode), ("batch_mode", batch_mode), ("observe_mode", observe_mode)):
                if val is not None:
                    sets.append(f"{col} = %s")
                    params.append(val)
            if sets:
                cur.execute(f"UPDATE channel_metadata SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE channel_id = %s;", (*params, channel_id))
            conn.commit()
    finally:
        conn.close()
    _BEHAVIOR_CACHE.clear()
    try:
        from app.log_stream import emit_telemetry

        emit_telemetry(
            action="CHANNEL_BEHAVIOR_CHANGED", category="DATABASE", level="INFO", channel_id=channel_id,
            message=f"{actor} changed bot behaviour for {channel_id}: response_mode={response_mode}, batch_mode={batch_mode}, observe_mode={observe_mode}.",
        )
    except Exception:
        pass
    out = get_behavior(channel_id)
    out["channel_id"] = channel_id
    return out


async def is_solo_channel(token: str, channel_id: str, bot_user_ids: Optional[set] = None) -> bool:
    """True when exactly one human (plus bots) is in the channel. Unknown or too big means group (the safe default)."""
    if not token or not channel_id:
        return False
    key = str(channel_id).upper()
    hit = _SOLO_CACHE.get(key)
    if hit and time.time() < hit[1]:
        return hit[0]
    solo = False
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                "https://slack.com/api/conversations.members",
                headers={"Authorization": f"Bearer {token}"},
                params={"channel": channel_id, "limit": 200},
            )
        data = resp.json() if resp.status_code == 200 else {}
        if data.get("ok"):
            more = bool((data.get("response_metadata") or {}).get("next_cursor"))
            humans = [m for m in (data.get("members") or []) if m not in (bot_user_ids or set())]
            solo = (not more) and len(humans) <= 1
    except Exception as e:
        logger.debug(f"[BEHAVIOR] Could not count members of {channel_id}: {e}")
    _SOLO_CACHE[key] = (solo, time.time() + _SOLO_TTL)
    return solo


def should_answer(*, response_mode: str, is_dm: bool, is_tagged: bool, solo: bool) -> bool:
    """The reply rule. DMs are always solo."""
    if is_tagged:
        return True
    if response_mode == "tagged":
        return False
    if response_mode == "always":
        return True
    return bool(is_dm or solo)


def is_batch_trigger(text: str) -> bool:
    cleaned = re.sub(r"<@[A-Z0-9]+>", "", text or "").strip().lower().strip(" .!?")
    return cleaned in BATCH_TRIGGERS


def pending_batch(channel_id: str, thread_ts: str, user_id: str, exclude_message_ts: Optional[str] = None) -> List[str]:
    """This person's messages since the assistant last answered in this conversation, oldest first."""
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(id), 0) AS last_id FROM conversation_messages WHERE channel_id = %s AND thread_ts = %s AND role = 'assistant';",
                    (channel_id, thread_ts),
                )
                row = cur.fetchone()
                last_id = int((row["last_id"] if isinstance(row, dict) else row[0]) or 0)
                cur.execute(
                    "SELECT content, message_ts FROM conversation_messages WHERE channel_id = %s AND thread_ts = %s AND role = 'user' AND user_id = %s AND id > %s ORDER BY id ASC;",
                    (channel_id, thread_ts, user_id, last_id),
                )
                rows = cur.fetchall() or []
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[BATCH] Could not read the queued messages: {e}")
        return []
    out = []
    for r in rows:
        content = (r["content"] if isinstance(r, dict) else r[0]) or ""
        ts = r["message_ts"] if isinstance(r, dict) else r[1]
        if exclude_message_ts and ts == exclude_message_ts:
            continue
        if content.strip() and not is_batch_trigger(content):
            out.append(content.strip())
    return out
