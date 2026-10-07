"""
Dashboard Authentication & User Management Router for JTS-PowerTool Console.
Provides multi-tenant Role-Based Access Control (RBAC):
- JTS Admin: Global access, key vault, database, context inspector, user impersonation
- Client Admin: Scoped to client folder, manage team members, client billing & approvals
- Client Standard: Task portal, assigned channels, personal task approvals
"""
from app.services.tool_permissions import clean_permissions, effective_permissions
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Response, Request, status, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool

from app.tools.secrets_manager import get_secret
from app.db.session import get_db_connection
from app.services.email_service import send_password_setup_email

load_dotenv()
logger = logging.getLogger(__name__)

auth_router = APIRouter(tags=["Dashboard Auth & Users"])
users_router = APIRouter(prefix="/api/users", tags=["Users"])

# Secret key for signing session tokens
_SECRET_KEY = get_secret(
    "DASHBOARD_SECRET_KEY",
    os.getenv("DASHBOARD_SECRET_KEY", "jts-dashboard-super-secure-token-signing-key-2026")
).encode("utf-8")

SESSION_COOKIE_NAME = "jts_session"
# Folder id used for client logins that aren't linked to a client yet (no folder has this id).
UNASSIGNED_CLIENT_FOLDER = -1
SESSION_DURATION_SECONDS = 7 * 24 * 60 * 60  # 7 days


def get_admin_credentials() -> tuple[str, str]:
    """Retrieves expected dashboard admin username and password from environment/secrets."""
    user = get_secret("DASHBOARD_ADMIN_USER", os.getenv("DASHBOARD_ADMIN_USER", "admin")).strip()
    pwd = get_secret("DASHBOARD_ADMIN_PASSWORD", os.getenv("DASHBOARD_ADMIN_PASSWORD", "JTSAdmin#2026!")).strip()
    return user, pwd


def hash_password(password: str) -> str:
    """Hashes password with SHA-256 for basic security."""
    return hashlib.sha256(password.strip().encode("utf-8")).hexdigest()


def create_session_token(
    username: str,
    role: str = "jts_admin",
    client_folder_id: Optional[int] = None
) -> str:
    """Generates an HMAC-signed session token with expiration and role context."""
    payload = {
        "user": username,
        "role": role,
        "client_folder_id": client_folder_id,
        "exp": int(time.time()) + SESSION_DURATION_SECONDS,
        "nonce": secrets.token_hex(8),
    }
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")

    signature = hmac.new(_SECRET_KEY, b64_payload.encode("utf-8"), hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    return f"{b64_payload}.{b64_sig}"


MFA_TOKEN_SECONDS = 10 * 60


def create_mfa_token(username: str, role: str, client_folder_id: Optional[int] = None) -> str:
    """Short-lived token proving the password step was passed. It is NOT a session (verify_session_token rejects it)."""
    payload = {
        "purpose": "mfa", "user": username, "role": role, "client_folder_id": client_folder_id,
        "exp": int(time.time()) + MFA_TOKEN_SECONDS, "nonce": secrets.token_hex(8),
    }
    b64_payload = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode("utf-8")).decode("utf-8").rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(_SECRET_KEY, b64_payload.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8").rstrip("=")
    return f"{b64_payload}.{sig}"


def verify_mfa_token(token: str) -> Optional[dict]:
    payload = _decode_token(token)
    return payload if payload and payload.get("purpose") == "mfa" else None


def verify_session_token(token: str) -> Optional[dict]:
    """A real session token only. Tokens made for another purpose (the two-step sign-in token) are rejected."""
    payload = _decode_token(token)
    if payload and payload.get("purpose"):
        return None
    return payload


def _decode_token(token: str) -> Optional[dict]:
    """Verifies HMAC signature and expiration of a signed token. Returns payload dict or None."""
    if not token or "." not in token:
        return None

    try:
        parts = token.strip().split(".")
        if len(parts) != 2:
            return None

        b64_payload, b64_sig = parts

        expected_sig = hmac.new(_SECRET_KEY, b64_payload.encode("utf-8"), hashlib.sha256).digest()
        actual_sig = base64.urlsafe_b64decode(b64_sig + "=" * (-len(b64_sig) % 4))

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_json = base64.urlsafe_b64decode(b64_payload + "=" * (-len(b64_payload) % 4)).decode("utf-8")
        payload = json.loads(payload_json)

        if time.time() > payload.get("exp", 0):
            return None

        return payload
    except Exception as e:
        logger.debug(f"[AUTH] Token verification failed: {e}")
        return None


# --- Request / Response Models ---
class LoginRequest(BaseModel):
    username: str = Field(..., description="User ID or Email")
    password: str = Field(..., description="Password")


class UserResponse(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    username: str
    email: Optional[str] = None
    role: str
    timezone: Optional[str] = "UTC"
    client_folder_id: Optional[int] = None
    client_folder_name: Optional[str] = None
    organization_id: Optional[int] = None
    organization_name: Optional[str] = None


class LoginResponse(BaseModel):
    status: str
    message: str
    token: str
    user: UserResponse
    recovery_codes: Optional[List[str]] = None


class CreateUserRequest(BaseModel):
    name: str = Field(..., description="Full Name of user")
    username: str = Field(..., description="Username / User ID")
    email: str = Field(..., description="Email Address (must be unique)")
    password: Optional[str] = Field(default="", description="Password (optional, if omitted user receives an email with setup link)")
    role: str = Field("client_admin", description="Access role (client_admin or jts_admin)")
    timezone: Optional[str] = Field(default=None, description="Preferred timezone (defaults to global system timezone if omitted)")
    client_folder_id: Optional[int] = None
    organization_id: Optional[int] = None
    tool_permissions: Optional[Dict[str, StrictBool]] = Field(default=None, description="GitHub / Jira permission checkboxes")


class UpdateUserRequest(BaseModel):
    name: str = Field(..., description="Full Name of user")
    email: str = Field(..., description="Email Address (must be unique)")
    password: Optional[str] = Field(None, description="New Password (optional, leave empty to retain current password)")
    role: str = Field("client_admin", description="Access role (client_admin or jts_admin)")
    timezone: Optional[str] = Field(None, description="Preferred timezone")
    client_folder_id: Optional[int] = None
    organization_id: Optional[int] = None
    tool_permissions: Optional[Dict[str, StrictBool]] = Field(default=None, description="GitHub / Jira permission checkboxes")


class UpdateProfileRequest(BaseModel):
    name: str = Field(..., description="Full Name of user")
    email: Optional[str] = Field(None, description="Email Address (must be unique)")
    password: Optional[str] = Field(None, description="New Password (optional, leave empty to retain current password)")
    timezone: Optional[str] = Field(None, description="Personal timezone preference (e.g. UTC, Asia/Karachi, America/New_York)")


class SetPasswordRequest(BaseModel):
    token: str = Field(..., description="Password setup token")
    password: str = Field(..., description="New password (at least 6 characters)")
    confirm_password: Optional[str] = Field(None, description="Confirm new password")


class VerifyTokenResponse(BaseModel):
    valid: bool
    username: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    message: Optional[str] = None


# --- Helper Functions ---
def get_user_context(request: Request, ignore_simulation: bool = False) -> dict:
    """
    Extracts authenticated user context from authorization header or session cookie.
    Authorization header takes precedence over cookie for tab isolation.
    Also supports X-JTS-Simulated-Role for JTS Admin role previewing.
    If ignore_simulation is True, returns the user's actual authenticated role.
    """
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()

    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)

    sim_role = request.headers.get("X-JTS-Simulated-Role", "").strip().lower()

    if token:
        payload = verify_session_token(token)
        if payload:
            user = payload.get("user", "admin")
            actual_role = payload.get("role", "jts_admin")
            client_folder_id = payload.get("client_folder_id")

            # If folder_id missing for client role, lookup user in DB
            if actual_role in ("client_admin", "client_standard") and not client_folder_id:
                db_u = get_user_from_db(user)
                if db_u and db_u.get("client_folder_id"):
                    client_folder_id = db_u["client_folder_id"]

            # Clients can be limited to approved networks (JTS Admins never are)
            if actual_role in ("client_admin", "client_standard"):
                from app.services import ip_allowlist

                ip_allowlist.enforce(request, actual_role, client_folder_id)

            # Allow JTS Admin to simulate other roles in UI preview mode unless ignore_simulation is set
            if not ignore_simulation and actual_role == "jts_admin" and sim_role:
                if sim_role in ("client_admin", "client_standard"):
                    return {
                        "username": user,
                        "role": sim_role,
                        "actual_role": actual_role,
                        "client_folder_id": client_folder_id or 2,
                    }
                elif sim_role in ("jts_admin", "admin"):
                    return {
                        "username": user,
                        "role": "jts_admin",
                        "actual_role": actual_role,
                        "client_folder_id": None,
                    }

            return {
                "username": user,
                "role": actual_role,
                "actual_role": actual_role,
                # A client login without an assigned client gets a folder id that matches nothing,
                # so every "filter to my folder" check returns no data instead of another client's.
                "client_folder_id": client_folder_id or (UNASSIGNED_CLIENT_FOLDER if actual_role in ("client_admin", "client_standard") else None),
            }

    role_hdr = request.headers.get("X-JTS-Role", "").strip().lower()
    if not token and not role_hdr and not sim_role:
        return {"username": None, "role": None, "actual_role": None, "client_folder_id": None}

    if ignore_simulation:
        active_role = sim_role if sim_role in ("jts_admin", "admin") else ("client_admin" if role_hdr == "client_admin" else ("client_standard" if role_hdr == "client_standard" else "jts_admin"))
    else:
        active_role = sim_role if sim_role in ("client_admin", "client_standard", "jts_admin") else (role_hdr if role_hdr in ("client_admin", "client_standard", "jts_admin") else "jts_admin")
    
    actual_role = "jts_admin" if (role_hdr == "admin" and not sim_role and not token) else active_role
    folder_id = 2 if active_role in ("client_admin", "client_standard") else None
    return {"username": "admin", "role": active_role if not ignore_simulation else actual_role, "actual_role": actual_role, "client_folder_id": folder_id}


def require_session(request: Request) -> dict:
    """
    Returns the caller's user context, but only for a real, signed session token.
    Unlike get_user_context, it never trusts role headers without a login, and it
    resolves a client's real folder instead of defaulting to folder 2 (None if unassigned).
    Raises 401 when the caller is not signed in.
    """
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)

    payload = verify_session_token(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail="Please sign in again.")

    ctx = get_user_context(request)
    actual_role = payload.get("role", "jts_admin")
    if ctx.get("role") in ("client_admin", "client_standard") and actual_role in ("client_admin", "client_standard"):
        folder_id = payload.get("client_folder_id")
        if not folder_id:
            db_u = get_user_from_db(payload.get("user", ""))
            folder_id = db_u.get("client_folder_id") if db_u else None
        ctx["client_folder_id"] = folder_id
    # A JTS admin previewing a client role keeps the simulated folder from get_user_context.
    return ctx


