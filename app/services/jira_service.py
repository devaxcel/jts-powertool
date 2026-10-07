"""
Jira connection per client (folder), via ONE Atlassian OAuth 2.0 (3LO) app registered by JTS.

Each client connects their own Jira Cloud site with one click (from Slack or the dashboard). We keep only the
site id in the database; the user's rotating refresh token lives in AWS Secrets Manager (provider "jira_oauth").
For every action we mint a 1-hour access token. Reads run directly, changes need a human approval first.
"""
import json
import logging
import re
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.db.session import get_db_connection
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
API_BASE = "https://api.atlassian.com/ex/jira"
SCOPES = "read:jira-work write:jira-work read:jira-user offline_access"
CONNECT_LINK_TTL_SECONDS = 15 * 60
TOKEN_PROVIDER = "jira_oauth"  # folder_api_keys provider holding the refresh token (hidden from the keys list)
MAX_TEXT = 12000
SEARCH_FIELDS = "summary,status,assignee,priority,issuetype,project,updated,labels,reporter"

# Tools the bot may use. Reads run directly; writes go to a human approval.
JIRA_READ_TOOLS = {"jira_search_issues", "jira_get_issue", "jira_list_projects"}
JIRA_WRITE_TOOLS = {"jira_create_issue", "jira_update_issue", "jira_add_comment", "jira_transition_issue"}

_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,19}-\d{1,9}$")
_PROJECT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,19}$")

_ACCESS_CACHE: Dict[str, Tuple[str, float]] = {}  # cloud_id -> (token, expires_epoch)
_REFRESH_LOCK = threading.Lock()


class JiraError(Exception):
    """A problem we can explain to the user in plain words."""


# --------------------------------------------------------------------------- config

def app_config() -> Dict[str, str]:
    return {
        "client_id": (get_secret("JIRA_OAUTH_CLIENT_ID", "") or "").strip(),
        "client_secret": (get_secret("JIRA_OAUTH_CLIENT_SECRET", "") or "").strip(),
    }


def is_configured() -> bool:
    cfg = app_config()
    return bool(cfg["client_id"] and cfg["client_secret"])


def public_base_url() -> str:
    return (get_secret("PUBLIC_BASE_URL", "https://journeys.pe") or "https://journeys.pe").rstrip("/")


def redirect_uri() -> str:
    return f"{public_base_url()}/api/jira/callback"


# --------------------------------------------------------------------------- tables

