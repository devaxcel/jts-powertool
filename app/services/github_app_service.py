"""
GitHub App connection per client (folder).

JTS registers ONE GitHub App. Each client installs it on their own GitHub account/org with one click
(started from Slack or the dashboard). We store only the installation id; for every action we mint a
short-lived (1 hour) installation token with the app's private key. The installing user's refresh token
is kept in AWS Secrets Manager for actions that need a user token (e.g. creating repos on personal accounts).
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.db.session import get_db_connection
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"
CONNECT_LINK_TTL_SECONDS = 15 * 60
USER_TOKEN_PROVIDER = "github_user"  # folder_api_keys provider name for the stored refresh token
_TOKEN_REFRESH_MARGIN_SECONDS = 10 * 60

# installation_id -> (token, expires_epoch)
_INSTALLATION_TOKEN_CACHE: Dict[int, Tuple[str, float]] = {}


class GitHubAppError(Exception):
    """A problem we can explain to the user in plain words."""


# --------------------------------------------------------------------------- config

def app_config() -> Dict[str, str]:
    key = get_secret("GITHUB_APP_PRIVATE_KEY", "") or ""
    # Keys stored in env/Secrets Manager often have escaped newlines.
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    return {
        "app_id": (get_secret("GITHUB_APP_ID", "") or "").strip(),
        "slug": (get_secret("GITHUB_APP_SLUG", "") or "").strip(),
        "private_key": key.strip(),
        "client_id": (get_secret("GITHUB_APP_CLIENT_ID", "") or "").strip(),
        "client_secret": (get_secret("GITHUB_APP_CLIENT_SECRET", "") or "").strip(),
        "webhook_secret": (get_secret("GITHUB_APP_WEBHOOK_SECRET", "") or "").strip(),
    }


def is_configured() -> bool:
    cfg = app_config()
    return all(cfg[k] for k in ("app_id", "slug", "private_key", "client_id", "client_secret"))


def public_base_url() -> str:
    return (get_secret("PUBLIC_BASE_URL", "https://journeys.pe") or "https://journeys.pe").rstrip("/")


# --------------------------------------------------------------------------- tables

def _ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS github_connections (
            id SERIAL PRIMARY KEY,
            folder_id INTEGER UNIQUE NOT NULL,
            installation_id BIGINT NOT NULL,
            account_login VARCHAR(255),
            account_type VARCHAR(50),
            repository_selection VARCHAR(20),
            repo_count INTEGER,
            default_repo VARCHAR(255),
            connected_by VARCHAR(255),
            channel_id VARCHAR(255),
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_github_connections_installation ON github_connections (installation_id);
        CREATE TABLE IF NOT EXISTS github_connect_links (
            nonce VARCHAR(80) PRIMARY KEY,
            folder_id INTEGER NOT NULL,
            channel_id VARCHAR(255),
            slack_user VARCHAR(255),
            created_by VARCHAR(255),
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            used_at TIMESTAMP WITH TIME ZONE
        );
    """)