def _norm_channel_key(value: Any) -> str:
    return str(value or "").strip().lower().lstrip("#@")


def client_channel_scope(ctx: dict) -> Optional[set]:
    """
    None means the caller sees every channel (JTS admin).
    Otherwise the set of normalized channel IDs/names in the caller's client folder (empty if unassigned).
    """
    if ctx.get("role") not in ("client_admin", "client_standard"):
        return None
    folder_id = ctx.get("client_folder_id")
    if not folder_id:
        return set()
    from app.services.channel_secrets_service import channel_id_variants

    scope = set()
    for c in get_folder_channel_ids(folder_id):
        if _norm_channel_key(c):
            scope.add(_norm_channel_key(c))
        # Slack's real ID can differ from a mistyped stored one (see LEGACY_CHANNEL_ALIASES): accept both.
        for v in channel_id_variants(c):
            if _norm_channel_key(v):
                scope.add(_norm_channel_key(v))
    return scope


def channel_in_scope(scope: Optional[set], *values: Any) -> bool:
    """Exact (case- and #/@-insensitive) match of any channel ID or name against the scope."""
    if scope is None:
        return True
    return any(_norm_channel_key(v) in scope for v in values if _norm_channel_key(v))


def require_jts_admin(request: Request) -> dict:
    """Signed-in JTS admin only (a JTS admin previewing a client role is treated as that client)."""
    ctx = require_session(request)
    if ctx.get("role") != "jts_admin":
        raise HTTPException(status_code=403, detail="Only JTS admins can do this.")
    return ctx


def get_folder_channel_ids(client_folder_id: Optional[int]) -> List[str]:
    """Retrieves all channel_ids and channel_names assigned to a given client folder, including prefixed/bare variants."""
    if not client_folder_id:
        return []
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            from app.services.channel_secrets_service import _ensure_workspace_folder_column
            _ensure_workspace_folder_column(cur)
            conn.commit()
            # The client's own channels, plus chats (for example personal chats) of a Slack workspace linked to this client.
            cur.execute("""
                SELECT cm.channel_id, cm.channel_name
                FROM channel_metadata cm
                LEFT JOIN slack_workspaces sw ON sw.team_id = cm.workspace_id
                WHERE cm.folder_id = %s OR cm.folder_id::text = %s OR (cm.folder_id IS NULL AND sw.folder_id = %s);
            """, (client_folder_id, str(client_folder_id), client_folder_id))
            rows = cur.fetchall() or []
            result = set()
            for r in rows:
                c_id = r["channel_id"] if isinstance(r, dict) else r[0]
                c_name = r["channel_name"] if isinstance(r, dict) else r[1]
                for item in (c_id, c_name):
                    if item:
                        clean = str(item).strip()
                        if not clean:
                            continue
                        result.add(clean)
                        result.add(clean.lower())
                        result.add(clean.upper())
                        bare = clean.lstrip("#@")
                        if bare:
                            result.add(bare)
                            result.add(bare.lower())
                            result.add(bare.upper())
                            result.add(f"#{bare}")
                            result.add(f"#{bare.lower()}")
                            result.add(f"@{bare}")
                            result.add(f"@{bare.lower()}")
            # Include legacy mistyped/real ID pairs so Slack's real ID matches the stored one.
            from app.services.channel_secrets_service import LEGACY_CHANNEL_ALIASES
            for item in list(result):
                alias = LEGACY_CHANNEL_ALIASES.get(item.upper())
                if alias:
                    result.update({alias, alias.lower()})
            return list(result)
    except Exception as e:
        logger.warning(f"Error fetching channel IDs for folder #{client_folder_id}: {e}")
        return []
    finally:
        if conn:
            conn.close()


