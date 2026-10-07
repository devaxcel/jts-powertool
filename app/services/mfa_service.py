"""Two-step verification (TOTP, the 6-digit codes of Google Authenticator / Microsoft Authenticator / Authy) for admin accounts.

  - Applies to JTS Admins and Client Admins (set MFA_REQUIRED_FOR_ADMINS=false to switch it off in an emergency).
  - The person scans a QR code once; from then on a 6-digit code is asked after the password.
  - 8 one-time recovery codes are shown once, for a lost phone.
  - Secrets are stored encrypted. Too many wrong codes lock the account for 15 minutes.
Nothing here needs an extra package: TOTP is RFC 6238 (HMAC-SHA1, 30 seconds, 6 digits).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from app.db.session import get_db_connection
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

ISSUER = "JTS PowerTool"
STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accept the code before and after the current one (clock drift)
RECOVERY_CODE_COUNT = 8
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
FAILURE_WINDOW_SECONDS = 15 * 60
ADMIN_ROLES = ("jts_admin", "client_admin", "admin")

_fail_lock = threading.Lock()
_failures: Dict[str, Tuple[int, float]] = {}  # username -> (count, first failure time)
_locked_until: Dict[str, float] = {}


# --------------------------------------------------------------------------- TOTP

def generate_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def totp_at(secret_b32: str, timestamp: Optional[float] = None) -> str:
    return _hotp(secret_b32, int((timestamp if timestamp is not None else time.time()) // STEP_SECONDS))


def match_step(secret_b32: str, code: str, now: Optional[float] = None) -> Optional[int]:
    """The time step the code belongs to (within the allowed window), or None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = int((now if now is not None else time.time()) // STEP_SECONDS)
    for delta in range(-WINDOW, WINDOW + 1):
        if hmac.compare_digest(_hotp(secret_b32, step + delta), code):
            return step + delta
    return None


def otpauth_uri(username: str, secret_b32: str) -> str:
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


# --------------------------------------------------------------------------- storage (encrypted)

def _fernet():
    from cryptography.fernet import Fernet

    base = (get_secret("MFA_ENCRYPTION_KEY", "") or get_secret("DASHBOARD_SECRET_KEY", "") or "jts-mfa").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"jts-mfa-v1:" + base).digest()))


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")


def _key(username: str) -> str:
    return (username or "").strip().lower()


def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_mfa (
                    username VARCHAR(255) PRIMARY KEY,
                    secret_enc TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT FALSE,
                    recovery_hashes TEXT,
                    last_step BIGINT DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    enabled_at TIMESTAMP WITH TIME ZONE
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


def _row(username: str) -> Optional[Dict[str, Any]]:
    def run(cur):
        cur.execute("SELECT * FROM user_mfa WHERE username = %s;", (_key(username),))
        r = cur.fetchone()
        return dict(r) if r else None

    return _db(run)


def is_enabled(username: str) -> bool:
    r = _row(username)
    return bool(r and r.get("enabled"))


def enabled_usernames() -> set:
    def run(cur):
        cur.execute("SELECT username FROM user_mfa WHERE enabled = TRUE;")
        return {(r["username"] if isinstance(r, dict) else r[0]) for r in cur.fetchall() or []}

    try:
        return _db(run)
    except Exception as e:
        logger.warning(f"[MFA] Could not list MFA users: {e}")
        return set()


def required_for(role: Optional[str]) -> bool:
    flag = str(get_secret("MFA_REQUIRED_FOR_ADMINS", "true") or "true").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return (role or "") in ADMIN_ROLES


# --------------------------------------------------------------------------- brute-force protection

def locked_seconds(username: str) -> int:
    with _fail_lock:
        until = _locked_until.get(_key(username), 0)
    return max(0, int(until - time.time()))


def _record_failure(username: str) -> None:
    k, now = _key(username), time.time()
    with _fail_lock:
        count, first = _failures.get(k, (0, now))
        if now - first > FAILURE_WINDOW_SECONDS:
            count, first = 0, now
        count += 1
        _failures[k] = (count, first)
        if count >= MAX_FAILURES:
            _locked_until[k] = now + LOCK_SECONDS
            _failures.pop(k, None)


def _clear_failures(username: str) -> None:
    with _fail_lock:
        _failures.pop(_key(username), None)
        _locked_until.pop(_key(username), None)


# --------------------------------------------------------------------------- setup / verify

class MfaError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def start_setup(username: str) -> Dict[str, str]:
    """Creates (or replaces) a not-yet-confirmed secret. Refused when MFA is already on."""
    existing = _row(username)
    if existing and existing.get("enabled"):
        raise MfaError("Two-step verification is already on for this account.")
    secret = generate_secret()

    def run(cur):
        cur.execute(
            """
            INSERT INTO user_mfa (username, secret_enc, enabled, recovery_hashes, last_step)
            VALUES (%s, %s, FALSE, NULL, 0)
            ON CONFLICT (username) DO UPDATE SET secret_enc = EXCLUDED.secret_enc, enabled = FALSE, recovery_hashes = NULL, last_step = 0;
            """,
            (_key(username), _encrypt(secret)),
        )

    _db(run)
    return {"secret": secret, "otpauth_uri": otpauth_uri(username, secret)}


def _new_recovery_codes() -> List[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(RECOVERY_CODE_COUNT)]


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode("utf-8")).hexdigest()