def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            result = fn(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- connect links

def create_connect_link(
    folder_id: int, channel_id: Optional[str] = None, slack_user: Optional[str] = None, created_by: Optional[str] = None
) -> str:
    """Single-use, 15-minute link that starts the GitHub install for this client."""
    if not is_configured():
        raise GitHubAppError("GitHub connection isn't set up on JTS PowerTool yet. Please ask your JTS administrator.")
    nonce = secrets.token_urlsafe(32)

    def run(cur):
        cur.execute("SELECT id FROM channel_folders WHERE id = %s;", (folder_id,))
        if not cur.fetchone():
            raise GitHubAppError("That client was not found.")
        cur.execute("DELETE FROM github_connect_links WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 day';")
        cur.execute(
            """
            INSERT INTO github_connect_links (nonce, folder_id, channel_id, slack_user, created_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP + (%s || ' seconds')::interval);
            """,
            (nonce, folder_id, channel_id, slack_user, created_by, str(CONNECT_LINK_TTL_SECONDS)),
        )

    _db(run)
    return f"{public_base_url()}/api/github/connect?state={nonce}"


def peek_connect_link(nonce: str) -> Optional[Dict[str, Any]]:
    """Valid (unused, unexpired) link row, without consuming it."""
    if not nonce:
        return None

    def run(cur):
        cur.execute(
            "SELECT * FROM github_connect_links WHERE nonce = %s AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP;",
            (nonce,),
        )
        return cur.fetchone()

    row = _db(run)
    return dict(row) if row else None


def _consume_connect_link(nonce: str) -> Optional[Dict[str, Any]]:
    def run(cur):
        cur.execute(
            """
            UPDATE github_connect_links SET used_at = CURRENT_TIMESTAMP
            WHERE nonce = %s AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            RETURNING *;
            """,
            (nonce,),
        )
        return cur.fetchone()

    row = _db(run)
    return dict(row) if row else None


def install_url(nonce: str) -> str:
    return f"{GITHUB_WEB}/apps/{app_config()['slug']}/installations/new?state={nonce}"


# --------------------------------------------------------------------------- GitHub API helpers

def _app_jwt() -> str:
    import jwt  # PyJWT[crypto]

    cfg = app_config()
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": cfg["app_id"]}, cfg["private_key"], algorithm="RS256")


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh(method: str, path: str, token: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.request(method, f"{GITHUB_API}{path}", headers=_headers(token), **kwargs)


async def exchange_code_for_user_token(code: str) -> Dict[str, Any]:
    cfg = app_config()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{GITHUB_WEB}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            json={"client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "code": code},
        )
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or not data.get("access_token"):
        logger.warning(f"[GITHUB_APP] Code exchange failed: {resp.status_code} {data.get('error')}")
        raise GitHubAppError("GitHub didn't confirm the connection (the authorization expired). Please start again.")
    return data


async def user_has_installation(user_token: str, installation_id: int) -> bool:
    page = 1
    while page <= 10:
        resp = await _gh("GET", "/user/installations", user_token, params={"per_page": 100, "page": page})
        if resp.status_code != 200:
            return False
        items = resp.json().get("installations", [])
        if any(int(i.get("id", 0)) == int(installation_id) for i in items):
            return True
        if len(items) < 100:
            return False
        page += 1
    return False


async def get_installation(installation_id: int) -> Dict[str, Any]:
    resp = await _gh("GET", f"/app/installations/{installation_id}", _app_jwt())
    if resp.status_code != 200:
        raise GitHubAppError("GitHub couldn't find that installation. Please start again.")
    return resp.json()


async def get_installation_token(installation_id: int) -> str:
    """Fresh 1-hour token for this installation (cached until ~10 minutes before expiry)."""
    installation_id = int(installation_id)
    cached = _INSTALLATION_TOKEN_CACHE.get(installation_id)
    if cached and time.time() < cached[1] - _TOKEN_REFRESH_MARGIN_SECONDS:
        return cached[0]
    resp = await _gh("POST", f"/app/installations/{installation_id}/access_tokens", _app_jwt())
    if resp.status_code != 201:
        logger.warning(f"[GITHUB_APP] Token request failed for installation {installation_id}: {resp.status_code}")
        raise GitHubAppError("The GitHub connection isn't working (the app may have been removed). Please reconnect GitHub.")
    data = resp.json()
    expires = time.time() + 3600
    try:
        from datetime import datetime

        expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    _INSTALLATION_TOKEN_CACHE[installation_id] = (data["token"], expires)
    return data["token"]


async def list_installation_repos(installation_id: int) -> List[Dict[str, Any]]:
    token = await get_installation_token(installation_id)
    repos: List[Dict[str, Any]] = []
    page = 1
    while page <= 10:
        resp = await _gh("GET", "/installation/repositories", token, params={"per_page": 100, "page": page})
        if resp.status_code != 200:
            break
        items = resp.json().get("repositories", [])
        repos.extend(
            {"full_name": r["full_name"], "private": r.get("private", False), "default_branch": r.get("default_branch", "main")}
            for r in items
        )
        if len(items) < 100:
            break
        page += 1
    return repos


# --------------------------------------------------------------------------- connections

def get_connection(folder_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not folder_id:
        return None

    def run(cur):
        cur.execute("SELECT * FROM github_connections WHERE folder_id = %s;", (folder_id,))
        return cur.fetchone()

    row = _db(run)
    if not row:
        return None
    rec = dict(row)
    for k in ("created_at", "updated_at"):
        if rec.get(k) is not None and hasattr(rec[k], "isoformat"):
            rec[k] = rec[k].isoformat()
    return rec


def _save_connection(**fields) -> None:
    def run(cur):
        cur.execute(
            """
            INSERT INTO github_connections (folder_id, installation_id, account_login, account_type, repository_selection,
                                            repo_count, default_repo, connected_by, channel_id, status, updated_at)
            VALUES (%(folder_id)s, %(installation_id)s, %(account_login)s, %(account_type)s, %(repository_selection)s,
                    %(repo_count)s, %(default_repo)s, %(connected_by)s, %(channel_id)s, 'active', CURRENT_TIMESTAMP)
            ON CONFLICT (folder_id) DO UPDATE SET
                installation_id = EXCLUDED.installation_id,
                account_login = EXCLUDED.account_login,
                account_type = EXCLUDED.account_type,
                repository_selection = EXCLUDED.repository_selection,
                repo_count = EXCLUDED.repo_count,
                default_repo = EXCLUDED.default_repo,
                connected_by = EXCLUDED.connected_by,
                channel_id = EXCLUDED.channel_id,
                status = 'active',
                updated_at = CURRENT_TIMESTAMP;
            """,
            fields,
        )

    _db(run)


async def complete_connection(state: str, installation_id: Optional[str], code: Optional[str]) -> Dict[str, Any]:
    """Called when GitHub sends the user back after installing. Verifies everything, then saves."""
    if not state:
        raise GitHubAppError("Please start from Slack (type “@bot connect my github”) or from the client page in the dashboard.")
    link = _consume_connect_link(state)
    if not link:
        raise GitHubAppError("This connect link has expired or was already used. Ask the bot for a new one.")
    if not installation_id or not str(installation_id).isdigit():
        raise GitHubAppError("GitHub didn't say which installation was made. Please start again.")
    if not code:
        raise GitHubAppError(
            "GitHub didn't confirm who installed the app. Please start again and click “Authorize” when GitHub asks."
        )
    inst_id = int(installation_id)

    tokens = await exchange_code_for_user_token(code)
    # Prove the person who came back really has access to this installation (stops forged installation ids).
    if not await user_has_installation(tokens["access_token"], inst_id):
        raise GitHubAppError("The GitHub account that authorized doesn't have access to that installation.")

    inst = await get_installation(inst_id)
    repos = await list_installation_repos(inst_id)
    account = inst.get("account") or {}
    folder_id = int(link["folder_id"])

    _save_connection(
        folder_id=folder_id,
        installation_id=inst_id,
        account_login=account.get("login"),
        account_type=account.get("type"),
        repository_selection=inst.get("repository_selection"),
        repo_count=len(repos),
        default_repo=repos[0]["full_name"] if len(repos) == 1 else None,
        connected_by=link.get("slack_user") or link.get("created_by"),
        channel_id=link.get("channel_id"),
    )

    # Keep the user's refresh token (not the short-lived access token) in AWS Secrets Manager.
    if tokens.get("refresh_token"):
        try:
            from app.services.channel_secrets_service import store_folder_api_key

            store_folder_api_key(
                folder_id=folder_id,
                api_key=json.dumps({
                    "refresh_token": tokens["refresh_token"],
                    "refresh_token_expires_in": tokens.get("refresh_token_expires_in"),
                    "github_login": account.get("login"),
                    "saved_at": int(time.time()),
                }),
                provider=USER_TOKEN_PROVIDER,
                updated_by=link.get("slack_user") or link.get("created_by") or "github",
            )
        except Exception as e:
            logger.warning(f"[GITHUB_APP] Could not store user refresh token for folder {folder_id}: {e}")

    logger.info(f"[GITHUB_APP] Folder {folder_id} connected to {account.get('login')} (installation {inst_id}, {len(repos)} repos)")
    return {
        "folder_id": folder_id,
        "channel_id": link.get("channel_id"),
        "slack_user": link.get("slack_user"),
        "account_login": account.get("login"),
        "account_type": account.get("type"),
        "repository_selection": inst.get("repository_selection"),
        "repos": [r["full_name"] for r in repos],
        "default_repo": repos[0]["full_name"] if len(repos) == 1 else None,
    }


def set_default_repo(folder_id: int, full_name: Optional[str]) -> None:
    def run(cur):
        cur.execute(
            "UPDATE github_connections SET default_repo = %s, updated_at = CURRENT_TIMESTAMP WHERE folder_id = %s;",
            (full_name, folder_id),
        )

    _db(run)


def disconnect(folder_id: int) -> bool:
    """Unlinks the client's GitHub here. (The app stays installed on GitHub until they uninstall it there.)"""
    conn = get_connection(folder_id)

    def run(cur):
        cur.execute("DELETE FROM github_connections WHERE folder_id = %s RETURNING installation_id;", (folder_id,))
        return cur.fetchone()

    row = _db(run)
    if conn:
        _INSTALLATION_TOKEN_CACHE.pop(int(conn["installation_id"]), None)
    try:
        from app.services.channel_secrets_service import delete_folder_api_key

        delete_folder_api_key(folder_id, USER_TOKEN_PROVIDER)
    except Exception as e:
        logger.warning(f"[GITHUB_APP] Could not delete stored GitHub user token for folder {folder_id}: {e}")
    return bool(row)


# --------------------------------------------------------------------------- webhooks

def verify_webhook_signature(body: bytes, signature_header: Optional[str]) -> bool:
    secret = app_config()["webhook_secret"]
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def handle_webhook(event: str, payload: Dict[str, Any]) -> str:
    installation = payload.get("installation") or {}
    inst_id = installation.get("id")
    action = payload.get("action")
    if not inst_id:
        return "ignored"

    if event == "installation" and action in ("deleted", "suspend", "unsuspend"):
        status = {"deleted": "removed", "suspend": "suspended", "unsuspend": "active"}[action]

        def run(cur):
            cur.execute(
                "UPDATE github_connections SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE installation_id = %s;",
                (status, inst_id),
            )

        _db(run)
        _INSTALLATION_TOKEN_CACHE.pop(int(inst_id), None)
        logger.info(f"[GITHUB_APP] Installation {inst_id} -> {status}")
        return status

    if event == "installation_repositories":
        added = len(payload.get("repositories_added") or [])
        removed = len(payload.get("repositories_removed") or [])

        def run(cur):
            cur.execute(
                """
                UPDATE github_connections
                SET repo_count = GREATEST(COALESCE(repo_count, 0) + %s - %s, 0),
                    repository_selection = COALESCE(%s, repository_selection),
                    updated_at = CURRENT_TIMESTAMP
                WHERE installation_id = %s;
                """,
                (added, removed, payload.get("repository_selection"), inst_id),
            )

        _db(run)
        return "repos_updated"

    return "ignored"


# --------------------------------------------------------------------------- runtime token resolution

async def resolve_github_token(channel_id: str, **channel_ctx) -> Dict[str, Any]:
    """
    Which GitHub credentials the bot uses in this channel:
      1. a channel's own stored GitHub token (legacy)            -> source "channel"
      2. the client's GitHub App connection (1-hour token)        -> source "client_app"
      3. nothing                                                  -> source "none" (caller decides about JTS's token)
    Returns {"token", "source", "folder_id", "default_repo", "account_login", "error"}.
    """
    from app.services.channel_secrets_service import get_channel_secret_value, get_folder_id_for_channel

    result: Dict[str, Any] = {"token": None, "source": "none", "folder_id": None, "default_repo": None, "account_login": None, "error": None}
    try:
        legacy = get_channel_secret_value(channel_id, "github", **channel_ctx)
    except Exception:
        legacy = None
    if legacy:
        result.update(token=legacy, source="channel")
        return result

    folder_id = get_folder_id_for_channel(channel_id)
    result["folder_id"] = folder_id
    conn = get_connection(folder_id)
    if not conn:
        return result
    if conn.get("status") != "active":
        result["error"] = "This client's GitHub connection was removed or suspended on GitHub. Please reconnect GitHub."
        return result
    try:
        result.update(
            token=await get_installation_token(conn["installation_id"]),
            source="client_app",
            default_repo=conn.get("default_repo"),
            account_login=conn.get("account_login"),
        )
    except GitHubAppError as e:
        result["error"] = str(e)
    return result


async def github_client_for_channel(channel_id: str):
    """
    GitHub MCP client for running an approved action from this channel, using the same credentials the bot used.
    Raises GitHubAppError if the client's connection is broken, or if a connection is required but missing.
    """
    from app.tools.mcp_client import GitHubMCPClient

    ctx = await resolve_github_token(channel_id)
    if ctx.get("error"):
        raise GitHubAppError(ctx["error"])
    if ctx.get("token"):
        return GitHubMCPClient(token=ctx["token"])
    if ctx.get("folder_id") and str(get_secret("GITHUB_REQUIRE_CLIENT_CONNECTION", "false")).strip().lower() in ("1", "true", "yes"):
        raise GitHubAppError("This client hasn't connected their GitHub yet. Ask the bot to “connect my github”.")
    return GitHubMCPClient()