def _link_folder_to_organization(cur, role: str, folder_id: Optional[int], org_id: Optional[int]) -> None:
    """A Client Admin tied to an organization also tells us which organization their client is billed as.
    Only fills the client's organization when it has none yet (never overwrites a choice)."""
    if role != "client_admin" or not folder_id or not org_id:
        return
    try:
        from app.services.channel_secrets_service import ensure_folder_org_column
        ensure_folder_org_column(cur)
        cur.execute("UPDATE channel_folders SET organization_id = %s WHERE id = %s AND organization_id IS NULL;", (org_id, folder_id))
    except Exception as e:
        logger.debug(f"[USERS] Could not link client {folder_id} to organization {org_id}: {e}")


def _ensure_users_columns(conn_or_cur):
    """Ensure name, organization_id, and timezone columns exist in dashboard_users table safely and committed to disk."""
    try:
        if hasattr(conn_or_cur, "cursor"):
            with conn_or_cur.cursor() as cur:
                cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS name VARCHAR(255);")
                cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL;")
                cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS timezone VARCHAR(100) DEFAULT 'UTC';")
                cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS tool_permissions JSONB;")
            conn_or_cur.commit()
        else:
            conn_or_cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS name VARCHAR(255);")
            conn_or_cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL;")
            conn_or_cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS timezone VARCHAR(100) DEFAULT 'UTC';")
            conn_or_cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS tool_permissions JSONB;")
    except Exception as e:
        logger.debug(f"[_ensure_users_columns] DDL note: {e}")


def _ensure_token_table(cur):
    """Ensure password_reset_tokens table exists safely."""
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id SERIAL PRIMARY KEY,
                workspace_id VARCHAR(64),
                workspace_name VARCHAR(255),
                user_id INTEGER REFERENCES dashboard_users(id) ON DELETE CASCADE,
                token VARCHAR(128) UNIQUE NOT NULL,
                token_type VARCHAR(50) DEFAULT 'set_password',
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                used_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_pwd_reset_token ON password_reset_tokens (token);
            CREATE INDEX IF NOT EXISTS idx_pwd_reset_user ON password_reset_tokens (user_id);
        """)
    except Exception:
        pass


def get_user_from_db(username: str) -> Optional[dict]:
    """Fetch user record from dashboard_users table."""
    if not username:
        return None
    conn = None
    try:
        conn = get_db_connection()
        _ensure_users_columns(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT u.id, COALESCE(u.name, u.username) as name, u.username, u.email, u.password_hash, u.role, 
                       COALESCE(u.timezone, 'UTC') as timezone,
                       u.client_folder_id, f.name as client_folder_name,
                       u.organization_id, o.name as organization_name
                FROM dashboard_users u
                LEFT JOIN channel_folders f ON u.client_folder_id = f.id
                LEFT JOIN organizations o ON u.organization_id = o.id
                WHERE LOWER(TRIM(u.username)) = LOWER(TRIM(%s)) OR LOWER(TRIM(u.email)) = LOWER(TRIM(%s));
            """, (username, username))
            row = cur.fetchone()
            if row:
                res = dict(row)
                logger.info(f"[AUTH] get_user_from_db('{username}') -> found id={res.get('id')}, name='{res.get('name')}', timezone='{res.get('timezone')}'")
                return res
        logger.info(f"[AUTH] get_user_from_db('{username}') -> user not found in DB")
    except Exception as e:
        logger.warning(f"[AUTH] Error fetching user '{username}' from DB: {e}", exc_info=True)
        if conn:
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, username, email, password_hash, role, client_folder_id, organization_id, COALESCE(timezone, 'UTC') as timezone
                        FROM dashboard_users
                        WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s)) OR LOWER(TRIM(email)) = LOWER(TRIM(%s));
                    """, (username, username))
                    row = cur.fetchone()
                    if row:
                        return dict(row)
            except Exception:
                pass
    finally:
        if conn:
            conn.close()
    return None



# --- Endpoints ---
def _issue_session(response: Response, username: str, message: Optional[str] = None, recovery_codes: Optional[List[str]] = None) -> LoginResponse:
    """Creates the real session for a person who has passed every sign-in step."""
    expected_admin_user, _ = get_admin_credentials()
    if secrets.compare_digest(username.strip(), expected_admin_user):
        token = create_session_token(username=expected_admin_user, role="jts_admin", client_folder_id=None)
        db_admin = get_user_from_db(expected_admin_user)
        user = UserResponse(
            name=(db_admin.get("name") if db_admin else None) or "JTS Admin",
            username=expected_admin_user, role="jts_admin",
            timezone=(db_admin.get("timezone") if db_admin else None) or "UTC",
        )
        msg = message or "Login successful as JTS Admin"
    else:
        db_user = get_user_from_db(username.strip())
        if not db_user:
            raise HTTPException(status_code=401, detail="Please sign in again.")
        token = create_session_token(username=db_user["username"], role=db_user["role"], client_folder_id=db_user["client_folder_id"])
        user = UserResponse(
            id=db_user["id"], name=db_user.get("name"), username=db_user["username"], email=db_user["email"],
            role=db_user["role"], timezone=db_user.get("timezone") or "UTC",
            client_folder_id=db_user["client_folder_id"], client_folder_name=db_user.get("client_folder_name"),
        )
        msg = message or f"Login successful as {db_user['role']}"
    response.set_cookie(key=SESSION_COOKIE_NAME, value=token, httponly=False, samesite="lax", secure=False, path="/")
    return LoginResponse(status="success", message=msg, token=token, user=user, recovery_codes=recovery_codes)


def _after_password(request: Request, response: Response, username: str, role: str, folder_id: Optional[int]):
    """Steps after a correct password: the client's network rule, then two-step verification for admins."""
    from app.services import ip_allowlist, mfa_service as mfa

    ip_allowlist.enforce(request, role, folder_id)
    try:
        needs_mfa = mfa.required_for(role) or mfa.is_enabled(username)
    except Exception as e:  # never lock everyone out because of a database hiccup in the MFA table
        logger.error(f"[MFA] Could not check two-step status for '{username}': {e}")
        needs_mfa = False
    if needs_mfa:
        enabled = mfa.is_enabled(username)
        return JSONResponse(content={
            "status": "mfa_required" if enabled else "mfa_setup_required",
            "message": "Enter the 6-digit code from your authenticator app." if enabled
                       else "Set up two-step verification to finish signing in.",
            "mfa_token": create_mfa_token(username, role, folder_id),
        })
    return _issue_session(response, username)


