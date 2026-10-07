"""
Per-user tool permissions (GitHub and Jira checkboxes on the Users page) and the check the bot runs before it
reads or changes anything for someone.

How it works
  * Each dashboard user has nine permissions (stored as JSON in dashboard_users.tool_permissions; empty = role defaults).
  * When someone asks the bot for a GitHub / Jira action, we look up their Slack email and match it to a dashboard user.
  * JTS Admins may do everything. Client users may only act in channels of their own client, and only with the
    permissions ticked for them.
  * A Slack person who can't be matched to a dashboard user may read but not make changes
    (set secret TOOL_PERMISSIONS_UNMATCHED=allow to let them change things, TOOL_PERMISSIONS_ENFORCE=false to turn checks off).
"""
import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from app.db.session import get_db_connection
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

# key, group, label, description
PERMISSIONS = [
    ("github_read", "GitHub", "Can read repositories", "Read issues, files, code, commits and pull requests"),
    ("github_issues", "GitHub", "Can create and comment on issues", "Create issues, update issues and add comments"),
    ("github_code", "GitHub", "Can commit code and files", "Create or update files, push files and create branches"),
    ("github_prs", "GitHub", "Can open pull requests", "Open pull requests"),
    ("github_sites", "GitHub", "Can publish and edit websites", "Publish a built website and propose changes to a published site"),
    ("jira_read", "Jira", "Can read tickets", "Search tickets and read their details"),
    ("jira_create", "Jira", "Can create tickets", "Create new Jira tickets"),
    ("jira_edit", "Jira", "Can edit and comment", "Change a ticket's title, description, priority, labels and add comments"),
    ("jira_close", "Jira", "Can move or close tickets", "Change a ticket's status (for example to Done)"),
]
PERMISSION_KEYS = [p[0] for p in PERMISSIONS]
LABELS = {p[0]: p[2] for p in PERMISSIONS}
READ_KEYS = {"github_read", "jira_read"}

# Before the GitHub change-permissions were split, one box "github_write" covered all of these. Saved settings that still
# carry it are expanded, so nobody gains or loses access when the boxes were split.
LEGACY_GITHUB_WRITE = "github_write"
GITHUB_WRITE_KEYS = ("github_issues", "github_code", "github_prs", "github_sites")

TOOL_PERMISSION: Dict[str, str] = {
    # GitHub reads
    "get_file_contents": "github_read", "list_issues": "github_read", "get_issue": "github_read",
    "search_repositories": "github_read", "search_code": "github_read", "search_issues": "github_read",
    "list_commits": "github_read", "get_commit": "github_read", "list_pull_requests": "github_read",
    "get_pull_request": "github_read", "get_issue_comments": "github_read",
    "list_my_websites": "github_read", "start_site_edit": "github_read",
    # GitHub changes
    "create_issue": "github_issues", "update_issue": "github_issues", "add_issue_comment": "github_issues",
    "create_or_update_file": "github_code", "push_files": "github_code", "create_branch": "github_code",
    "create_pull_request": "github_prs",
    "publish_website": "github_sites", "propose_site_changes": "github_sites",
    # Jira
    "jira_search_issues": "jira_read", "jira_get_issue": "jira_read", "jira_list_projects": "jira_read",
    "jira_create_issue": "jira_create",
    "jira_update_issue": "jira_edit", "jira_add_comment": "jira_edit",
    "jira_transition_issue": "jira_close",
}

_column_ready = False
_EMAIL_CACHE: Dict[str, Tuple[Optional[str], float]] = {}
EMAIL_CACHE_SECONDS = 600


def required_permission(tool_name: str) -> Optional[str]:
    return TOOL_PERMISSION.get(tool_name)


def defaults_for_role(role: Optional[str]) -> Dict[str, bool]:
    """JTS Admin and Client Admin: everything. Team Member: read-only."""
    if role in ("jts_admin", "admin", "client_admin"):
        return {k: True for k in PERMISSION_KEYS}
    return {k: (k in READ_KEYS) for k in PERMISSION_KEYS}


def effective_permissions(stored: Any, role: Optional[str]) -> Dict[str, bool]:
    """Role defaults overridden by the saved checkboxes. JTS Admins always have everything."""
    perms = defaults_for_role(role)
    if role in ("jts_admin", "admin"):
        return perms
    if isinstance(stored, str):
        try:
            import json
            stored = json.loads(stored)
        except ValueError:
            stored = None
    if isinstance(stored, dict):
        legacy = stored.get(LEGACY_GITHUB_WRITE)
        if isinstance(legacy, bool):
            for k in GITHUB_WRITE_KEYS:
                if not isinstance(stored.get(k), bool):
                    perms[k] = legacy
        for k in PERMISSION_KEYS:
            if isinstance(stored.get(k), bool):
                perms[k] = stored[k]
    return perms