def _ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jira_connections (
            id SERIAL PRIMARY KEY,
            folder_id INTEGER UNIQUE NOT NULL,
            cloud_id VARCHAR(80) NOT NULL,
            site_url VARCHAR(255),
            site_name VARCHAR(255),
            account_name VARCHAR(255),
            default_project VARCHAR(40),
            connected_by VARCHAR(255),
            channel_id VARCHAR(255),
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        ALTER TABLE jira_connections ADD COLUMN IF NOT EXISTS auto_rules BOOLEAN DEFAULT TRUE;
        CREATE TABLE IF NOT EXISTS jira_linked_issues (
            id SERIAL PRIMARY KEY,
            folder_id INTEGER NOT NULL,
            channel_id VARCHAR(255) NOT NULL,
            issue_key VARCHAR(40) NOT NULL,
            summary VARCHAR(300),
            created_by VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (channel_id, issue_key)
        );
        CREATE TABLE IF NOT EXISTS jira_connect_links (
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
    """Single-use, 15-minute link that starts the Jira connection for this client."""
    if not is_configured():
        raise JiraError("Jira connection isn't set up on JTS PowerTool yet. Please ask your JTS administrator.")
    nonce = secrets.token_urlsafe(32)

    def run(cur):
        cur.execute("SELECT id FROM channel_folders WHERE id = %s;", (folder_id,))
        if not cur.fetchone():
            raise JiraError("That client was not found.")
        cur.execute("DELETE FROM jira_connect_links WHERE expires_at < CURRENT_TIMESTAMP - INTERVAL '1 day';")
        cur.execute(
            """
            INSERT INTO jira_connect_links (nonce, folder_id, channel_id, slack_user, created_by, expires_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP + (%s || ' seconds')::interval);
            """,
            (nonce, folder_id, channel_id, slack_user, created_by, str(CONNECT_LINK_TTL_SECONDS)),
        )

    _db(run)
    return f"{public_base_url()}/api/jira/connect?state={nonce}"


def peek_connect_link(nonce: str) -> Optional[Dict[str, Any]]:
    if not nonce:
        return None

    def run(cur):
        cur.execute(
            "SELECT * FROM jira_connect_links WHERE nonce = %s AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP;",
            (nonce,),
        )
        return cur.fetchone()

    row = _db(run)
    return dict(row) if row else None


def _consume_connect_link(nonce: str) -> Optional[Dict[str, Any]]:
    def run(cur):
        cur.execute(
            """
            UPDATE jira_connect_links SET used_at = CURRENT_TIMESTAMP
            WHERE nonce = %s AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP
            RETURNING *;
            """,
            (nonce,),
        )
        return cur.fetchone()

    row = _db(run)
    return dict(row) if row else None


def authorize_url(nonce: str) -> str:
    from urllib.parse import urlencode

    return AUTH_URL + "?" + urlencode({
        "audience": "api.atlassian.com",
        "client_id": app_config()["client_id"],
        "scope": SCOPES,
        "redirect_uri": redirect_uri(),
        "state": nonce,
        "response_type": "code",
        "prompt": "consent",
    })


# --------------------------------------------------------------------------- Atlassian OAuth

async def _token_request(payload: Dict[str, str]) -> Dict[str, Any]:
    cfg = app_config()
    body = {**payload, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"]}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(TOKEN_URL, json=body)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200 or not data.get("access_token"):
        err = data.get("error") or f"HTTP {resp.status_code}"
        logger.warning(f"[JIRA] Token request failed: {err} {data.get('error_description', '')[:120]}")
        raise JiraError("invalid_grant" if err == "invalid_grant" else "Atlassian didn't accept the sign-in. Please try again.")
    return data


async def exchange_code(code: str) -> Dict[str, Any]:
    try:
        return await _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()})
    except JiraError as e:
        raise JiraError("Atlassian didn't accept the sign-in code. Please start again from Slack or the dashboard.") from e


async def list_sites(access_token: str) -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(RESOURCES_URL, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    if resp.status_code != 200:
        raise JiraError("Couldn't read which Jira sites this account can use. Please try again.")
    return [s for s in resp.json() if isinstance(s, dict) and s.get("id")]


# --------------------------------------------------------------------------- connection storage

def get_connection(folder_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not folder_id:
        return None

    def run(cur):
        cur.execute("SELECT * FROM jira_connections WHERE folder_id = %s;", (folder_id,))
        return cur.fetchone()

    row = _db(run)
    if not row:
        return None
    rec = dict(row)
    for k in ("created_at", "updated_at"):  # JSON-safe for the dashboard
        if rec.get(k) is not None and hasattr(rec[k], "isoformat"):
            rec[k] = rec[k].isoformat()
    return rec


def _save_connection(**f) -> None:
    def run(cur):
        cur.execute(
            """
            INSERT INTO jira_connections (folder_id, cloud_id, site_url, site_name, account_name, connected_by, channel_id, status)
            VALUES (%(folder_id)s, %(cloud_id)s, %(site_url)s, %(site_name)s, %(account_name)s, %(connected_by)s, %(channel_id)s, 'active')
            ON CONFLICT (folder_id) DO UPDATE SET
                cloud_id = EXCLUDED.cloud_id, site_url = EXCLUDED.site_url, site_name = EXCLUDED.site_name,
                account_name = EXCLUDED.account_name, connected_by = EXCLUDED.connected_by,
                channel_id = COALESCE(EXCLUDED.channel_id, jira_connections.channel_id),
                default_project = CASE WHEN jira_connections.cloud_id = EXCLUDED.cloud_id THEN jira_connections.default_project END,
                status = 'active', updated_at = CURRENT_TIMESTAMP;
            """,
            f,
        )

    _db(run)


def _set_status(folder_id: int, status: str) -> None:
    def run(cur):
        cur.execute("UPDATE jira_connections SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE folder_id = %s;", (status, folder_id))

    _db(run)


def set_auto_rules(folder_id: int, enabled: bool) -> None:
    """Turns the automatic ticket rules on or off for a client (changes still need approval either way)."""
    def run(cur):
        cur.execute("UPDATE jira_connections SET auto_rules = %s, updated_at = CURRENT_TIMESTAMP WHERE folder_id = %s;", (bool(enabled), folder_id))

    _db(run)


def auto_rules_enabled(conn: Optional[Dict[str, Any]]) -> bool:
    return bool(conn) and conn.get("auto_rules") is not False


def record_linked_issue(folder_id: int, channel_id: str, issue_key: str, summary: str = "", created_by: str = "") -> None:
    """Remembers a ticket the assistant raised from this channel, so later messages can update the right ticket."""
    if not (folder_id and channel_id and issue_key):
        return

    def run(cur):
        cur.execute(
            """
            INSERT INTO jira_linked_issues (folder_id, channel_id, issue_key, summary, created_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (channel_id, issue_key) DO UPDATE SET summary = EXCLUDED.summary;
            """,
            (folder_id, channel_id, issue_key, (summary or "")[:300], created_by or None),
        )

    try:
        _db(run)
    except Exception as e:
        logger.warning(f"[JIRA] Could not remember {issue_key} for {channel_id}: {e}")


def recent_linked_issues(channel_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    if not channel_id:
        return []

    def run(cur):
        cur.execute(
            "SELECT issue_key, summary FROM jira_linked_issues WHERE channel_id = %s ORDER BY id DESC LIMIT %s;",
            (channel_id, limit),
        )
        return cur.fetchall() or []

    try:
        return [dict(r) for r in _db(run)]
    except Exception as e:
        logger.debug(f"[JIRA] Could not read linked issues for {channel_id}: {e}")
        return []


_CLAIMS_APPROVAL = re.compile(
    r"(awaiting|pending|waiting\s+for)\s+(your\s+|human\s+)?approval"
    r"|(sent|submitted|posted|asked|requested)\s+(it\s+|this\s+|that\s+)?(for|to)\s+(your\s+)?approval"
    r"|(i\'?m|i\s+am|i\s+will|i\'?ll)\s+(now\s+)?(proposing|propose|creating|create)\s+(a\s+|the\s+)?(jira\s+)?(ticket|issue)"
    r"|proposing\s+to\s+(create|update|add|move|comment)",
    re.IGNORECASE,
)


def claims_approval(text: str) -> bool:
    """True when a reply talks about sending something for approval (used to catch a claim made without calling the tool)."""
    return bool(text and _CLAIMS_APPROVAL.search(text))


RETRY_NOTE = (
    "[SYSTEM NOTE] Your last reply described a Jira proposal, but you did NOT call the tool, so NO approval card was created and "
    "nothing was sent. Call the matching jira_* tool now (jira_create_issue, jira_update_issue, jira_add_comment or "
    "jira_transition_issue) with the details you described. Then reply with ONE short line. "
    "If you did not actually intend to propose any Jira change, reply with exactly: NOOP"
)


AUTOMATION_RULES = (
    "\n\nJIRA AUTOMATION RULES (this client has them switched on). Jira is the system of record for tasks, decisions, goals and "
    "requirements. Use the `jira_*` tools on your own initiative, following these rules. Every change still goes to a person for "
    "approval, so proposing is safe, but do not spam.\n"
    "CREATE a ticket (`jira_create_issue`) when: (a) a task is confirmed (the user agrees to do something, e.g. 'yes, let's do that', "
    "'go ahead', 'assign it to me'), (b) a multi-step project or piece of work starts, or (c) the user explicitly asks. "
    "Do NOT create tickets for casual questions, brainstorming, or things that are already finished in this reply. "
    "Before creating, call `jira_search_issues` for similar open tickets; if one exists, use or update it instead of creating a duplicate. "
    "Never create more than 3 tickets in one reply. Write a clear summary and a description that explains the goal and the 'why'.\n"
    "UPDATE tickets when: (a) status changes (work started -> `jira_transition_issue` to In Progress; finished -> Done), "
    "(b) a decision is made (add a comment with the decision and the reason), or (c) a blocker is identified (add a comment starting "
    "'Blocker:' with what is blocking and who can unblock it). Only update a ticket you know the key of (see the list below, "
    "the conversation, or search for it).\n"
    "COMMENTS must capture context and the 'why' (what was decided, what changed, what is next), not just 'status updated'.\n"
    "HOW TO PROPOSE: proposing means CALLING the tool (`jira_create_issue`, `jira_update_issue`, `jira_add_comment`, `jira_transition_issue`) "
    "in this same reply. The approval card appears only because you called it. NEVER write that you are 'proposing', 'awaiting approval' "
    "or 'asking for approval' unless you have just called the tool; do not describe the ticket and wait for the user to say 'ok'. "
    "Call the tool first, then add the one-line note. If you are missing a detail, make a sensible choice (default project, type Task) "
    "rather than asking.\n"
    "After proposing, say in ONE short line what you proposed (e.g. 'I've asked for approval to create a ticket: ...'). "
    "Mention ticket keys like KAN-12 whenever you refer to a ticket. If no project is set and none was named, ask for the project key once."
)


def automation_prompt(conn: Optional[Dict[str, Any]], channel_id: str) -> str:
    """Extra system-prompt text that turns the Jira rules on for this channel (empty when they are off)."""
    if not conn or conn.get("status") != "active" or not auto_rules_enabled(conn):
        return ""
    text = AUTOMATION_RULES
    if conn.get("default_project"):
        text += f"\nDefault project for new tickets: {conn['default_project']}."
    linked = recent_linked_issues(channel_id)
    if linked:
        lines = "\n".join(f"- {r['issue_key']}: {r.get('summary') or ''}".rstrip() for r in linked)
        text += f"\nTickets already raised from this channel (newest first):\n{lines}"
    return text


def set_default_project(folder_id: int, key: Optional[str]) -> None:
    def run(cur):
        cur.execute("UPDATE jira_connections SET default_project = %s, updated_at = CURRENT_TIMESTAMP WHERE folder_id = %s;", (key, folder_id))

    _db(run)


def _store_refresh_token(folder_id: int, refresh_token: str, updated_by: str) -> None:
    from app.services.channel_secrets_service import store_folder_api_key

    store_folder_api_key(
        folder_id=folder_id,
        api_key=json.dumps({"refresh_token": refresh_token, "saved_at": int(time.time())}),
        provider=TOKEN_PROVIDER,
        updated_by=updated_by,
    )


def _load_refresh_token(folder_id: int) -> Optional[str]:
    from app.services.channel_secrets_service import get_folder_api_key_value

    raw = get_folder_api_key_value(folder_id, TOKEN_PROVIDER)
    if not raw:
        return None
    try:
        return json.loads(raw).get("refresh_token")
    except Exception:
        return None


async def complete_connection(state: str, code: Optional[str]) -> Dict[str, Any]:
    """Called when Atlassian sends the user back. Verifies the single-use link, then saves the connection."""
    if not state:
        raise JiraError("Please start from Slack (type “@bot connect my jira”) or from the Keys & Connections page.")
    link = _consume_connect_link(state)
    if not link:
        raise JiraError("This connect link has expired or was already used. Ask the bot for a new one.")
    if not code:
        raise JiraError("Atlassian didn't confirm the sign-in. Please start again and click “Accept”.")

    tokens = await exchange_code(code)
    sites = await list_sites(tokens["access_token"])
    if not sites:
        raise JiraError("That Atlassian account has no Jira Cloud site the app can use. Check you accepted access to your site.")
    site = sites[0]  # the user picks the site on Atlassian's consent screen; if several, use the first
    folder_id = int(link["folder_id"])
    who = link.get("slack_user") or link.get("created_by") or "jira"
    if not tokens.get("refresh_token"):
        raise JiraError("Atlassian didn't allow ongoing access. Please try again and accept all the permissions.")

    _store_refresh_token(folder_id, tokens["refresh_token"], who)
    _save_connection(
        folder_id=folder_id, cloud_id=site["id"], site_url=site.get("url"), site_name=site.get("name"),
        account_name=None, connected_by=link.get("slack_user") or link.get("created_by"), channel_id=link.get("channel_id"),
    )
    _ACCESS_CACHE[site["id"]] = (tokens["access_token"], time.time() + int(tokens.get("expires_in", 3600)) - 120)
    logger.info(f"[JIRA] Folder {folder_id} connected to {site.get('url')} ({len(sites)} site(s) offered)")
    return {
        "folder_id": folder_id, "channel_id": link.get("channel_id"), "slack_user": link.get("slack_user"),
        "site_url": site.get("url"), "site_name": site.get("name"), "site_count": len(sites),
    }


def disconnect(folder_id: int) -> bool:
    conn = get_connection(folder_id)

    def run(cur):
        cur.execute("DELETE FROM jira_connections WHERE folder_id = %s RETURNING cloud_id;", (folder_id,))
        return cur.fetchone()

    row = _db(run)
    if conn:
        _ACCESS_CACHE.pop(conn["cloud_id"], None)
    try:
        from app.services.channel_secrets_service import delete_folder_api_key

        delete_folder_api_key(folder_id, TOKEN_PROVIDER)
    except Exception as e:
        logger.warning(f"[JIRA] Could not delete stored Jira token for folder {folder_id}: {e}")
    return bool(row)


# --------------------------------------------------------------------------- runtime access

async def _access_token(conn: Dict[str, Any]) -> str:
    cloud_id, folder_id = conn["cloud_id"], int(conn["folder_id"])
    cached = _ACCESS_CACHE.get(cloud_id)
    if cached and time.time() < cached[1]:
        return cached[0]
    refresh = _load_refresh_token(folder_id)
    if not refresh:
        _set_status(folder_id, "needs_reconnect")
        raise JiraError("This client's Jira connection needs to be set up again. Ask the bot to “connect my jira”.")
    try:
        tokens = await _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    except JiraError as e:
        if str(e) == "invalid_grant":
            _set_status(folder_id, "needs_reconnect")
            raise JiraError("Jira access expired or was removed. Please connect Jira again (“connect my jira”).") from e
        raise
    if tokens.get("refresh_token") and tokens["refresh_token"] != refresh:  # Atlassian rotates refresh tokens
        with _REFRESH_LOCK:
            _store_refresh_token(folder_id, tokens["refresh_token"], "jira-refresh")
    _ACCESS_CACHE[cloud_id] = (tokens["access_token"], time.time() + int(tokens.get("expires_in", 3600)) - 120)
    return tokens["access_token"]


async def resolve_connection(channel_id: str) -> Dict[str, Any]:
    """The active connection for the client that owns this channel. Raises JiraError with a plain message if none."""
    from app.services.channel_secrets_service import get_folder_id_for_channel

    folder_id = get_folder_id_for_channel(channel_id)
    if not folder_id:
        raise JiraError("This Slack channel isn't linked to a client yet.")
    conn = get_connection(folder_id)
    if not conn:
        raise JiraError("This client hasn't connected Jira yet. Call connect_jira to post a Connect Jira button.")
    if conn.get("status") != "active":
        raise JiraError("This client's Jira connection needs to be set up again. Call connect_jira to post a new button.")
    return conn


async def _api(conn: Dict[str, Any], method: str, path: str, **kwargs) -> httpx.Response:
    token = await _access_token(conn)
    url = f"{API_BASE}/{conn['cloud_id']}/rest/api/2{path}"  # always the connected site, never a model-supplied host
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        resp = await client.request(
            method, url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, **kwargs
        )
    if resp.status_code == 401:
        _ACCESS_CACHE.pop(conn["cloud_id"], None)
        raise JiraError("Jira didn't accept the saved access. Ask the bot to “connect my jira” again.")
    return resp


def _err_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        parts = list(data.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (data.get("errors") or {}).items()]
        if parts:
            return "; ".join(parts)[:600]
    except ValueError:
        pass
    return f"HTTP {resp.status_code}"


def _check_key(key: str) -> str:
    key = (key or "").strip().upper()
    if not _ISSUE_KEY.match(key):
        raise JiraError(f"'{key}' isn't a Jira issue key (it looks like ABC-123).")
    return key


def _check_project(key: str) -> str:
    key = (key or "").strip().upper()
    if not _PROJECT_KEY.match(key):
        raise JiraError(f"'{key}' isn't a Jira project key (it looks like ABC).")
    return key


def _user_name(u: Optional[Dict[str, Any]]) -> str:
    return (u or {}).get("displayName") or "Unassigned"


def _summarise(issue: Dict[str, Any], base_url: str) -> str:
    f = issue.get("fields") or {}
    return (
        f"{issue.get('key')}: {f.get('summary')}\n"
        f"  status: {(f.get('status') or {}).get('name')} | type: {(f.get('issuetype') or {}).get('name')} | "
        f"priority: {(f.get('priority') or {}).get('name') or '-'} | assignee: {_user_name(f.get('assignee'))} | "
        f"updated: {(f.get('updated') or '')[:10]}\n  {base_url}/browse/{issue.get('key')}"
    )


# --------------------------------------------------------------------------- tools (read)

async def run_read_tool(channel_id: str, tool: str, args: Dict[str, Any]) -> Tuple[str, bool]:
    try:
        conn = await resolve_connection(channel_id)
        base = (conn.get("site_url") or "").rstrip("/")
        if tool == "jira_search_issues":
            jql = str(args.get("jql") or "").strip()
            if not jql:
                return ("Give a JQL query, e.g. project = ABC AND status != Done ORDER BY updated DESC.", True)
            limit = max(1, min(int(args.get("max_results") or 15), 30))
            resp = await _api(conn, "GET", "/search/jql", params={"jql": jql, "maxResults": limit, "fields": SEARCH_FIELDS})
            if resp.status_code != 200:
                return (f"Jira search failed: {_err_text(resp)}", True)
            issues = resp.json().get("issues") or []
            if not issues:
                return ("No issues matched that search.", False)
            return (f"{len(issues)} issue(s):\n\n" + "\n\n".join(_summarise(i, base) for i in issues), False)
        if tool == "jira_get_issue":
            key = _check_key(args.get("issue_key", ""))
            resp = await _api(conn, "GET", f"/issue/{key}", params={"fields": SEARCH_FIELDS + ",description,comment"})
            if resp.status_code != 200:
                return (f"Couldn't read {key}: {_err_text(resp)}", True)
            issue = resp.json()
            f = issue.get("fields") or {}
            comments = ((f.get("comment") or {}).get("comments") or [])[-5:]
            out = _summarise(issue, base) + f"\n\nDescription:\n{(f.get('description') or '(none)')}"
            if comments:
                out += "\n\nLatest comments:\n" + "\n".join(f"- {_user_name(c.get('author'))}: {c.get('body')}" for c in comments)
            return (out[:MAX_TEXT], False)
        if tool == "jira_list_projects":
            resp = await _api(conn, "GET", "/project/search", params={"maxResults": 50, "orderBy": "name"})
            if resp.status_code != 200:
                return (f"Couldn't list projects: {_err_text(resp)}", True)
            projects = resp.json().get("values") or []
            return ("\n".join(f"{p.get('key')}: {p.get('name')}" for p in projects) or "No projects found.", False)
    except JiraError as e:
        return (str(e), True)
    except httpx.TimeoutException:
        return ("Jira didn't answer in time. Try again.", True)
    except Exception as e:
        logger.error(f"[JIRA] {tool} failed: {e}", exc_info=True)
        return ("Something went wrong talking to Jira.", True)
    return (f"Unknown Jira tool '{tool}'.", True)


# --------------------------------------------------------------------------- tools (write, after approval)

def clean_write_args(tool: str, args: Dict[str, Any], default_project: Optional[str] = None) -> Dict[str, Any]:
    """Validates a change request BEFORE it goes to approval. Raises JiraError with a message for the model."""
    a: Dict[str, Any] = {}
    if tool == "jira_create_issue":
        project = args.get("project") or default_project
        if not project:
            raise JiraError("Which Jira project? Ask the user for the project key (call jira_list_projects if unsure).")
        a["project"] = _check_project(project)
        a["summary"] = str(args.get("summary") or "").strip()[:250]
        if not a["summary"]:
            raise JiraError("An issue needs a summary (title).")
        a["issue_type"] = str(args.get("issue_type") or "Task").strip()[:60]
        a["description"] = str(args.get("description") or "")[:MAX_TEXT]
        if args.get("priority"):
            a["priority"] = str(args["priority"]).strip()[:40]
        if isinstance(args.get("labels"), list):
            a["labels"] = [re.sub(r"\s+", "-", str(x).strip())[:50] for x in args["labels"] if str(x).strip()][:10]
    elif tool == "jira_update_issue":
        a["issue_key"] = _check_key(args.get("issue_key", ""))
        for k, limit in (("summary", 250), ("description", MAX_TEXT), ("priority", 40)):
            if args.get(k) is not None:
                a[k] = str(args[k])[:limit]
        if isinstance(args.get("labels"), list):
            a["labels"] = [re.sub(r"\s+", "-", str(x).strip())[:50] for x in args["labels"] if str(x).strip()][:10]
        if len(a) == 1:
            raise JiraError("Say what to change: summary, description, priority or labels.")
    elif tool == "jira_add_comment":
        a["issue_key"] = _check_key(args.get("issue_key", ""))
        a["comment"] = str(args.get("comment") or "").strip()[:MAX_TEXT]
        if not a["comment"]:
            raise JiraError("The comment is empty.")
    elif tool == "jira_transition_issue":
        a["issue_key"] = _check_key(args.get("issue_key", ""))
        a["status"] = str(args.get("status") or "").strip()[:80]
        if not a["status"]:
            raise JiraError("Which status should the issue move to (e.g. In Progress, Done)?")
    else:
        raise JiraError(f"Unknown Jira tool '{tool}'.")
    return a


async def execute_write(channel_id: str, tool: str, args: Dict[str, Any]) -> Tuple[str, bool]:
    """Runs an approved change with the client's own connection."""
    try:
        conn = await resolve_connection(channel_id)
        base = (conn.get("site_url") or "").rstrip("/")
        a = clean_write_args(tool, args)  # re-validate: never trust stored arguments blindly
        if tool == "jira_create_issue":
            fields: Dict[str, Any] = {
                "project": {"key": a["project"]}, "summary": a["summary"], "issuetype": {"name": a["issue_type"]},
            }
            if a.get("description"):
                fields["description"] = a["description"]
            if a.get("priority"):
                fields["priority"] = {"name": a["priority"]}
            if a.get("labels"):
                fields["labels"] = a["labels"]
            resp = await _api(conn, "POST", "/issue", json={"fields": fields})
            if resp.status_code not in (200, 201):
                return (f"[Jira Error]: Couldn't create the issue: {_err_text(resp)}", True)
            key = resp.json().get("key")
            record_linked_issue(conn.get("folder_id"), channel_id, key, a["summary"])
            return (f"Created {key}: {base}/browse/{key}", False)
        if tool == "jira_update_issue":
            fields = {}
            if "summary" in a:
                fields["summary"] = a["summary"]
            if "description" in a:
                fields["description"] = a["description"]
            if "priority" in a:
                fields["priority"] = {"name": a["priority"]}
            if "labels" in a:
                fields["labels"] = a["labels"]
            resp = await _api(conn, "PUT", f"/issue/{a['issue_key']}", json={"fields": fields})
            if resp.status_code not in (200, 204):
                return (f"[Jira Error]: Couldn't update {a['issue_key']}: {_err_text(resp)}", True)
            return (f"Updated {a['issue_key']}: {base}/browse/{a['issue_key']}", False)
        if tool == "jira_add_comment":
            resp = await _api(conn, "POST", f"/issue/{a['issue_key']}/comment", json={"body": a["comment"]})
            if resp.status_code not in (200, 201):
                return (f"[Jira Error]: Couldn't comment on {a['issue_key']}: {_err_text(resp)}", True)
            return (f"Comment added to {a['issue_key']}: {base}/browse/{a['issue_key']}", False)
        if tool == "jira_transition_issue":
            resp = await _api(conn, "GET", f"/issue/{a['issue_key']}/transitions")
            if resp.status_code != 200:
                return (f"[Jira Error]: Couldn't read the workflow of {a['issue_key']}: {_err_text(resp)}", True)
            options = resp.json().get("transitions") or []
            want = a["status"].lower()
            match = next((t for t in options if want in ((t.get("name") or "").lower(), ((t.get("to") or {}).get("name") or "").lower())), None)
            if not match:
                names = ", ".join(sorted({(t.get("to") or {}).get("name") or t.get("name") for t in options})) or "none"
                return (f"[Jira Error]: {a['issue_key']} can't move to '{a['status']}' from its current status. Available: {names}.", True)
            resp = await _api(conn, "POST", f"/issue/{a['issue_key']}/transitions", json={"transition": {"id": match["id"]}})
            if resp.status_code not in (200, 204):
                return (f"[Jira Error]: Couldn't move {a['issue_key']}: {_err_text(resp)}", True)
            return (f"Moved {a['issue_key']} to {(match.get('to') or {}).get('name') or a['status']}: {base}/browse/{a['issue_key']}", False)
    except JiraError as e:
        return (f"[Jira Error]: {e}", True)
    except httpx.TimeoutException:
        return ("[Jira Error]: Jira didn't answer in time. Nothing was changed; try again.", True)
    except Exception as e:
        logger.error(f"[JIRA] {tool} failed: {e}", exc_info=True)
        return ("[Jira Error]: Something went wrong talking to Jira.", True)
    return (f"[Jira Error]: Unknown tool {tool}.", True)


async def list_projects(folder_id: int) -> List[Dict[str, str]]:
    conn = get_connection(folder_id)
    if not conn or conn.get("status") != "active":
        return []
    resp = await _api(conn, "GET", "/project/search", params={"maxResults": 100, "orderBy": "name"})
    if resp.status_code != 200:
        raise JiraError(f"Couldn't list Jira projects: {_err_text(resp)}")
    return [{"key": p.get("key"), "name": p.get("name")} for p in resp.json().get("values") or []]