@auth_router.post("/login", summary="Sign in to JTS Console")
def login(req: LoginRequest, request: Request, response: Response):
    """
    Authenticates user (JTS Admin, Client Admin, or Client Standard). Admins then pass two-step verification.
    """
    expected_admin_user, expected_admin_pwd = get_admin_credentials()

    # 1. Check Default JTS Master Admin
    if secrets.compare_digest(req.username.strip(), expected_admin_user) and secrets.compare_digest(req.password.strip(), expected_admin_pwd):
        return _after_password(request, response, expected_admin_user, "jts_admin", None)

    # 2. Check Database Users
    db_user = get_user_from_db(req.username.strip())
    if db_user:
        input_hash = hash_password(req.password.strip())
        if secrets.compare_digest(input_hash, db_user["password_hash"]):
            return _after_password(request, response, db_user["username"], db_user["role"], db_user["client_folder_id"])

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid User ID or Password.",
    )


class MfaTokenRequest(BaseModel):
    mfa_token: str


class MfaCodeRequest(BaseModel):
    mfa_token: str
    code: str = Field(..., max_length=32)


def _mfa_username(token: str) -> str:
    payload = verify_mfa_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Your sign-in timed out. Please sign in again.")
    return payload["user"]


def _mfa_http(e) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


@auth_router.post("/mfa/setup", summary="Start two-step verification setup (returns the secret and QR link)")
def mfa_setup(req: MfaTokenRequest):
    from app.services import mfa_service as mfa

    username = _mfa_username(req.mfa_token)
    try:
        return mfa.start_setup(username)
    except mfa.MfaError as e:
        raise _mfa_http(e)


@auth_router.post("/mfa/enable", summary="Confirm the first code, turn two-step verification on and sign in")
def mfa_enable(req: MfaCodeRequest, response: Response):
    from app.services import mfa_service as mfa

    username = _mfa_username(req.mfa_token)
    try:
        codes = mfa.confirm_setup(username, req.code)
    except mfa.MfaError as e:
        raise _mfa_http(e)
    logger.info(f"[MFA] Two-step verification turned on for '{username}'")
    return _issue_session(response, username, "Two-step verification is on.", recovery_codes=codes)


@auth_router.post("/mfa/verify", summary="Second sign-in step: the 6-digit code (or a recovery code)")
def mfa_verify(req: MfaCodeRequest, response: Response):
    from app.services import mfa_service as mfa

    username = _mfa_username(req.mfa_token)
    try:
        ok = mfa.verify_login(username, req.code)
    except mfa.MfaError as e:
        raise _mfa_http(e)
    if not ok:
        raise HTTPException(status_code=401, detail="That code isn't right. Check your authenticator app and try again.")
    return _issue_session(response, username)


@auth_router.get("/mfa/status", summary="Is two-step verification on for me?")
def mfa_status(request: Request):
    from app.services import mfa_service as mfa

    ctx = require_session(request)
    username = ctx.get("username") or ""
    return {
        "required": mfa.required_for(ctx.get("actual_role") or ctx.get("role")),
        "enabled": mfa.is_enabled(username),
        "recovery_codes_left": mfa.recovery_codes_left(username),
    }


@auth_router.get("/my-ip", summary="The address this request comes from")
def my_ip(request: Request):
    from app.services import ip_allowlist

    return {"ip": ip_allowlist.client_ip(request)}


@auth_router.post("/logout", summary="Log out of JTS Console")
def logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "success", "message": "Logged out successfully"}


@auth_router.get("/me", summary="Check current authentication session")
@users_router.get("/me", summary="Get current user profile")
@auth_router.get("/profile", summary="Get current user profile")
def get_current_user(request: Request):
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)

    role = None
    client_folder_id = None
    if token:
        payload = verify_session_token(token)
        if not payload:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")
        username = payload.get("user")
        role = payload.get("role")
        client_folder_id = payload.get("client_folder_id")
    else:
        user_ctx = get_user_context(request)
        username = user_ctx.get("username")
        role = user_ctx.get("role")
        client_folder_id = user_ctx.get("client_folder_id")

    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    db_u = get_user_from_db(username)
    if db_u:
        tz = db_u.get("timezone") or "UTC"
        logger.info(f"[AUTH] get_current_user: user='{username}' -> DB id={db_u.get('id')}, name='{db_u.get('name')}', email='{db_u.get('email')}', timezone='{tz}'")
        return {
            "authenticated": True,
            "user": {
                "id": db_u.get("id"),
                "name": db_u.get("name") or db_u.get("username"),
                "username": db_u.get("username"),
                "email": db_u.get("email") or "",
                "role": db_u.get("role") or role or "client_admin",
                "timezone": tz,
                "client_folder_id": db_u.get("client_folder_id") or client_folder_id,
                "client_folder_name": db_u.get("client_folder_name"),
                "organization_id": db_u.get("organization_id"),
                "organization_name": db_u.get("organization_name"),
            }
        }

    logger.info(f"[AUTH] get_current_user: user='{username}' not in DB, returning fallback default JTS Admin (timezone=UTC)")
    return {
        "authenticated": True,
        "user": {
            "id": 0,
            "name": "JTS Admin",
            "username": username,
            "email": "admin@jts.com",
            "role": role or "jts_admin",
            "timezone": "UTC",
            "client_folder_id": None,
            "client_folder_name": "Global (All Folders)",
            "organization_id": None,
            "organization_name": None,
        }
    }