def clean_permissions(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, bool]]:
    """Validates what the dashboard sends. Unknown keys are refused; returns None when nothing was sent."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("Tool permissions must be a list of on/off values.")
    out: Dict[str, bool] = {}
    for k, v in data.items():
        if k != LEGACY_GITHUB_WRITE and k not in PERMISSION_KEYS:
            raise ValueError(f"Unknown permission '{k}'.")
        if not isinstance(v, bool):
            raise ValueError(f"Permission '{k}' must be on or off.")
        out[k] = v
    # An old client may still send the single "can make changes" box: spread it over the finer ones.
    if LEGACY_GITHUB_WRITE in out:
        legacy = out.pop(LEGACY_GITHUB_WRITE)
        for k in GITHUB_WRITE_KEYS:
            out.setdefault(k, legacy)
    return out


def ensure_column(cur) -> None:
    global _column_ready
    if _column_ready:
        return
    cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS tool_permissions JSONB;")
    cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS disabled BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS access_expires_at DATE;")
    _column_ready = True


def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    email = (email or "").strip().lower()
    if not email:
        return None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            ensure_column(cur)
            conn.commit()
            cur.execute(
                "SELECT id, username, role, client_folder_id, tool_permissions, disabled, access_expires_at FROM dashboard_users WHERE LOWER(TRIM(email)) = %s LIMIT 1;",
                (email,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


async def slack_email(slack_user_id: str, bot_token: str) -> Optional[str]:
    """The person's Slack email (needs the users:read.email scope), cached for a few minutes."""
    cached = _EMAIL_CACHE.get(slack_user_id)
    if cached and time.time() < cached[1]:
        return cached[0]
    token = (bot_token or get_secret("SLACK_BOT_TOKEN", "") or "").strip()
    email = None
    if token:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get("https://slack.com/api/users.info", params={"user": slack_user_id},
                                        headers={"Authorization": f"Bearer {token}"})
            data = resp.json()
            if data.get("ok"):
                email = ((data.get("user") or {}).get("profile") or {}).get("email") or None
                if not email:
                    logger.warning(
                        f"[PERMISSIONS] Slack returned no email for {slack_user_id}. The Slack app probably lacks the "
                        "users:read.email scope (add it under OAuth & Permissions and reinstall), or this person hides their email."
                    )
            else:
                logger.warning(f"[PERMISSIONS] users.info failed for {slack_user_id}: {data.get('error')}")
        except Exception as e:
            logger.warning(f"[PERMISSIONS] Could not read the Slack email of {slack_user_id}: {type(e).__name__}")
    if email:  # don't cache failures: a transient error shouldn't block someone for minutes
        _EMAIL_CACHE[slack_user_id] = (email, time.time() + EMAIL_CACHE_SECONDS)
    return email


def _flag(name: str, default: str) -> str:
    return str(get_secret(name, default) or default).strip().lower()


async def check_tool(tool_name: str, slack_user_id: str, channel_id: str, bot_token: str = "") -> Tuple[bool, str]:
    """(allowed, message_for_the_person_if_not)."""
    perm = required_permission(tool_name)
    if not perm:
        return True, ""
    if _flag("TOOL_PERMISSIONS_ENFORCE", "true") in ("0", "false", "no", "off"):
        return True, ""
    if not slack_user_id or slack_user_id in ("unknown-user", "U_TEST_USER"):
        return True, ""  # internal calls without a person (never from Slack)

    from app.services.channel_secrets_service import get_folder_id_for_channel

    email = await slack_email(slack_user_id, bot_token)
    user = None
    try:
        user = find_user_by_email(email) if email else None
    except Exception as e:
        logger.error(f"[PERMISSIONS] User lookup failed: {type(e).__name__}")
        # A broken lookup must not silently open changes: reads stay possible, changes are refused.
        return (perm in READ_KEYS), "I couldn't check your permissions just now, so I can't make changes. Please try again in a minute."

    if not user:
        if email:
            masked = (email[:2] + "***@" + email.split("@")[-1]) if "@" in email else "***"
            logger.warning(f"[PERMISSIONS] Slack email {masked} for {slack_user_id} matches no dashboard user (check the email on the Users page).")
        if perm in READ_KEYS or _flag("TOOL_PERMISSIONS_UNMATCHED", "block") == "allow":
            return True, ""
        return False, (
            "I couldn't match your Slack account to a dashboard user, so I can only read, not make changes. Ask your JTS "
            "administrator to add you under Users with the same email you use in Slack."
        )

    from app.services import access_control

    ended = access_control.inactive_reason(user.get("disabled"), user.get("access_expires_at"))
    if ended:
        return False, access_control.reason_message(ended).replace("Please contact", "I can't do that for you. Please contact")

    role = user.get("role")
    if role in ("jts_admin", "admin"):
        return True, ""
    folder_id = get_folder_id_for_channel(channel_id)
    if not folder_id or not user.get("client_folder_id") or int(user["client_folder_id"]) != int(folder_id):
        return False, "Your account belongs to a different client than this channel, so I can't do that for you here."
    if effective_permissions(user.get("tool_permissions"), role).get(perm):
        return True, ""
    return False, (
        f"You don't have permission to do that (\"{LABELS[perm]}\" is switched off for you). "
        "Ask your administrator to turn it on under Users."
    )
