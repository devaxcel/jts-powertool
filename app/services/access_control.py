"""Time-limited access and immediate revocation for dashboard people.

  - An account can have an end date ("access ends on"): the contractor's access stops by itself at the end of that day (UTC).
  - An account can be disabled, or its sessions ended ("sign out everywhere"), and the effect is immediate:
    every request of an already signed-in person is checked against the account, so a deleted, disabled, expired or revoked
    person is refused on their very next click (a 3-second cache at most). Nothing waits for the 7-day token to run out.
  - The same rule applies to what a person can do from Slack (GitHub / Jira changes).
The built-in master admin (from the server settings) is not a stored account and is never locked out by this.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

SESSION_SECONDS = 7 * 24 * 60 * 60  # must match the session token lifetime
_TTL = 3.0
_cache: Dict[str, Tuple[Any, float]] = {}
_lock = threading.Lock()


def ensure_columns(cur) -> None:
    cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS disabled BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS access_expires_at DATE;")
    cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS sessions_valid_after TIMESTAMP WITH TIME ZONE;")


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def inactive_reason(disabled: Any, expires_on: Any, today: Optional[date] = None) -> Optional[str]:
    """Why the account can't be used right now ("disabled" / "expired"), or None."""
    if disabled:
        return "disabled"
    if expires_on:
        d = expires_on.date() if isinstance(expires_on, datetime) else expires_on
        if isinstance(d, str):
            try:
                d = date.fromisoformat(d[:10])
            except ValueError:
                return None
        if isinstance(d, date) and (today or today_utc()) > d:
            return "expired"
    return None


def reason_message(reason: str) -> str:
    return {
        "disabled": "This account has been disabled. Please contact your administrator.",
        "expired": "Your access has ended. Please contact your administrator if you still need it.",
        "revoked": "Your access has been ended. Please sign in again or contact your administrator.",
        "removed": "This account no longer exists. Please contact your administrator.",
        "idle": "You were signed out after a period of inactivity. Please sign in again.",
    }.get(reason, "Your access has ended. Please contact your administrator.")


def parse_expiry(value: Any) -> Optional[date]:
    """'2026-12-31' -> date. Empty -> None. A date in the past is refused (use disable instead)."""
    if value in (None, "", "null"):
        return None
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError("Please enter the end date like 2026-12-31.")
    if d < today_utc():
        raise ValueError("The end date is already in the past. To stop someone's access now, disable the account instead.")
    return d


def invalidate(username: Optional[str] = None) -> None:
    with _lock:
        if username:
            _cache.pop(username.strip().lower(), None)
        else:
            _cache.clear()


def _account(key: str):
    """(disabled, access_expires_at, sessions_valid_after) or the string 'removed'; cached for a few seconds. Raises on database errors."""
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() < hit[1]:
            return hit[0]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            ensure_columns(cur)
            conn.commit()
            cur.execute(
                "SELECT disabled, access_expires_at, sessions_valid_after FROM dashboard_users WHERE LOWER(TRIM(username)) = %s LIMIT 1;",
                (key,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    value = "removed" if not row else (
        (row["disabled"], row["access_expires_at"], row["sessions_valid_after"]) if isinstance(row, dict) else tuple(row)
    )
    with _lock:
        _cache[key] = (value, time.time() + _TTL)
    return value


def session_problem(username: str, issued_at: float, master_admin: str = "") -> Optional[str]:
    """None when a signed-in person may continue, else the reason ('removed', 'disabled', 'expired', 'revoked').
    A database hiccup lets the request through (the rest of the app needs the database anyway) and is logged."""
    key = (username or "").strip().lower()
    if not key or key == (master_admin or "").strip().lower():
        return None
    try:
        acct = _account(key)
    except Exception as e:
        logger.warning(f"[ACCESS] Could not check the account of '{key}': {type(e).__name__}")
        return None
    if acct == "removed":
        return "removed"
    disabled, expires_on, valid_after = acct
    reason = inactive_reason(disabled, expires_on)
    if reason:
        return reason
    if valid_after is not None:
        ts = valid_after.timestamp() if hasattr(valid_after, "timestamp") else float(valid_after)
        if issued_at < ts:
            return "revoked"
    return None


# --------------------------------------------------------------------------- admin actions

def revoke_sessions(cur, user_id: int) -> None:
    """Ends every signed-in session of this person (they can sign in again if the account is active)."""
    ensure_columns(cur)
    cur.execute("UPDATE dashboard_users SET sessions_valid_after = %s WHERE id = %s;", (datetime.now(timezone.utc), user_id))


def set_disabled(cur, user_id: int, disabled: bool) -> None:
    ensure_columns(cur)
    if disabled:
        cur.execute("UPDATE dashboard_users SET disabled = TRUE, sessions_valid_after = %s WHERE id = %s;", (datetime.now(timezone.utc), user_id))
    else:
        cur.execute("UPDATE dashboard_users SET disabled = FALSE WHERE id = %s;", (user_id,))


def set_expiry(cur, user_id: int, expires_on: Optional[date]) -> None:
    ensure_columns(cur)
    cur.execute("UPDATE dashboard_users SET access_expires_at = %s WHERE id = %s;", (expires_on, user_id))