@users_router.put("/me", summary="Update current user's own profile")
@users_router.patch("/me", summary="Update current user's own profile")
@auth_router.put("/profile", summary="Update current user's own profile")
def update_my_profile(req: UpdateProfileRequest, request: Request):
    """Allows the currently authenticated user to update their own profile (name, email, timezone, optional password)."""
    user_ctx = get_user_context(request, ignore_simulation=True)
    username = user_ctx.get("username")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    clean_name = (req.name or "").strip()
    clean_email = (req.email or "").strip()
    clean_password = (req.password or "").strip() if req.password else ""
    clean_timezone = (req.timezone or "").strip() or "UTC"

    logger.info(f"[USERS] PUT /api/users/me called by '{username}': name='{clean_name}', email='{clean_email}', timezone='{clean_timezone}', pwd_set={bool(clean_password)}")

    if not clean_name:
        raise HTTPException(status_code=400, detail="Full Name is required.")

    if clean_email and ("@" not in clean_email or "." not in clean_email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    conn = None
    try:
        conn = get_db_connection()
        _ensure_users_columns(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, email, role, client_folder_id, timezone 
                FROM dashboard_users 
                WHERE LOWER(TRIM(username)) = LOWER(TRIM(%s)) OR LOWER(TRIM(email)) = LOWER(TRIM(%s));
            """, (username, username))
            db_user_row = cur.fetchone()

            if clean_email:
                if db_user_row:
                    uid = db_user_row.get("id") if isinstance(db_user_row, dict) else db_user_row[0]
                    cur.execute("SELECT id FROM dashboard_users WHERE LOWER(TRIM(email)) = LOWER(%s) AND id != %s;", (clean_email, uid))
                else:
                    cur.execute("SELECT id FROM dashboard_users WHERE LOWER(TRIM(email)) = LOWER(%s);", (clean_email,))
                if cur.fetchone():
                    raise HTTPException(status_code=400, detail=f"Email '{clean_email}' is already registered to another user.")

            pwd_hash = hash_password(clean_password) if clean_password else None

            if db_user_row:
                uid = db_user_row.get("id") if isinstance(db_user_row, dict) else db_user_row[0]
                existing_email = db_user_row.get("email") if isinstance(db_user_row, dict) else (db_user_row[2] if len(db_user_row) > 2 else None)
                final_email = clean_email or existing_email or "admin@jts.com"
                if pwd_hash:
                    cur.execute("""
                        UPDATE dashboard_users
                        SET name = %s, email = %s, timezone = %s, password_hash = %s, updated_at = now()
                        WHERE id = %s
                        RETURNING id, name, username, email, role, timezone, client_folder_id;
                    """, (clean_name, final_email, clean_timezone, pwd_hash, uid))
                else:
                    cur.execute("""
                        UPDATE dashboard_users
                        SET name = %s, email = %s, timezone = %s, updated_at = now()
                        WHERE id = %s
                        RETURNING id, name, username, email, role, timezone, client_folder_id;
                    """, (clean_name, final_email, clean_timezone, uid))
                updated_row = cur.fetchone()
                conn.commit()
            else:
                expected_user, expected_pwd = get_admin_credentials()
                default_hash = hash_password(expected_pwd)
                cur.execute("""
                    INSERT INTO dashboard_users (name, username, email, password_hash, role, timezone, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (username) DO UPDATE SET
                        name = EXCLUDED.name,
                        email = EXCLUDED.email,
                        timezone = EXCLUDED.timezone
                    RETURNING id, name, username, email, role, timezone, client_folder_id;
                """, (clean_name, username, clean_email or "admin@jts.com", pwd_hash or default_hash, "jts_admin", clean_timezone))
                updated_row = cur.fetchone()
                conn.commit()

            # Synchronize creator name in secrets_vault if exists
            try:
                cur.execute("""
                    UPDATE secrets_vault
                    SET name = %s
                    WHERE name = %s OR key_name LIKE %s;
                """, (clean_name, username, f"%{username}%"))
                conn.commit()
            except Exception:
                pass

            folder_id = updated_row.get("client_folder_id") if isinstance(updated_row, dict) else (updated_row[6] if len(updated_row) > 6 else None)
            folder_name = None
            if folder_id:
                try:
                    cur.execute("SELECT name FROM channel_folders WHERE id = %s;", (folder_id,))
                    f_row = cur.fetchone()
                    if f_row:
                        folder_name = f_row.get("name") if isinstance(f_row, dict) else f_row[0]
                except Exception:
                    pass

            ret_user = {
                "id": updated_row.get("id") if isinstance(updated_row, dict) else updated_row[0],
                "name": clean_name,
                "username": username,
                "email": clean_email,
                "role": updated_row.get("role") if isinstance(updated_row, dict) else (updated_row[4] if len(updated_row) > 4 else "client_admin"),
                "timezone": clean_timezone,
                "client_folder_id": folder_id,
                "client_folder_name": folder_name or ("Global (All Folders)" if user_ctx.get("role") == "jts_admin" else None),
            }
            logger.info(f"[USERS] User '{username}' successfully updated profile in DB: id={ret_user['id']}, name='{clean_name}', email='{clean_email}', timezone='{clean_timezone}'")
            return {
                "status": "success",
                "message": "Profile updated successfully.",
                "user": ret_user,
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error updating user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")
    finally:
        if conn:
            conn.close()


# --- User Management API Endpoints ---
@users_router.get("", summary="List all users")
@users_router.get("/", summary="List all users")
def list_users(request: Request):
    """Retrieve users list. Filtered by client_folder_id only if caller is strictly a client_admin."""
    user_ctx = get_user_context(request)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    active_role = user_ctx.get("role")
    folder_id = user_ctx.get("client_folder_id")

    is_admin_view = (actual_role in ("jts_admin", "admin")) or (active_role in ("jts_admin", "admin"))

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_users_columns(cur)
            if not is_admin_view and active_role in ("client_admin", "client_standard") and folder_id:
                cur.execute("""
                    SELECT u.id, COALESCE(u.name, u.username) as name, u.username, u.email, u.role, 
                           COALESCE(u.timezone, 'UTC') as timezone,
                           u.client_folder_id, f.name as client_folder_name,
                           u.organization_id, o.name as organization_name,
                           u.created_at, u.tool_permissions
                    FROM dashboard_users u
                    LEFT JOIN channel_folders f ON u.client_folder_id = f.id
                    LEFT JOIN organizations o ON u.organization_id = o.id
                    WHERE u.client_folder_id = %s
                    ORDER BY u.id DESC;
                """, (folder_id,))
            else:
                cur.execute("""
                    SELECT u.id, COALESCE(u.name, u.username) as name, u.username, u.email, u.role, 
                           COALESCE(u.timezone, 'UTC') as timezone,
                           u.client_folder_id, f.name as client_folder_name,
                           u.organization_id, o.name as organization_name,
                           u.created_at, u.tool_permissions
                    FROM dashboard_users u
                    LEFT JOIN channel_folders f ON u.client_folder_id = f.id
                    LEFT JOIN organizations o ON u.organization_id = o.id
                    ORDER BY u.id DESC;
                """)
            rows = cur.fetchall() or []
            from app.services import mfa_service

            mfa_on = mfa_service.enabled_usernames()
            users = []
            for r in rows:
                d = dict(r)
                # The dashboard gets the permissions that are actually in force (role defaults + saved checkboxes).
                d["tool_permissions"] = effective_permissions(d.get("tool_permissions"), d.get("role"))
                d["mfa_enabled"] = (d.get("username") or "").strip().lower() in mfa_on
                d["mfa_required"] = mfa_service.required_for(d.get("role"))
                users.append(d)
            return {"users": users}
    except Exception as e:
        logger.error(f"Failed to fetch users: {e}", exc_info=True)
        if conn:
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("SELECT id, username, email, role, client_folder_id, organization_id, COALESCE(timezone, 'UTC') as timezone, created_at FROM dashboard_users ORDER BY id DESC;")
                    rows = cur.fetchall() or []
                    return {"users": [{"name": r.get("username") if isinstance(r, dict) else r[1], **(dict(r) if isinstance(r, dict) else {})} for r in rows]}
            except Exception as e2:
                logger.error(f"Fallback fetch users failed: {e2}")
        return {"users": []}
    finally:
        if conn:
            conn.close()


@auth_router.get("/users", summary="List all users (auth prefix)")
@auth_router.get("/users/", summary="List all users (auth prefix)")
def list_users_auth(request: Request):
    return list_users(request)


@users_router.post("", summary="Create new dashboard user")
@users_router.post("/", summary="Create new dashboard user")
def create_user(req: CreateUserRequest, request: Request):
    """Creates a new dashboard user with designated role, folder, and organization assignment."""
    user_ctx = get_user_context(request)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    active_role = user_ctx.get("role")

    is_admin_view = (actual_role in ("jts_admin", "admin")) or (active_role in ("jts_admin", "admin"))
    if not is_admin_view:
        raise HTTPException(status_code=403, detail="Access denied: Only JTS Admin can create new users.")

    # Validate fields
    clean_name = (req.name or "").strip()
    clean_username = (req.username or "").strip()
    clean_email = (req.email or "").strip()
    clean_password = (req.password or "").strip() if req.password else ""
    role = (req.role or "client_admin").strip()
    # Inherit global system timezone if not explicitly provided
    if req.timezone and req.timezone.strip():
        clean_tz = req.timezone.strip()
    else:
        try:
            from app.db.repositories import get_global_system_settings
            gs = get_global_system_settings()
            clean_tz = (gs.get("timezone") or "UTC").strip()
        except Exception:
            clean_tz = "UTC"
    folder_id = req.client_folder_id if req.client_folder_id else None
    org_id = req.organization_id if req.organization_id else None

    if not clean_name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if not clean_username:
        raise HTTPException(status_code=400, detail="Username / User ID is required.")
    if not clean_email:
        raise HTTPException(status_code=400, detail="Email Address is required.")
    if "@" not in clean_email or "." not in clean_email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if role not in ("jts_admin", "client_admin", "client_standard"):
        raise HTTPException(status_code=400, detail="Invalid User Access Role selected.")
    if role != "jts_admin" and not folder_id:
        raise HTTPException(status_code=400, detail="Please select a Client Folder to assign to this user.")
    if role == "client_admin" and not org_id:
        raise HTTPException(status_code=400, detail="Please select an Organization to assign to this Client Admin.")
    try:
        perms = clean_permissions(req.tool_permissions)
    except ValueError as pe:
        raise HTTPException(status_code=400, detail=str(pe))

    clean_password = (req.password or "").strip() if req.password else ""
    is_placeholder_pwd = (not clean_password) or clean_password.startswith("Temp_")

    conn = None
    try:
        # If real password is provided, use it. Otherwise, assign a temporary random hash
        # that cannot be guessed while awaiting the user to set their password via email token.
        pwd_hash = hash_password(secrets.token_urlsafe(32)) if is_placeholder_pwd else hash_password(clean_password)
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_users_columns(cur)
            _ensure_token_table(cur)

            # 1. Check if username already exists (case-insensitive)
            cur.execute("SELECT id FROM dashboard_users WHERE LOWER(TRIM(username)) = LOWER(%s);", (clean_username,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail=f"Username '{clean_username}' already exists.")

            # 2. Check if email already exists (unique email enforcement)
            cur.execute("SELECT id FROM dashboard_users WHERE LOWER(TRIM(email)) = LOWER(%s);", (clean_email,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail=f"Email '{clean_email}' is already registered to another user.")

            cur.execute("""
                INSERT INTO dashboard_users (name, username, email, password_hash, role, timezone, client_folder_id, organization_id, tool_permissions, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                RETURNING id;
            """, (clean_name, clean_username, clean_email, pwd_hash, role, clean_tz, folder_id, org_id,
                  json.dumps(perms) if perms is not None else None))
            row = cur.fetchone()
            new_id = row["id"] if isinstance(row, dict) else row[0]
            _link_folder_to_organization(cur, role, folder_id, org_id)

            email_sent = False
            email_msg = ""
            if is_placeholder_pwd:
                # Generate set-password token (valid for 24 hours)
                setup_token = secrets.token_urlsafe(48)
                expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
                cur.execute("""
                    INSERT INTO password_reset_tokens (user_id, token, token_type, expires_at)
                    VALUES (%s, %s, 'set_password', %s);
                """, (new_id, setup_token, expires_at))

                email_res = send_password_setup_email(
                    to_email=clean_email,
                    recipient_name=clean_name,
                    token=setup_token,
                )
                email_sent = email_res.get("success", False)
                email_msg = f" A password setup invitation email has been sent to {clean_email}."
                if email_res.get("mode") == "mock":
                    email_msg += f" (Note: SMTP not configured on server. Setup link: {email_res.get('url')})"
                elif not email_sent:
                    email_msg = f" Note: User created, but could not send email via SMTP ({email_res.get('error')})."

            conn.commit()
            logger.info(f"[USERS] Created user '{clean_username}' (id={new_id}, name='{clean_name}', role={role}, folder={folder_id}, org={org_id}, email_sent={email_sent})")
            return {
                "status": "success",
                "user_id": new_id,
                "message": f"User '{clean_name}' (@{clean_username}) created successfully.{email_msg}",
                "email_sent": email_sent,
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to create user: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to create user: {str(e)}")
    finally:
        if conn:
            conn.close()


@auth_router.post("/users", summary="Create new dashboard user (auth prefix)")
def create_user_auth(req: CreateUserRequest, request: Request):
    return create_user(req, request)


@users_router.post("/{user_id}/send-setup-email", summary="Send/resend password setup email to user")
@auth_router.post("/users/{user_id}/send-setup-email", summary="Send/resend password setup email to user (auth prefix)")
def send_user_setup_email(user_id: int, request: Request):
    """Admin triggers or resends password setup email with a fresh 24h token to the user's email address."""
    user_ctx = get_user_context(request)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    active_role = user_ctx.get("role")

    is_admin_view = (actual_role in ("jts_admin", "admin")) or (active_role in ("jts_admin", "admin"))
    if not is_admin_view:
        raise HTTPException(status_code=403, detail="Access denied: Only JTS Admin can send setup emails.")

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_users_columns(cur)
            _ensure_token_table(cur)

            cur.execute("SELECT id, name, username, email FROM dashboard_users WHERE id = %s;", (user_id,))
            user_row = cur.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail=f"User #{user_id} not found.")

            name = user_row["name"] if isinstance(user_row, dict) else user_row[1]
            username = user_row["username"] if isinstance(user_row, dict) else user_row[2]
            email = user_row["email"] if isinstance(user_row, dict) else user_row[3]

            if not email or "@" not in email:
                raise HTTPException(status_code=400, detail=f"User '{username}' does not have a valid email address configured.")

            # Invalidate any existing active tokens for this user
            cur.execute("""
                UPDATE password_reset_tokens
                SET used_at = now()
                WHERE user_id = %s AND used_at IS NULL;
            """, (user_id,))

            # Generate fresh token
            setup_token = secrets.token_urlsafe(48)
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            cur.execute("""
                INSERT INTO password_reset_tokens (user_id, token, token_type, expires_at)
                VALUES (%s, %s, 'set_password', %s);
            """, (user_id, setup_token, expires_at))
            conn.commit()

            email_res = send_password_setup_email(
                to_email=email,
                recipient_name=name or username,
                token=setup_token,
            )

            if not email_res.get("success", False) and email_res.get("mode") == "error":
                raise HTTPException(status_code=500, detail=f"Failed to send email: {email_res.get('error')}")

            logger.info(f"[USERS] Admin sent password setup email to user #{user_id} ({email})")
            return {
                "status": "success",
                "message": f"Password setup email successfully sent to {email}.",
                "email_result": email_res,
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to send setup email for user #{user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to send setup email: {str(e)}")
    finally:
        if conn:
            conn.close()


@auth_router.get("/verify-setup-token", response_model=VerifyTokenResponse, summary="Verify password setup token")
def verify_setup_token(token: str = Query(..., description="Password setup token")):
    """Validates whether a password setup token is valid, unexpired, and not previously used."""
    if not token or not token.strip():
        return VerifyTokenResponse(valid=False, message="Token is missing.")

    clean_token = token.strip()
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_token_table(cur)
            cur.execute("""
                SELECT t.id, t.user_id, t.expires_at, t.used_at,
                       u.username, u.name, u.email
                FROM password_reset_tokens t
                JOIN dashboard_users u ON t.user_id = u.id
                WHERE t.token = %s;
            """, (clean_token,))
            row = cur.fetchone()

            if not row:
                return VerifyTokenResponse(
                    valid=False,
                    message="This password setup link is invalid or has expired."
                )

            used_at = row["used_at"] if isinstance(row, dict) else row[3]
            expires_at = row["expires_at"] if isinstance(row, dict) else row[2]
            username = row["username"] if isinstance(row, dict) else row[4]
            name = row["name"] if isinstance(row, dict) else row[5]
            email = row["email"] if isinstance(row, dict) else row[6]

            if used_at is not None:
                return VerifyTokenResponse(
                    valid=False,
                    message="This password setup link has already been used. Please log in or request a new link."
                )

            # Check expiration
            if expires_at:
                now_utc = datetime.now(timezone.utc)
                # Ensure expires_at is timezone-aware for comparison
                exp_utc = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
                if now_utc > exp_utc:
                    return VerifyTokenResponse(
                        valid=False,
                        message="This password setup link has expired (links are valid for 24 hours). Please contact your administrator."
                    )

            return VerifyTokenResponse(
                valid=True,
                username=username,
                name=name or username,
                email=email,
                message="Token is valid."
            )
    except Exception as e:
        logger.error(f"Error verifying setup token: {e}", exc_info=True)
        return VerifyTokenResponse(valid=False, message=f"Verification error: {str(e)}")
    finally:
        if conn:
            conn.close()


@auth_router.post("/set-password", summary="Set password using valid setup token")
def set_password(req: SetPasswordRequest):
    """Sets a new password for the user verified by the one-time token."""
    clean_token = (req.token or "").strip()
    clean_pwd = (req.password or "").strip()

    if not clean_token:
        raise HTTPException(status_code=400, detail="Token is required.")
    if not clean_pwd or len(clean_pwd) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long.")
    if req.confirm_password is not None and clean_pwd != req.confirm_password.strip():
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_token_table(cur)
            cur.execute("""
                SELECT t.id, t.user_id, t.expires_at, t.used_at, u.username, u.name
                FROM password_reset_tokens t
                JOIN dashboard_users u ON t.user_id = u.id
                WHERE t.token = %s;
            """, (clean_token,))
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=400, detail="Invalid or expired setup token.")

            t_id = row["id"] if isinstance(row, dict) else row[0]
            user_id = row["user_id"] if isinstance(row, dict) else row[1]
            expires_at = row["expires_at"] if isinstance(row, dict) else row[2]
            used_at = row["used_at"] if isinstance(row, dict) else row[3]
            username = row["username"] if isinstance(row, dict) else row[4]

            if used_at is not None:
                raise HTTPException(status_code=400, detail="This setup link has already been used.")

            if expires_at:
                now_utc = datetime.now(timezone.utc)
                exp_utc = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
                if now_utc > exp_utc:
                    raise HTTPException(status_code=400, detail="This setup link has expired.")

            # Hash new password and update user
            new_hash = hash_password(clean_pwd)
            cur.execute("""
                UPDATE dashboard_users
                SET password_hash = %s, updated_at = now()
                WHERE id = %s;
            """, (new_hash, user_id))

            # Mark token as used
            cur.execute("""
                UPDATE password_reset_tokens
                SET used_at = now()
                WHERE id = %s;
            """, (t_id,))

            conn.commit()
            logger.info(f"[AUTH] User '{username}' (#{user_id}) successfully set new password via token.")
            return {
                "status": "success",
                "message": "Password has been successfully set. You can now log in.",
                "username": username,
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error setting password via token: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to set password: {str(e)}")
    finally:
        if conn:
            conn.close()


ROLE_NAMES = {"jts_admin": "JTS Admin", "client_admin": "Client Admin", "client_standard": "Team Member"}


class UpdatePermissionsRequest(BaseModel):
    # null = go back to the role's defaults; an object = exactly these checkboxes
    tool_permissions: Optional[Dict[str, StrictBool]] = None


@users_router.post("/{user_id}/mfa/reset", summary="Reset a person's two-step verification (JTS Admin)")
def reset_user_mfa(user_id: int, request: Request):
    """For a lost phone: they set two-step verification up again at their next sign-in."""
    require_jts_admin(request)
    from app.services import mfa_service

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM dashboard_users WHERE id = %s;", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    username = row["username"] if isinstance(row, dict) else row[0]
    removed = mfa_service.reset(username)
    logger.info(f"[MFA] Two-step verification reset for '{username}' by an admin (had it on: {removed})")
    return {"ok": True, "message": f"Two-step verification was reset for {username}. They will set it up again at their next sign-in."}


@users_router.put("/{user_id}/permissions", summary="Set a user's GitHub / Jira permission checkboxes")
@users_router.put("/{user_id}/permissions/", summary="Set a user's GitHub / Jira permission checkboxes")
def update_user_permissions(user_id: int, req: UpdatePermissionsRequest, request: Request):
    """Saves only the permission checkboxes of one user (the Permissions tab). JTS Admin only."""
    user_ctx = get_user_context(request)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    active_role = user_ctx.get("role")
    if not ((actual_role in ("jts_admin", "admin")) or (active_role in ("jts_admin", "admin"))):
        raise HTTPException(status_code=403, detail="Access denied: Only JTS Admin can change permissions.")

    reset = "tool_permissions" in req.model_fields_set and req.tool_permissions is None
    try:
        perms = clean_permissions(req.tool_permissions)
    except ValueError as pe:
        raise HTTPException(status_code=400, detail=str(pe))
    if perms is None and not reset:
        raise HTTPException(status_code=400, detail="Nothing to save.")

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_users_columns(cur)
            cur.execute("SELECT id, username, role FROM dashboard_users WHERE id = %s;", (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"User #{user_id} not found.")
            u_role = row["role"] if isinstance(row, dict) else row[2]
            u_name = row["username"] if isinstance(row, dict) else row[1]
            if u_role in ("jts_admin", "admin"):
                conn.rollback()
                return {"status": "success", "message": "JTS Admins always have every permission.",
                        "tool_permissions": effective_permissions(None, u_role)}
            if reset:
                cur.execute("UPDATE dashboard_users SET tool_permissions = NULL WHERE id = %s;", (user_id,))
            else:
                cur.execute("UPDATE dashboard_users SET tool_permissions = %s::jsonb WHERE id = %s;", (json.dumps(perms), user_id))
            conn.commit()
            logger.info(f"[USERS] Permissions of user #{user_id} (@{u_name}) {'reset to role defaults' if reset else 'set to ' + json.dumps(perms)}")
            return {
                "status": "success",
                "message": f"@{u_name} is back to the {ROLE_NAMES.get(u_role, u_role)} defaults." if reset else f"Permissions saved for @{u_name}.",
                "tool_permissions": effective_permissions(perms if not reset else None, u_role),
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to update permissions of user #{user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="The permissions couldn't be saved. Please try again.")
    finally:
        if conn:
            conn.close()


@users_router.put("/{user_id}", summary="Update existing dashboard user")
@users_router.put("/{user_id}/", summary="Update existing dashboard user")
@users_router.patch("/{user_id}", summary="Update existing dashboard user")
@users_router.patch("/{user_id}/", summary="Update existing dashboard user")
@users_router.post("/{user_id}", summary="Update existing dashboard user")
@users_router.post("/{user_id}/", summary="Update existing dashboard user")
def update_user(user_id: int, req: UpdateUserRequest, request: Request):
    """Updates an existing dashboard user's info (name, email, role, folder, organization, and optional password). Username is never editable."""
    user_ctx = get_user_context(request)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    active_role = user_ctx.get("role")

    is_admin_view = (actual_role in ("jts_admin", "admin")) or (active_role in ("jts_admin", "admin"))
    if not is_admin_view:
        raise HTTPException(status_code=403, detail="Access denied: Only JTS Admin can update users.")

    clean_name = (req.name or "").strip()
    clean_email = (req.email or "").strip()
    clean_password = (req.password or "").strip() if req.password else ""
    role = (req.role or "client_admin").strip()
    folder_id = req.client_folder_id if req.client_folder_id else None
    org_id = req.organization_id if req.organization_id else None

    if not clean_name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if not clean_email:
        raise HTTPException(status_code=400, detail="Email Address is required.")
    if "@" not in clean_email or "." not in clean_email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if role not in ("jts_admin", "client_admin", "client_standard"):
        raise HTTPException(status_code=400, detail="Invalid User Access Role selected.")
    if role != "jts_admin" and not folder_id:
        raise HTTPException(status_code=400, detail="Please select a Client Folder to assign to this user.")
    if role == "client_admin" and not org_id:
        raise HTTPException(status_code=400, detail="Please select an Organization to assign to this Client Admin.")
    try:
        perms = clean_permissions(req.tool_permissions)
    except ValueError as pe:
        raise HTTPException(status_code=400, detail=str(pe))

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_users_columns(cur)

            # Check if user exists
            cur.execute("SELECT id, username FROM dashboard_users WHERE id = %s;", (user_id,))
            user_row = cur.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail=f"User #{user_id} not found.")

            # Check if email is already taken by another user
            cur.execute("SELECT id FROM dashboard_users WHERE LOWER(TRIM(email)) = LOWER(%s) AND id != %s;", (clean_email, user_id))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail=f"Email '{clean_email}' is already registered to another user.")

            clean_tz = (req.timezone or "").strip() if req.timezone else None

            if clean_password:
                pwd_hash = hash_password(clean_password)
                if clean_tz:
                    cur.execute("""
                        UPDATE dashboard_users 
                        SET name = %s, email = %s, password_hash = %s, role = %s, timezone = %s, client_folder_id = %s, organization_id = %s, updated_at = now()
                        WHERE id = %s;
                    """, (clean_name, clean_email, pwd_hash, role, clean_tz, folder_id, org_id, user_id))
                else:
                    cur.execute("""
                        UPDATE dashboard_users 
                        SET name = %s, email = %s, password_hash = %s, role = %s, client_folder_id = %s, organization_id = %s, updated_at = now()
                        WHERE id = %s;
                    """, (clean_name, clean_email, pwd_hash, role, folder_id, org_id, user_id))
            else:
                if clean_tz:
                    cur.execute("""
                        UPDATE dashboard_users 
                        SET name = %s, email = %s, role = %s, timezone = %s, client_folder_id = %s, organization_id = %s, updated_at = now()
                        WHERE id = %s;
                    """, (clean_name, clean_email, role, clean_tz, folder_id, org_id, user_id))
                else:
                    cur.execute("""
                        UPDATE dashboard_users 
                        SET name = %s, email = %s, role = %s, client_folder_id = %s, organization_id = %s, updated_at = now()
                        WHERE id = %s;
                    """, (clean_name, clean_email, role, folder_id, org_id, user_id))

            if perms is not None:
                cur.execute("UPDATE dashboard_users SET tool_permissions = %s::jsonb WHERE id = %s;", (json.dumps(perms), user_id))
            _link_folder_to_organization(cur, role, folder_id, org_id)

            conn.commit()
            u_name = user_row.get("username") if isinstance(user_row, dict) else user_row[1]
            logger.info(f"[USERS] Updated user #{user_id} (@{u_name}) - name='{clean_name}', role={role}, folder={folder_id}, org={org_id}, tz={clean_tz}")
            return {"status": "success", "message": f"User '{clean_name}' (@{u_name}) updated successfully."}

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Failed to update user #{user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to update user: {str(e)}")
    finally:
        if conn:
            conn.close()


@auth_router.put("/users/{user_id}", summary="Update existing dashboard user (auth prefix)")
@auth_router.put("/users/{user_id}/", summary="Update existing dashboard user (auth prefix)")
@auth_router.patch("/users/{user_id}", summary="Update existing dashboard user (auth prefix)")
@auth_router.patch("/users/{user_id}/", summary="Update existing dashboard user (auth prefix)")
@auth_router.post("/users/{user_id}", summary="Update existing dashboard user (auth prefix)")
@auth_router.post("/users/{user_id}/", summary="Update existing dashboard user (auth prefix)")
def update_user_auth(user_id: int, req: UpdateUserRequest, request: Request):
    return update_user(user_id, req, request)


@users_router.delete("/{user_id}", summary="Delete user")
@users_router.delete("/{user_id}/", summary="Delete user")
def delete_user(user_id: int, request: Request):
    user_ctx = get_user_context(request)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    active_role = user_ctx.get("role")

    is_admin_view = (actual_role in ("jts_admin", "admin")) or (active_role in ("jts_admin", "admin"))
    if not is_admin_view:
        raise HTTPException(status_code=403, detail="Access denied: Only JTS Admin can delete users.")

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dashboard_users WHERE id = %s;", (user_id,))
            conn.commit()
            return {"status": "success", "message": f"User #{user_id} deleted."}
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to delete user: {str(e)}")
    finally:
        if conn:
            conn.close()


@auth_router.delete("/users/{user_id}", summary="Delete user (auth prefix)")
def delete_user_auth(user_id: int, request: Request):
    return delete_user(user_id, request)
