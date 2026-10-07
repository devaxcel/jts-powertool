"""Sign-in protection: lock an account after repeated wrong passwords, and slow down one address trying many accounts.

  - 5 wrong passwords in a row (within 15 minutes) lock that account for 15 minutes (LOGIN_MAX_FAILURES, LOGIN_LOCK_MINUTES).
  - While an account is locked even the right password is refused, so a guesser learns nothing.
  - One network address that fails 25 times is paused for 15 minutes as well (password spraying across many accounts).
  - A JTS Admin can unlock someone at once. A correct password (when not locked) clears the count.
Counts live in the database so they survive restarts and work with more than one server process.
A database problem never blocks sign-in (it is logged).
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

from app.db.session import get_db_connection
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

IP_MAX_FAILURES = 25


def _int(name: str, default: int) -> int:
    try:
        return max(1, int(str(get_secret(name, str(default)) or default).strip()))
    except ValueError:
        return default


def max_failures() -> int:
    return _int("LOGIN_MAX_FAILURES", 5)


def lock_seconds() -> int:
    return _int("LOGIN_LOCK_MINUTES", 15) * 60


def user_key(username: str) -> str:
    return "u:" + (username or "").strip().lower()[:120]


def ip_key(ip: str) -> str:
    return "i:" + (ip or "unknown")[:60]


def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS login_attempts (
                    key VARCHAR(140) PRIMARY KEY,
                    failures INTEGER DEFAULT 0,
                    first_ts DOUBLE PRECISION DEFAULT 0,
                    locked_until DOUBLE PRECISION DEFAULT 0
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


def locked_for(*keys: str) -> int:
    """Seconds left on the longest lock among these keys (0 = not locked). Never raises."""
    def run(cur):
        cur.execute("SELECT locked_until FROM login_attempts WHERE key = ANY(%s);", (list(keys),))
        return [float(r["locked_until"] if isinstance(r, dict) else r[0]) for r in cur.fetchall() or []]

    try:
        now = time.time()
        return max([0] + [int(u - now) + 1 for u in _db(run) if u > now])
    except Exception as e:
        logger.warning(f"[LOGIN] Could not read the lock state: {type(e).__name__}")
        return 0


def _record(cur, key: str, limit: int, now: float, lock_for: int) -> Tuple[int, bool]:
    """Counts one failure for the key. Returns (failures so far, locked now)."""
    window = lock_for  # failures older than one lock period no longer count
    cur.execute("SELECT failures, first_ts, locked_until FROM login_attempts WHERE key = %s;", (key,))
    r = cur.fetchone()
    failures, first_ts = 0, now
    if r:
        f = r["failures"] if isinstance(r, dict) else r[0]
        ft = r["first_ts"] if isinstance(r, dict) else r[1]
        if now - float(ft or 0) <= window:
            failures, first_ts = int(f or 0), float(ft)
    failures += 1
    locked_until = now + lock_for if failures >= limit else 0
    cur.execute(
        """
        INSERT INTO login_attempts (key, failures, first_ts, locked_until) VALUES (%s, %s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET failures = EXCLUDED.failures, first_ts = EXCLUDED.first_ts, locked_until = EXCLUDED.locked_until;
        """,
        (key, 0 if locked_until else failures, first_ts, locked_until),
    )
    return failures, bool(locked_until)


def record_failure(username: str, ip: str) -> Dict[str, int]:
    """One wrong password. Returns {'attempts_left': n, 'locked_seconds': s}. Never raises."""
    limit, lock = max_failures(), lock_seconds()
    out = {"attempts_left": limit, "locked_seconds": 0}

    def run(cur):
        now = time.time()
        failures, locked = _record(cur, user_key(username), limit, now, lock)
        _record(cur, ip_key(ip), IP_MAX_FAILURES, now, lock)
        return failures, locked

    try:
        failures, locked = _db(run)
        out["attempts_left"] = 0 if locked else max(0, limit - failures)
        out["locked_seconds"] = lock if locked else 0
        if locked:
            logger.warning(f"[LOGIN] Account '{(username or '').strip().lower()[:60]}' locked for {lock // 60} minutes after {limit} wrong passwords")
    except Exception as e:
        logger.warning(f"[LOGIN] Could not record a failed sign-in: {type(e).__name__}")
    return out


def clear(username: str) -> None:
    try:
        _db(lambda cur: cur.execute("DELETE FROM login_attempts WHERE key = %s;", (user_key(username),)))
    except Exception as e:
        logger.warning(f"[LOGIN] Could not clear the failure count: {type(e).__name__}")


def unlock(username: str) -> bool:
    """Admin action: clears the lock and the count for the account."""
    def run(cur):
        cur.execute("DELETE FROM login_attempts WHERE key = %s;", (user_key(username),))
        return cur.rowcount

    return bool(_db(run))


def locked_accounts() -> Dict[str, int]:
    """{username: seconds left} for every account locked right now (for the Users page)."""
    def run(cur):
        cur.execute("SELECT key, locked_until FROM login_attempts WHERE key LIKE 'u:%%' AND locked_until > %s;", (time.time(),))
        return [(r["key"] if isinstance(r, dict) else r[0], float(r["locked_until"] if isinstance(r, dict) else r[1])) for r in cur.fetchall() or []]

    try:
        now = time.time()
        return {k[2:]: int(u - now) + 1 for k, u in _db(run)}
    except Exception as e:
        logger.warning(f"[LOGIN] Could not list locked accounts: {type(e).__name__}")
        return {}


def lock_message(seconds: int) -> str:
    minutes = max(1, (seconds + 59) // 60)
    return (f"Too many wrong passwords. For your security, sign-in is locked for about {minutes} more minute{'s' if minutes != 1 else ''}. "
            "Try again then, or ask an administrator to unlock it.")