def confirm_setup(username: str, code: str) -> List[str]:
    """Checks the first code from the person's app, turns MFA on and returns the recovery codes (shown once)."""
    wait = locked_seconds(username)
    if wait:
        raise MfaError(f"Too many wrong codes. Try again in {wait // 60 + 1} minutes.", 429)
    r = _row(username)
    if not r or r.get("enabled"):
        raise MfaError("Start the setup again.")
    step = match_step(_decrypt(r["secret_enc"]), code)
    if step is None:
        _record_failure(username)
        raise MfaError("That code isn't right. Check the 6 digits in your authenticator app and try again.")
    codes = _new_recovery_codes()

    def run(cur):
        cur.execute(
            "UPDATE user_mfa SET enabled = TRUE, enabled_at = CURRENT_TIMESTAMP, last_step = %s, recovery_hashes = %s WHERE username = %s;",
            (step, json.dumps([_hash_recovery(c) for c in codes]), _key(username)),
        )

    _db(run)
    _clear_failures(username)
    return codes


def verify_login(username: str, code: str) -> bool:
    """A 6-digit app code (each can be used once) or a recovery code (consumed). Wrong codes count towards the lock."""
    wait = locked_seconds(username)
    if wait:
        raise MfaError(f"Too many wrong codes. Try again in {wait // 60 + 1} minutes.", 429)
    r = _row(username)
    if not r or not r.get("enabled"):
        raise MfaError("Two-step verification isn't set up for this account.")
    raw = (code or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == DIGITS and len(raw.replace(" ", "")) == DIGITS:
        step = match_step(_decrypt(r["secret_enc"]), digits)
        if step is not None and step > int(r.get("last_step") or 0):
            def run(cur):
                cur.execute("UPDATE user_mfa SET last_step = %s WHERE username = %s AND COALESCE(last_step, 0) < %s;", (step, _key(username), step))
                return cur.rowcount

            if _db(run):
                _clear_failures(username)
                return True
    else:
        hashes = json.loads(r.get("recovery_hashes") or "[]")
        h = _hash_recovery(raw)
        if h in hashes:
            hashes.remove(h)

            def run2(cur):
                cur.execute("UPDATE user_mfa SET recovery_hashes = %s WHERE username = %s;", (json.dumps(hashes), _key(username)))

            _db(run2)
            _clear_failures(username)
            return True
    _record_failure(username)
    return False


def recovery_codes_left(username: str) -> int:
    r = _row(username)
    try:
        return len(json.loads((r or {}).get("recovery_hashes") or "[]"))
    except ValueError:
        return 0


def reset(username: str) -> bool:
    """Removes a person's two-step verification (they set it up again at their next sign-in)."""
    def run(cur):
        cur.execute("DELETE FROM user_mfa WHERE username = %s;", (_key(username),))
        return cur.rowcount

    _clear_failures(username)
    return bool(_db(run))
