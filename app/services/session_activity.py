"""Inactivity timeout: a signed-in person who has been away for 15 minutes is signed out.

The browser reports real activity (mouse, keys, touch) with a small "heartbeat" at most once a minute while the person is at the
screen. Background refreshes of pages do NOT count, so an unattended dashboard can't keep itself alive. The server refuses every
request of a session whose last heartbeat is older than the timeout, so it also protects a session that someone copied.
Timeout: SESSION_IDLE_MINUTES (default 15). It applies to everyone, including the built-in admin.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

from app.db.session import get_db_connection
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

GRACE_SECONDS = 30  # the browser warns at 14 minutes and signs out at 15; the server allows a little slack
_CACHE_TTL = 3.0
_cache: Dict[str, Tuple[float, float]] = {}  # nonce -> (last_seen, checked_at)
_lock = threading.Lock()


def idle_seconds() -> int:
    try:
        return max(60, int(str(get_secret("SESSION_IDLE_MINUTES", "15") or 15).strip()) * 60)
    except ValueError:
        return 15 * 60


def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS session_activity (
                    nonce VARCHAR(64) PRIMARY KEY,
                    username VARCHAR(255),
                    last_seen DOUBLE PRECISION NOT NULL
                );
            """)
            result = fn(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def touch(nonce: str, username: str = "") -> None:
    """Records real activity (sign-in or a heartbeat). Never raises."""
    if not nonce:
        return
    now = time.time()

    def run(cur):
        cur.execute(
            "INSERT INTO session_activity (nonce, username, last_seen) VALUES (%s, %s, %s) ON CONFLICT (nonce) DO UPDATE SET last_seen = EXCLUDED.last_seen;",
            (nonce, username, now),
        )
        if int(now) % 50 == 0:  # tidy up old rows now and then
            cur.execute("DELETE FROM session_activity WHERE last_seen < %s;", (now - 8 * 24 * 3600,))

    with _lock:
        _cache[nonce] = (now, now)
    try:
        _db(run)
    except Exception as e:
        logger.warning(f"[IDLE] Could not record activity: {type(e).__name__}")


def _last_seen(nonce: str) -> Optional[float]:
    with _lock:
        hit = _cache.get(nonce)
        if hit and time.time() - hit[1] < _CACHE_TTL:
            return hit[0]

    def run(cur):
        cur.execute("SELECT last_seen FROM session_activity WHERE nonce = %s;", (nonce,))
        r = cur.fetchone()
        return float(r["last_seen"] if isinstance(r, dict) else r[0]) if r else None

    seen = _db(run)
    if seen is not None:
        with _lock:
            _cache[nonce] = (seen, time.time())
    return seen


def idle_problem(payload: Dict[str, Any]) -> Optional[str]:
    """'idle' when this session has been quiet for longer than the timeout. A session seen for the first time (for example one
    started before this feature existed) starts its clock now. A database problem never signs anyone out."""
    nonce = str(payload.get("nonce") or "")
    if not nonce:
        return None
    try:
        seen = _last_seen(nonce)
    except Exception as e:
        logger.warning(f"[IDLE] Could not check inactivity: {type(e).__name__}")
        return None
    if seen is None:
        touch(nonce, str(payload.get("user") or ""))
        return None
    if time.time() - seen > idle_seconds() + GRACE_SECONDS:
        return "idle"
    return None
