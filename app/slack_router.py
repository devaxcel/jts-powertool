import asyncio
import json
import logging
import os
import re
import uuid
import traceback
import hmac
import hashlib
import time
from typing import Optional, Dict, List, Any
import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse

from app.claude import stream
from app.db.repositories import (
    record_processed_activity,
    enqueue_job,
    get_pending_approval,
    claim_approval_for_execution,
    finalize_approval_execution,
    reject_approval,
    update_pending_approval_status,
    save_slack_workspace,
    get_slack_workspace_bot_user_id,
    get_all_slack_bot_user_ids,
)
from app.db.session import get_db_connection
from app.log_stream import emit_telemetry
from app.memory_manager import (
    save_conversation_message,
    get_rag_thread_context,
    get_thread_participants,
)
from app.tools.secrets_manager import get_secret, get_slack_bot_token, clear_secrets_cache
from app.tools.mcp_client import GitHubMCPClient
from app.tools.slack_approval_ui import (
    build_approved_card_blocks,
    build_rejected_card_blocks,
    build_expired_card_blocks,
    build_diff_modal_view,
)

load_dotenv()
logger = logging.getLogger(__name__)
slack_router = APIRouter(prefix="/api/slack", tags=["slack"])


@slack_router.get("/install", summary="Redirect to Slack OAuth authorization")
async def slack_install_redirect(delete_messages: bool = False):
    """Redirects administrator to Slack OAuth v2 authorization page to install bot in a workspace."""
    from fastapi.responses import RedirectResponse
    client_id = get_secret("SLACK_CLIENT_ID", "203729176583.11900714527686").strip() or "203729176583.11900714527686"
    redirect_uri = "https://journeys.pe/api/slack/oauth/callback"
    scopes = "app_mentions:read,channels:history,channels:read,chat:write,files:read,files:write,groups:history,groups:read,im:history,im:read,mpim:history,mpim:read,users:read"
    install_url = f"https://slack.com/oauth/v2/authorize?client_id={client_id}&scope={scopes}&redirect_uri={redirect_uri}"
    if delete_messages:
        # An admin of the workspace also allows the bot to delete messages that contain a key (their user token is kept
        # for this workspace only).
        install_url += "&user_scope=chat:write"
    return RedirectResponse(url=install_url)


@slack_router.get("/oauth/callback", summary="Slack OAuth v2 installation callback")
async def slack_oauth_callback(code: Optional[str] = None, error: Optional[str] = None):
    """
    Handles Slack OAuth v2 redirect when installing the bot into any Slack workspace.
    Exchanges authorization code for Bot User OAuth Token and registers the workspace.
    """
    if error:
        logger.error(f"[SLACK_OAUTH] Installation error reported by Slack: {error}")
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head><title>Installation Cancelled</title></head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f8fafc;">
                <div style="background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); max-width: 480px; text-align: center;">
                    <div style="font-size: 48px; margin-bottom: 16px;">❌</div>
                    <h2 style="color: #e11d48; margin: 0 0 12px 0;">Installation Cancelled</h2>
                    <p style="color: #64748b; font-size: 14px; line-height: 1.5;">Slack reported: <code>{error}</code></p>
                </div>
            </body>
            </html>
            """,
            status_code=400,
        )

    if not code:
        return HTMLResponse(
            content="<h3>Missing authorization code from Slack.</h3>",
            status_code=400,
        )

    client_id = get_secret("SLACK_CLIENT_ID", "203729176583.11900714527686").strip() or "203729176583.11900714527686"
    client_secret = get_secret("SLACK_CLIENT_SECRET", "6a13e27063c46dc0ed1f2d01c5687368").strip() or "6a13e27063c46dc0ed1f2d01c5687368"
    redirect_uri = "https://journeys.pe/api/slack/oauth/callback"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            data = resp.json()

        if not data.get("ok"):
            err = data.get("error", "oauth_failed")
            logger.error(f"[SLACK_OAUTH] OAuth exchange failed: {err}")
            return HTMLResponse(
                content=f"""
                <!DOCTYPE html>
                <html>
                <head><title>Connection Failed</title></head>
                <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f8fafc;">
                    <div style="background: white; padding: 40px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); max-width: 480px; text-align: center;">
                        <div style="font-size: 48px; margin-bottom: 16px;">⚠️</div>
                        <h2 style="color: #e11d48; margin: 0 0 12px 0;">Authentication Failed</h2>
                        <p style="color: #64748b; font-size: 14px; line-height: 1.5;">Could not exchange token with Slack: <code>{err}</code></p>
                    </div>
                </body>
                </html>
                """,
                status_code=400,
            )

        access_token = data.get("access_token")
        team_info = data.get("team", {})
        team_id = team_info.get("id", "")
        team_name = team_info.get("name", "Slack Workspace")
        bot_user_id = data.get("bot_user_id", "")

        logger.info(f"[SLACK_OAUTH] Successfully authorized team '{team_name}' ({team_id}), bot_user='{bot_user_id}'")

        # 1. Save in PostgreSQL slack_workspaces table
        save_slack_workspace(
            team_id=team_id,
            team_name=team_name,
            bot_token=access_token,
            bot_user_id=bot_user_id,
        )

        # 2. Save in AWS Secrets Manager / Vault bundle
        try:
            from app.services.channel_secrets_service import store_vault_secret
            store_vault_secret(
                key_name=f"SLACK_BOT_TOKEN_{team_id.upper()}",
                key_value=access_token,
            )
        except Exception as aws_err:
            logger.warning(f"[SLACK_OAUTH] Could not persist token to AWS Secrets Manager: {aws_err}")

        # 2b. Optional: an admin also allowed deleting key messages in this workspace
        delete_note = ""
        authed = data.get("authed_user") or {}
        if authed.get("access_token") and "chat:write" in str(authed.get("scope") or ""):
            from app.services import slack_key_capture as _skc

            enabled, delete_msg = await _skc.store_workspace_delete_token(team_id, authed.get("id", ""), authed["access_token"], access_token)
            delete_note = (
                f'<p style="margin: 8px 0 0 0;">{"✅" if enabled else "⚠️"} {delete_msg}</p>'
            )

        # 3. Clear in-memory secrets cache
        clear_secrets_cache()

        emit_telemetry(
            action="WORKSPACE_CONNECTED",
            category="SLACK",
            level="INFO",
            message=f"Slack workspace '{team_name}' ({team_id}) connected via OAuth installation.",
        )

        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Workspace Connected - JTS PowerTool</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f1f5f9;">
                <div style="background: white; padding: 48px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.06); max-width: 520px; text-align: center; border: 1px solid #e2e8f0;">
                    <div style="display: inline-flex; align-items: center; justify-content: center; width: 64px; height: 64px; background: #ecfdf5; border-radius: 50%; color: #059669; font-size: 32px; margin-bottom: 20px;">
                        ✓
                    </div>
                    <h1 style="color: #0f172a; margin: 0 0 10px 0; font-size: 24px; font-weight: 700;">Workspace Connected!</h1>
                    <p style="color: #475569; font-size: 15px; line-height: 1.6; margin: 0 0 24px 0;">
                        <strong>{team_name}</strong> (<code>{team_id}</code>) has been successfully connected to <strong>JTS PowerTool</strong>.
                    </p>
                    <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; margin-bottom: 24px; text-align: left; font-size: 13px; color: #64748b;">
                        <p style="margin: 0 0 6px 0;">🤖 Bot User: <strong>@{bot_user_id}</strong></p>
                        <p style="margin: 0;">🔒 Bot Token encrypted and stored in AWS Secrets Manager &amp; PostgreSQL.</p>
                        {delete_note}
                    </div>
                    <p style="color: #94a3b8; font-size: 13px; margin: 0;">You can now close this browser tab and return to Slack.</p>
                </div>
            </body>
            </html>
            """
        )
    except Exception as ex:
        logger.error(f"[SLACK_OAUTH] Exception during OAuth callback: {ex}", exc_info=True)
        return HTMLResponse(
            content=f"<h3>Internal error processing Slack OAuth callback: {str(ex)}</h3>",
            status_code=500,
        )



def verify_slack_signature(body_bytes: bytes, headers: dict) -> bool:
    """Verifies that the incoming request signature matches Slack's signing secret."""
    signing_secret = get_secret("SLACK_SIGNING_SECRET", "").strip()
    if not signing_secret:
        return True  # If signing secret is not configured, pass through (e.g. testing)
    
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    if not timestamp or not signature:
        return False
        
    # Guard against replay attacks
    try:
        if abs(time.time() - float(timestamp)) > 60 * 5:
            return False
    except (ValueError, TypeError):
        return False

    sig_basestring = f"v0:{timestamp}:{body_bytes.decode('utf-8')}".encode("utf-8")
    expected_signature = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        sig_basestring,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_signature, signature)

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


def get_system_prompt(user_prompt: str = "", user_profile: Optional[dict] = None) -> str:
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())
    date_str = now_utc.strftime("%A, %B %d, %Y")
    time_str = now_utc.strftime("%H:%M UTC")

    default_repo = get_secret("GITHUB_DEFAULT_REPO", "devaxcel/jts-powertool")

    user_tz_context = ""
    if user_profile and isinstance(user_profile, dict):
        tz = user_profile.get("tz")
        tz_label = user_profile.get("tz_label")
        if tz or tz_label:
            user_tz_context = f" (User Timezone: {tz or 'Unknown'} / {tz_label or 'N/A'})"
    
    prompt = (
        "You are a helpful AI assistant in Slack.\n"
        f"CURRENT REAL-TIME CONTEXT: Today's date is {date_str} (Current time: {time_str}){user_tz_context}. Current Unix epoch timestamp: {now_ts}. Always use this exact real-time date and year ({now_utc.year}) when answering date-related questions or searching for current news.\n"
        "LANGUAGE RULE: ALWAYS communicate strictly and entirely in clear, professional English. NEVER mix languages, switch to Roman Urdu/Hindi, or output Urdu script, unless the user explicitly requests you to speak or respond in a different language.\n"
        "SLACK FORMATTING RULES:\n"
        "- Slack mrkdwn uses SINGLE asterisks for bold text: `*bold text*`. NEVER use double asterisks `**text**`.\n"
        "- Slack uses single underscores for italics: `_italic text_`.\n"
        "- Slack uses single backticks for inline code: `code`.\n"
        f"- DYNAMIC DATE & TIME LOCALIZATION: When mentioning specific dates, timestamps, deadlines, or times of day, ALWAYS format them using Slack's native localized date formatting syntax: `<!date^UNIX_TIMESTAMP^{{date_pretty}} at {{time}}|FALLBACK_STRING>` (e.g. `<!date^{now_ts}^{{date_pretty}} at {{time}}|{date_str} at {time_str}>`). Slack automatically translates this timestamp into the local time and timezone of whoever is reading the message (e.g. USA, Europe, Pakistan, Asia).\n"
        "- Use clean Slack mrkdwn syntax in all your responses.\n"
        "You have access to the conversation memory of this channel/thread. "
        "You can analyze documents, Word files (.docx/.doc), spreadsheets (.xlsx/.csv), presentations (.pptx), archives, images, code, and text files attached by users.\n\n"
        "WEB BROWSING & LIVE SEARCH CAPABILITY:\n"
        "You are equipped with live web browsing and internet search tools:\n"
        "- `fetch_url_content(url)`: Use this tool whenever the user provides a link or asks you to visit, read, inspect, or extract information from a URL or webpage (e.g. documentation, articles, websites, APIs).\n"
        "- `search_web(query)`: Use this tool to search the internet whenever the user asks for current information, latest news, links, or whenever you need fresh web data to answer accurately.\n"
        "NEVER tell the user you cannot visit URLs, browse the web, or that you do not know the current date. You have full capability and permission to call `fetch_url_content` and `search_web` autonomously."
    )
    
    prompt_lower = (user_prompt or "").lower()
    
    # Dynamic System Prompting: Attach GitHub module only when relevant keywords are present in user prompt
    github_pattern = r'\b(github|repo|repository|issue|issues|commit|pr|pull request|branch|push)\b'
    if user_prompt and re.search(github_pattern, prompt_lower):
        prompt += (
            "\n\nGITHUB REPOSITORY CAPABILITY:\n"
            "You have full read, write, and update access to GitHub via the GitHub Model Context Protocol (MCP) server tools. "
            f"The team's default repository is: `{default_repo}`.\n"
            "You are authorized and equipped to:\n"
            "- Read: inspect open/closed issues, read file contents, search code/repositories, and list commits.\n"
            "- Write & Update: create new issues (`create_issue`), update existing issues (`update_issue`), add comments (`add_issue_comment`), create or update files (`create_or_update_file`), push commits (`push_files`), create pull requests (`create_pull_request`), and create branches (`create_branch`).\n"
            "CRITICAL INSTRUCTION: When a user asks you to check issues, create an issue, update files, or push code, you MUST immediately call the appropriate GitHub tool directly! NEVER output text claiming 'An interactive approval card has been posted' without calling the tool! The approval card is automatically generated only when you actually invoke the tool.\n\n"
            "GITHUB CODE & FILE CREATION RULES:\n"
            "- CRITICAL TOOL CALL GUARD: Only invoke GitHub file creation/update tools (`create_or_update_file`, `push_files`, `create_pull_request`) when the user EXPLICITLY asks to update GitHub, commit code, create a file in the repository, or mentions 'GitHub', 'repo', 'repository', 'commit', 'PR', or 'push'.\n"
            "- If a user asks a general question, asks to see a flow diagram, picture, explanation, or diagram without explicitly requesting a GitHub file commit/update, respond directly in Slack chat (or generate a file using ```generate_file:filename.ext) rather than calling GitHub write tools.\n"
            "- When a user DOES explicitly ask to create, build, or write code in a GitHub repository, create the file using `create_or_update_file` (or `push_files`) with the full, working implementation.\n"
            "- YOU MUST ALWAYS provide the complete, functional file source code in the 'content' parameter and a commit message in the 'message' parameter! NEVER leave 'content' empty or call the tool without code. For an HTML/CSS/JS page, write the full HTML, styled CSS, and interactive JavaScript directly inside 'content'.\n"
            "- If `create_or_update_file` returns an error indicating that 'content' is required, immediately write out the COMPLETE file source code and call `create_or_update_file` again with the full content so the user receives the interactive approval card with the complete code.\n"
            "- DO NOT attempt to call `create_branch` before creating files. Especially on new or empty repositories, Git strictly forbids creating branches when there are 0 commits. Always create the files directly on the default branch.\n"
            "- If the user mentions a specific repository name (e.g., 'test-repository'), be sure to set `repo: 'test-repository'` in the tool call arguments."
        )

    # Dynamic System Prompting: Attach File Generation module only when relevant keywords are present in user prompt
    file_gen_pattern = r'\b(generate|export|download|pdf|excel|word|docx|xlsx|csv|pptx|file)\b'
    if user_prompt and re.search(file_gen_pattern, prompt_lower):
        prompt += (
            "\n\nFILE GENERATION CAPABILITY:\n"
            "You have the capability to directly generate and upload any type of file into Slack (PDF, Word .docx, Excel .xlsx/.csv, PowerPoint .pptx, Markdown .md, Text .txt, HTML, JSON, code, etc.). "
            "NEVER tell the user you cannot generate files or suggest external tools! "
            "Whenever a user asks to generate, create, export, or download a file, you MUST provide the file content inside a code block marked with ```generate_file:filename.ext\n"
            "Example for PDF:\n"
            "```generate_file:summary.pdf\n"
            "# Title of Document\n"
            "Content with headings, bullet points, and paragraphs...\n"
            "```\n"
            "Example for Word (.docx):\n"
            "```generate_file:report.docx\n"
            "# Report Title\n"
            "Report content...\n"
            "```\n"
            "Example for Excel (.xlsx or .csv):\n"
            "```generate_file:data.xlsx\n"
            "Name,Score,Status\n"
            "Alice,95,Active\n"
            "```\n"
            "Include a friendly message in your response confirming you have created and attached the file. The system will automatically compile and upload the file to Slack."
        )

    # Jira guidance (always short): stops "issues in KAN" from going to the GitHub tools
    prompt += (
        "\n\nJIRA vs GITHUB:\n"
        "If tools starting with `jira_` are available, this client uses Jira. Requests about Jira, tickets, backlog, sprints, a Jira "
        "project key (e.g. KAN, WEB) or an issue key (e.g. KAN-12) MUST use the `jira_*` tools, NEVER the GitHub issue tools. "
        "'Issues in <PROJECT KEY>' means Jira. Use the GitHub issue tools only when the user clearly means a GitHub repository "
        "(they name a repo/owner, or say GitHub, PR, commit or branch). "
        "CRITICAL: to create, update, comment on or move a Jira issue you MUST actually call the matching tool "
        "(`jira_create_issue`, `jira_update_issue`, `jira_add_comment`, `jira_transition_issue`). NEVER say that something "
        "was 'sent for approval' or that a card was posted unless you really called the tool in this turn; the approval card "
        "is created only by the tool call. If no project is given and the tool says one is needed, ask the user for the project key. "
        "If the user asks to connect Jira, call `connect_jira`. Never ask for Jira passwords or API tokens. "
        "If a tool returns PERMISSION DENIED, tell the user the reason plainly and stop; never look for another way around it.\n\n"
        "API KEYS: keys are saved by the system itself, never by you. A message that is only `KEY_NAME = value` (one per line; add "
        "'for this channel only' to limit it to the channel) is saved for the client and the message is deleted automatically. "
        "If the user asks how to add a key, tell them exactly that format. You must NEVER repeat, store or use a key that appears in "
        "chat. If they prefer a form, call `add_api_key` (pass only the key's NAME) to post a secure dashboard button. If a key was "
        "pasted in some other way and is still visible, tell them to delete that message and, if it was real, rotate it. "
        "To list saved keys ALWAYS call `list_saved_keys` (never answer from conversation memory) and never show or guess a value: "
        "values can't be retrieved by anyone. There is NO slash command for keys; never invent commands or features."
    )

    return prompt


SYSTEM_PROMPT = get_system_prompt()
TENANT_ID = "T5ZMF56H5"
_KEY_TASKS: set = set()  # keeps background key-handling tasks alive until they finish

_USER_PROFILES: dict[str, dict[str, Any]] = {}


async def get_slack_user_profile(user_id: str, token: str) -> dict[str, Any]:
    """Fetch user's real name, display name, and timezone info from Slack profile with in-memory cache."""
    if not user_id or user_id in ["unknown-user", "U_TEST_USER", "U1", "U001", "U002", "U0123"]:
        return {"name": user_id, "display_name": user_id, "real_name": user_id, "tz": "", "tz_label": "", "tz_offset": 0}
    
    if user_id in _USER_PROFILES:
        return _USER_PROFILES[user_id]
        
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://slack.com/api/users.info",
                params={"user": user_id},
                headers={"Authorization": f"Bearer {token}"}
            )
            data = resp.json()
            if data.get("ok"):
                u = data.get("user", {})
                profile = u.get("profile", {})
                real_name = profile.get("real_name") or u.get("real_name") or profile.get("display_name") or user_id
                display_name = profile.get("display_name") or u.get("name") or real_name
                tz = u.get("tz") or profile.get("tz") or ""
                tz_label = u.get("tz_label") or profile.get("tz_label") or ""
                tz_offset = u.get("tz_offset") or profile.get("tz_offset") or 0
                info = {
                    "name": display_name,
                    "display_name": display_name,
                    "real_name": real_name,
                    "first_name": profile.get("first_name") or real_name.split()[0] if real_name else "User",
                    "tz": tz,
                    "tz_label": tz_label,
                    "tz_offset": tz_offset,
                }
                _USER_PROFILES[user_id] = info
                return info
            else:
                logger.warning(f"Slack users.info error for user {user_id}: {data.get('error')}")
    except Exception as e:
        logger.warning(f"Failed to fetch Slack profile for user {user_id}: {e}")
        
    fallback = {"name": user_id, "display_name": user_id, "real_name": user_id, "tz": "", "tz_label": "", "tz_offset": 0}
    _USER_PROFILES[user_id] = fallback
    return fallback


_BOT_USER_IDS: dict[str, str] = {}


async def get_bot_user_id(token: str, team_id: Optional[str] = None) -> str:
    """Fetch and cache our bot's own Slack user ID per workspace token and persist to DB."""
    if not token:
        return ""
    if token in _BOT_USER_IDS:
        return _BOT_USER_IDS[token]
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {token}"}
            )
            data = resp.json()
            if data.get("ok"):
                b_id = data.get("user_id", "")
                t_id = data.get("team_id") or team_id
                t_name = data.get("team", "")
                if b_id:
                    _BOT_USER_IDS[token] = b_id
                if t_id and b_id:
                    try:
                        save_slack_workspace(team_id=t_id, team_name=t_name or t_id, bot_token=token, bot_user_id=b_id)
                    except Exception:
                        pass
                return b_id
    except Exception as e:
        logger.warning(f"Failed to fetch bot user ID: {e}")
    return ""




def is_valid_uuid(val: str) -> bool:
    if not val:
        return False
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def get_existing_claude_session(tenant_id: str, conv_id: str, thread_id: str) -> str | None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT claude_session_id 
                FROM conversation_sessions 
                WHERE teams_tenant_id = %s 
                  AND teams_conversation_id = %s 
                  AND teams_thread_id = %s;
                """,
                (tenant_id, conv_id, thread_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            val = row.get("claude_session_id") if isinstance(row, dict) else row[0]
            if val and is_valid_uuid(val):
                return str(val)
            return None
    except Exception as e:
        emit_telemetry(
            action="DB_ERROR",
            category="DATABASE",
            level="ERROR",
            thread_id=thread_id,
            message=f"Failed to lookup session: {e}",
        )
        return None
    finally:
        conn.close()


def save_claude_session(tenant_id: str, conv_id: str, thread_id: str, user_id: str, claude_session_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_sessions (
                    team_id,
                    channel_id,
                    thread_ts,
                    user_id,
                    claude_session_id,
                    status,
                    created_at,
                    updated_at,
                    teams_tenant_id,
                    teams_conversation_id,
                    teams_thread_id,
                    teams_user_id
                )
                VALUES (%s, %s, %s, %s, %s, 'active', now(), now(), %s, %s, %s, %s)
                ON CONFLICT (teams_tenant_id, teams_conversation_id, teams_thread_id)
                DO UPDATE SET 
                    claude_session_id = EXCLUDED.claude_session_id,
                    team_id = EXCLUDED.team_id,
                    channel_id = EXCLUDED.channel_id,
                    thread_ts = EXCLUDED.thread_ts,
                    user_id = EXCLUDED.user_id,
                    updated_at = now();
                """,
                (tenant_id, conv_id, thread_id, user_id, claude_session_id, tenant_id, conv_id, thread_id, user_id),
            )
            conn.commit()
            emit_telemetry(
                action="SESSION_CREATED",
                category="DATABASE",
                level="INFO",
                thread_id=thread_id,
                session_id=claude_session_id,
                user_id=user_id,
                message=f"Mapped Slack thread {thread_id} to Claude session {claude_session_id}",
            )
    except Exception as e:
        emit_telemetry(
            action="DB_ERROR",
            category="DATABASE",
            level="ERROR",
            thread_id=thread_id,
            session_id=claude_session_id,
            message=f"Failed to save session mapping: {e}",
        )
    finally:
        conn.close()


async def process_slack_turn(
    channel_id: str,
    thread_ts: str,
    raw_text: str,
    user_id: str,
    message_id: str,
    reply_in_thread: bool = False,
    team_id: Optional[str] = None,
):
    ws_id = (team_id or TENANT_ID).strip()

    # 1. Deduplication check
    is_new = record_processed_activity(ws_id, message_id)
    if not is_new:
        emit_telemetry(
            action="DUPLICATE_IGNORED",
            category="DEDUPLICATION",
            level="WARNING",
            thread_id=thread_ts,
            event_id=message_id,
            user_id=user_id,
            message=f"Duplicate event rejected (event_id={message_id}). Claude invocation skipped.",
        )
        return

    # 2. Clean prompt and resolve user identity
    cleaned_prompt = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()
    if not cleaned_prompt:
        cleaned_prompt = "Hello"

    token = get_slack_bot_token(ws_id)
    profile = await get_slack_user_profile(user_id, token)
    user_display = profile.get("real_name") or profile.get("display_name") or user_id
    user_annotated_prompt = f"[{user_display} (User ID: {user_id})]: {cleaned_prompt}"

    # 3. Lookup existing session
    conv_id = f"slack-{channel_id}"
    thread_id = thread_ts or ""
    existing_session_id = get_existing_claude_session(ws_id, conv_id, thread_id)

    if existing_session_id:
        emit_telemetry(
            action="SESSION_RESUMED",
            category="CLAUDE",
            level="INFO",
            thread_id=thread_id,
            session_id=existing_session_id,
            event_id=message_id,
            user_id=user_id,
            message=f"Resuming existing Claude session for thread {thread_id} (Speaker: {user_display} / {user_id})",
        )
    else:
        emit_telemetry(
            action="SESSION_INITIALIZING",
            category="CLAUDE",
            level="INFO",
            thread_id=thread_id,
            event_id=message_id,
            user_id=user_id,
            message=f"Initializing new Claude session for thread {thread_id} (Speaker: {user_display} / {user_id})",
        )

    # 4. Retrieve local RAG thread history from PostgreSQL (top 5 semantic + latest 5)
    history_messages, rag_meta = get_rag_thread_context(
        channel_id=channel_id,
        thread_ts=thread_id,
        current_input=cleaned_prompt,
        top_k_semantic=5,
        top_k_recent=5,
    )

    # Format the exact requested RAG audit breakdown
    semantic_lines = []
    for idx, match in enumerate(rag_meta.get("semantic_matches", []), 1):
        txt = match.get("text", "").replace("\n", " ")
        if len(txt) > 55:
            txt = txt[:52] + "..."
        sim = match.get("similarity", 0.85)
        semantic_lines.append(f'{idx}. "{txt}"       similarity: {sim}')
    
    if not semantic_lines:
        semantic_text = "   (No prior semantic matches in thread)"
    else:
        semantic_text = "\n".join(semantic_lines)

    rag_breakdown_msg = (
        f"[RAG]\n"
        f'Query: "{cleaned_prompt}"\n\n'
        f"Semantic search:\n"
        f"{semantic_text}\n\n"
        f"Recent messages retrieved: {rag_meta.get('recent_count', 0)}\n\n"
        f"Total context messages sent to Claude: {rag_meta.get('total_context_sent', 0)}"
    )

    emit_telemetry(
        action="RAG_CONTEXT_RETRIEVED",
        category="RAG",
        level="INFO",
        thread_id=thread_id,
        session_id=existing_session_id,
        event_id=message_id,
        user_id=user_id,
        message=rag_breakdown_msg,
        extra=rag_meta,
    )

    # Resolve channel_name using workspace-specific token
    channel_name = None
    try:
        from app.services.channel_secrets_service import resolve_slack_channel_name
        channel_name = resolve_slack_channel_name(channel_id, token=token, team_id=ws_id)
    except Exception:
        pass

    # 5. Stream response from Claude Engine with local RAG context
    try:
        accumulated_text = ""
        last_usage_info = {}
        async for message in stream(
            user_message=user_annotated_prompt,
            system_prompt=get_system_prompt(cleaned_prompt, user_profile=profile),
            session_id=existing_session_id,
            model=MODEL,
            context_messages=history_messages,
            channel_id=channel_id,
            channel_name=channel_name,
            user_id=user_id,
            workspace_id=ws_id,
        ):
            if hasattr(message, "role"):
                if message.role == "system" and isinstance(message.content, dict):
                    data_obj = message.content.get("data", {})
                    real_session_id = data_obj.get("session_id")
                    if real_session_id and is_valid_uuid(real_session_id) and real_session_id != existing_session_id:
                        save_claude_session(ws_id, conv_id, thread_id, user_id, str(real_session_id))
                    if data_obj.get("usage"):
                        last_usage_info = data_obj["usage"]
                elif message.role == "assistant" and isinstance(message.content, str):
                    accumulated_text += message.content
            elif isinstance(message, str):
                accumulated_text += message

        # 6. Send Slack Message
        if not accumulated_text:
            accumulated_text = "I received your message, but no response was generated."

        token = get_slack_bot_token(ws_id)
        if not token:
            emit_telemetry(
                action="CONFIG_ERROR",
                category="SLACK",
                level="ERROR",
                thread_id=thread_id,
                message=f"Slack Bot Token missing for workspace '{ws_id}'.",
            )
            return

        post_kwargs = {
            "channel": channel_id,
            "text": accumulated_text,
        }
        if reply_in_thread and thread_ts and not thread_ts.startswith("dm_"):
            post_kwargs["thread_ts"] = thread_ts

        sent_ts = None
        async with httpx.AsyncClient(timeout=20.0) as http_client:
            resp = await http_client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8"
                },
                json=post_kwargs
            )
            resp_data = resp.json()
            if not resp_data.get("ok"):
                err_msg = resp_data.get("error", "Unknown Slack error")
                emit_telemetry(
                    action="SLACK_API_ERROR",
                    category="SLACK",
                    level="ERROR",
                    thread_id=thread_id,
                    session_id=existing_session_id,
                    event_id=message_id,
                    message=f"Slack API error: {err_msg}",
                )
            else:
                sent_ts = resp_data.get("ts")
                emit_telemetry(
                    action="RESPONSE_DISPATCHED",
                    category="SLACK",
                    level="INFO",
                    thread_id=thread_id,
                    session_id=existing_session_id,
                    event_id=message_id,
                    message=f"Successfully delivered response to Slack channel {channel_id} (ts={sent_ts})",
                )

        # 7. Save assistant response to local memory with Slack message_ts and usage stats
        if accumulated_text:
            save_conversation_message(
                team_id=ws_id,
                workspace_id=ws_id,
                channel_id=channel_id,
                thread_ts=post_kwargs.get("thread_ts") or sent_ts or thread_id,
                user_id="bot",
                user_name="JTS Powertool Agent",
                role="assistant",
                content=accumulated_text,
                message_ts=sent_ts,
                input_tokens=last_usage_info.get("input_tokens", 0),
                output_tokens=last_usage_info.get("output_tokens", 0),
                total_tokens=last_usage_info.get("total_tokens", 0),
                cost_usd=last_usage_info.get("cost_usd", 0.0),
            )

    except Exception as e:
        emit_telemetry(
            action="EXECUTION_ERROR",
            category="SYSTEM",
            level="ERROR",
            thread_id=thread_id,
            session_id=existing_session_id,
            event_id=message_id,
            message=f"Execution error: {str(e)}",
        )


def resolve_thread_identifier(channel_id: str, actual_thread_ts: str | None, user_id: str = "", is_bot: bool = False, raw_ts: str = "") -> str:
    """
    Computes a strictly uniform thread identifier across all participants, messages, and events:
    1. DMs: 'dm_{user_id}' for users, or the active user thread for bot replies in this DM channel.
    2. In-thread conversation: actual_thread_ts (all thread messages and bot replies share the exact same thread ts).
    3. Main Channel conversation: 'channel_{channel_id}' (every member's message, every bot reply, and every system event share the exact same thread_ts).
    """
    is_dm = str(channel_id).startswith("D")
    if is_dm:
        if not is_bot and user_id and user_id != "unknown-user" and not user_id.startswith("B"):
            return f"dm_{user_id}"
        # For bot events or unauthenticated lookups in DMs, find the existing thread_ts for this DM channel
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thread_ts FROM conversation_messages WHERE channel_id = %s ORDER BY id DESC LIMIT 1;",
                    (channel_id,)
                )
                row = cur.fetchone()
                if row:
                    val = row.get("thread_ts") if isinstance(row, dict) else row[0]
                    if val:
                        return str(val)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return f"dm_{user_id}" if (user_id and user_id != "unknown-user") else f"dm_{channel_id}"
    elif actual_thread_ts:
        return str(actual_thread_ts)
    elif channel_id:
        return f"channel_{channel_id}"
    return raw_ts or ""


@slack_router.post("/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks = None):
    # 1. Read raw body and verify Slack signature
    body_bytes = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    if not verify_slack_signature(body_bytes, headers):
        emit_telemetry(
            action="SIGNATURE_INVALID",
            category="SECURITY",
            level="ERROR",
            message="Slack request failed signature validation (unauthorized request).",
        )
        return JSONResponse(status_code=401, content={"error": "invalid_signature"})

    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    # URL Verification Challenge for Slack Setup
    if body.get("type") == "url_verification":
        return JSONResponse(content={"challenge": body.get("challenge")})

    event = body.get("event", {})
    auths = body.get("authorizations", [])
    auth_team_id = (
        auths[0].get("team_id")
        if (auths and isinstance(auths, list) and len(auths) > 0 and isinstance(auths[0], dict))
        else None
    )
    auth_bot_user_id = (
        auths[0].get("user_id")
        if (auths and isinstance(auths, list) and len(auths) > 0 and isinstance(auths[0], dict))
        else None
    )

    incoming_team_id = (
        body.get("team_id")
        or (event.get("team") if isinstance(event.get("team"), str) else (event.get("team") or {}).get("id"))
        or auth_team_id
        or TENANT_ID
    )
    event_type = event.get("type")
    user_id = event.get("user", "unknown-user")
    channel_id = event.get("channel", "")
    actual_thread_ts = event.get("thread_ts")
    message_ts = event.get("ts")
    event_id = event.get("client_msg_id") or message_ts or str(uuid.uuid4())
    message_id = event_id

    subtype = event.get("subtype")
    inner_msg = event.get("message", {})
    is_bot = bool(
        event.get("bot_id")
        or subtype == "bot_message"
        or inner_msg.get("bot_id")
        or (user_id and user_id.startswith("B"))
    )
    is_dm = str(channel_id).startswith("D")

    # Ensure completely uniform thread identifier across all participants, messages, and events
    thread_to_pass = resolve_thread_identifier(
        channel_id=channel_id,
        actual_thread_ts=actual_thread_ts,
        user_id=user_id,
        is_bot=is_bot,
        raw_ts=message_ts or "",
    )

    logger.info(
        f"[DEBUG_SLACK_EVENT] Received event: event_id='{event_id}', type='{event_type}', "
        f"subtype='{subtype}', user='{user_id}', channel='{channel_id}', is_bot={is_bot}"
    )

    # Auto-register channel into database when bot or user joins
    if event_type == "member_joined_channel":
        join_channel = event.get("channel")
        if join_channel:
            try:
                from app.services.channel_secrets_service import resolve_slack_channel_name
                resolved = resolve_slack_channel_name(join_channel, token=get_slack_bot_token(incoming_team_id), team_id=incoming_team_id)
                logger.info(f"[SLACK_DISCOVERY] Auto-registered channel '{join_channel}' ({resolved}) on member_joined_channel.")
            except Exception as ex:
                logger.debug(f"Failed to auto-register channel on member_joined_channel: {ex}")
        return JSONResponse(content={"status": "channel_registered"})

    # 1. Ignore non-message notification subtypes (e.g. message_changed when card updates, message_deleted, etc.)
    IGNORED_SUBTYPES = {
        "message_changed",
        "message_deleted",
        "channel_join",
        "channel_leave",
        "pinned_item",
        "unpinned_item",
        "channel_topic",
        "channel_purpose",
        "channel_name",
    }
    if subtype in IGNORED_SUBTYPES:
        logger.info(
            f"[DEBUG_SLACK_EVENT] Subtype '{subtype}' MATCHED IGNORED_SUBTYPES for event_id='{event_id}'. Discarded."
        )
        emit_telemetry(
            action="SUBTYPE_IGNORED",
            category="FILTER",
            level="INFO",
            thread_id=thread_to_pass,
            event_id=event_id,
            user_id=user_id,
            message=f"Slack event notification subtype '{subtype}' ignored. Message discarded.",
        )
        return JSONResponse(content={"status": f"ignored_subtype_{subtype}"})

    # 2. Filter out bot loops (Requirement #5)
    if is_bot:
        logger.info(
            f"[DEBUG_SLACK_EVENT] Bot event detected for event_id='{event_id}' (bot_id='{event.get('bot_id')}'). Discarded."
        )
        emit_telemetry(
            action="BOT_IGNORED",
            category="FILTER",
            level="INFO",
            thread_id=thread_to_pass,
            event_id=event_id,
            user_id=user_id,
            message=f"Bot activity detected (bot_id={event.get('bot_id', 'unknown')}). Message discarded and content redacted.",
        )
        return JSONResponse(content={"status": "ignored_bot_event"})

    if event_type in ["app_mention", "message"]:
        text = (event.get("text") or "").strip()
        raw_files = event.get("files", [])

        # Atomic deduplication check
        is_new = record_processed_activity(incoming_team_id, message_id)
        if not is_new:
            emit_telemetry(
                action="DUPLICATE_IGNORED",
                category="DEDUPLICATION",
                level="WARNING",
                thread_id=thread_to_pass,
                event_id=message_id,
                user_id=user_id,
                message=f"Duplicate event rejected (event_id={message_id}).",
            )
            return JSONResponse(content={"status": "duplicate_ignored"})

        # Ignore empty messages without files
        if not text and not raw_files:
            return JSONResponse(content={"status": "ignored_empty_message"})

        reply_in_thread = bool(actual_thread_ts) and not is_dm

        user_profile_event = event.get("user_profile", {})
        if user_profile_event and isinstance(user_profile_event, dict):
            r_name = user_profile_event.get("real_name") or user_profile_event.get("display_name") or user_profile_event.get("name")
            if r_name:
                _USER_PROFILES[user_id] = {
                    "name": r_name,
                    "display_name": user_profile_event.get("display_name") or r_name,
                    "real_name": r_name,
                    "first_name": user_profile_event.get("first_name") or r_name.split()[0],
                }

        token = get_slack_bot_token(incoming_team_id)
        profile = await get_slack_user_profile(user_id, token)
        user_display = profile.get("real_name") or profile.get("display_name") or user_id

        # Extract file attachments from event
        raw_files = event.get("files", [])
        files_data = []
        for f in raw_files:
            download_url = f.get("url_private_download") or f.get("url_private")
            if download_url:
                files_data.append({
                    "id": f.get("id"),
                    "name": f.get("name") or "file",
                    "filetype": (f.get("filetype") or "").lower(),
                    "mimetype": (f.get("mimetype") or "").lower(),
                    "url_private_download": download_url,
                    "size": f.get("size", 0),
                })

        # Resolve authoritative workspace and channel metadata
        from app.services.usage_service import normalize_workspace_info
        from app.services.channel_secrets_service import canonical_channel_id, resolve_slack_channel_name

        norm_wid, norm_wname = normalize_workspace_info(incoming_team_id, channel_id=channel_id)
        if norm_wid and norm_wid != "UNKNOWN":
            incoming_team_id = norm_wid
        workspace_name = norm_wname

        # Ensure token is refreshed with canonical workspace ID
        token = get_slack_bot_token(incoming_team_id) or token
        if auth_bot_user_id and token:
            _BOT_USER_IDS[token] = auth_bot_user_id

        channel_name = None
        if channel_id:
            try:
                channel_name = resolve_slack_channel_name(channel_id, token=token, team_id=incoming_team_id)
            except Exception:
                pass
            channel_id = canonical_channel_id(channel_id, workspace_id=incoming_team_id, workspace_name=workspace_name, channel_name=channel_name)

        # 0. Keys pasted as "KEY_NAME = value": save them, delete the message, and never store/log/send the value anywhere.
        from app.services import slack_key_capture as skc

        key_msg = skc.parse_key_message(text)
        if key_msg:
            from app.services.channel_secrets_service import get_folder_id_for_channel

            slack_channel_for_api = event.get("channel") or channel_id
            key_thread = actual_thread_ts if not is_dm else None
            key_folder = None if is_dm else get_folder_id_for_channel(channel_id)
            key_names = [n.upper() for n, _ in key_msg["entries"]]

            async def _process_key_message(parsed=key_msg):
                # Runs AFTER Slack got its 200 (Slack retries anything slower than ~3 seconds).
                try:
                    outcome = await skc.handle_key_message(
                        parsed=parsed, channel_id=channel_id, slack_channel_id=slack_channel_for_api,
                        message_ts=message_ts or "", thread_ts=key_thread, bot_token=token,
                        folder_id=key_folder, actor=user_display, team_id=incoming_team_id,
                    )
                except Exception as key_err:
                    logger.error(f"[SLACK_KEYS] Key message handling failed: {type(key_err).__name__}")
                    outcome = {"names": key_names, "deleted": False, "saved": 0}
                    await skc.post_reply(token, slack_channel_for_api, key_thread,
                                         "⚠️ Something went wrong, so the key wasn't saved. 🔒 Please delete your message with the key now.")
                try:
                    save_conversation_message(
                        team_id=incoming_team_id, workspace_id=incoming_team_id, workspace_name=workspace_name,
                        channel_id=channel_id, thread_ts=thread_to_pass, user_id=user_id, user_name=user_display, role="user",
                        content=f"[Key(s) {', '.join(outcome['names'])} sent by {user_display}; values hidden]",
                        message_ts=message_ts, input_tokens=0, output_tokens=0, total_tokens=0, cost_usd=0,
                    )
                    emit_telemetry(
                        action="KEY_MESSAGE_HANDLED", category="SECURITY", level="INFO", thread_id=thread_to_pass,
                        event_id=message_id, user_id=user_id, channel_id=channel_id, channel_name=channel_name,
                        message=f"Key message from {user_display}: {outcome['saved']} saved, message deleted={outcome['deleted']} (values never logged).",
                    )
                except Exception as note_err:
                    logger.warning(f"[SLACK_KEYS] Could not record the key note: {type(note_err).__name__}")

            if background_tasks is not None:
                background_tasks.add_task(_process_key_message)
            else:
                _KEY_TASKS.add(task := asyncio.create_task(_process_key_message()))
                task.add_done_callback(_KEY_TASKS.discard)
            return JSONResponse(content={"status": "key_capture_started"})

        # 1. Store EVERY incoming message from any team member in PostgreSQL memory
        file_summary = f"[Attached: {', '.join([f['name'] for f in files_data])}]" if files_data else ""
        store_text = f"{file_summary} {text}".strip() if file_summary else text
        if store_text:
            cleaned_store_text = re.sub(r"<@[A-Z0-9]+>", "", store_text).strip()
            msg_content = cleaned_store_text or store_text or "[User uploaded file]"
            user_in_tokens = max(15, len(msg_content) // 4)
            user_cost = round(user_in_tokens * 3.0 / 1_000_000.0, 6)
            save_conversation_message(
                team_id=incoming_team_id,
                workspace_id=incoming_team_id,
                workspace_name=workspace_name,
                channel_id=channel_id,
                thread_ts=thread_to_pass,
                user_id=user_id,
                user_name=user_display,
                role="user",
                content=msg_content,
                message_ts=message_ts,
                input_tokens=user_in_tokens,
                output_tokens=0,
                total_tokens=user_in_tokens,
                cost_usd=user_cost,
            )
            emit_telemetry(
                action="MESSAGE_PERSISTED",
                category="DATABASE",
                level="INFO",
                thread_id=thread_to_pass,
                event_id=message_id,
                user_id=user_id,
                channel_id=channel_id,
                channel_name=channel_name,
                message=f"Persisted message from {user_display} into conversation_messages (workspace={workspace_name}, channel={channel_id}, files={len(files_data)}).",
            )

        # 2. Side-note ignore prefix check (e.g. // or (note) or (human))
        if text.startswith("//") or text.startswith("/*") or text.lower().startswith("(note)") or text.lower().startswith("(human)"):
            emit_telemetry(
                action="HUMAN_NOTE_IGNORED",
                category="FILTER",
                level="INFO",
                thread_id=thread_to_pass,
                event_id=message_id,
                user_id=user_id,
                message="Human side-note prefix detected ('//' or '(note)'). Message stored in memory, bot stayed silent.",
            )
            return JSONResponse(content={"status": "ignored_human_note"})

        # 3. Check Human-to-Human Mentions vs Bot Mention
        db_bot_user_id = get_slack_workspace_bot_user_id(incoming_team_id)
        api_bot_user_id = await get_bot_user_id(token, incoming_team_id) if token else ""
        all_bot_ids = set(get_all_slack_bot_user_ids())
        
        bot_ids_to_check = {b for b in [auth_bot_user_id, db_bot_user_id, api_bot_user_id, *all_bot_ids] if b}

        user_mentions = re.findall(r"<@([A-Z0-9]+)>", text)
        has_bot_in_mentions = (
            (event_type == "app_mention")
            or (bool(bot_ids_to_check) and any(b in user_mentions for b in bot_ids_to_check))
            or (bool(bot_ids_to_check) and any(f"<@{b}>" in text for b in bot_ids_to_check))
        )

        # If colleagues are mentioned, but neither bot_user_id nor auth_bot_user_id is mentioned -> stay silent
        if user_mentions and bot_ids_to_check and not has_bot_in_mentions:
            emit_telemetry(
                action="HUMAN_CONVERSATION_IGNORED",
                category="FILTER",
                level="INFO",
                thread_id=thread_to_pass,
                event_id=message_id,
                user_id=user_id,
                message=f"Human-to-human conversation detected (tagged colleagues {user_mentions}). Message stored in memory, bot stayed silent.",
            )
            return JSONResponse(content={"status": "ignored_human_to_human_mention"})

        # 4. Require explicit @mention in channels (in DMs @mention is not needed)
        is_bot_mention = (event_type == "app_mention") or has_bot_in_mentions

        if not is_dm and not is_bot_mention:
            emit_telemetry(
                action="MESSAGE_SAVED_TO_MEMORY",
                category="SLACK",
                level="INFO",
                thread_id=thread_to_pass,
                event_id=message_id,
                user_id=user_id,
                message=f"Stored message from {user_display} in local memory. Bot stayed silent (not @mentioned).",
            )
            return JSONResponse(content={"status": "stored_in_memory_no_bot_mention"})

        emit_telemetry(
            action="EVENT_RECEIVED",
            category="SLACK",
            level="INFO",
            thread_id=thread_to_pass,
            event_id=message_id,
            user_id=user_id,
            channel_id=channel_id,
            channel_name=channel_name,
            message=f"Slack event received (type={event_type}, user={user_id}, channel={channel_id}, files={len(files_data)}) [Message content redacted]",
        )

        # Reply in thread ONLY if the user is already inside an existing thread
        reply_in_thread = bool(actual_thread_ts) and not is_dm

        # 5. Enqueue job into durable PostgreSQL job_queue and immediately ACK HTTP 200
        payload = {
            "raw_text": text,
            "message_id": message_id,
            "message_ts": message_ts,
            "reply_in_thread": reply_in_thread,
            "user_display": user_display,
            "files": files_data,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "workspace_id": incoming_team_id,
            "workspace_name": workspace_name,
        }

        job_id = enqueue_job(
            event_id=message_id,
            channel_id=channel_id,
            thread_ts=thread_to_pass,
            user_id=user_id,
            payload=payload,
            team_id=incoming_team_id,
        )

        logger.info(
            f"[DEBUG_SLACK_EVENT] Job #{job_id} ENQUEUED for event_id='{message_id}': "
            f"type='{event_type}', channel='{channel_id}', user='{user_id}', "
            f"text_len={len(text)}, text_preview={repr(text[:80])}"
        )

        emit_telemetry(
            action="JOB_ENQUEUED",
            category="QUEUE",
            level="INFO",
            thread_id=thread_to_pass,
            event_id=message_id,
            user_id=user_id,
            channel_id=channel_id,
            channel_name=channel_name,
            message=f"Slack event enqueued as Job #{job_id} for asynchronous worker processing. Immediate 200 OK returned.",
        )

        return JSONResponse(content={"status": "enqueued", "job_id": job_id, "event_id": message_id})

    return JSONResponse(content={"status": "ok"})


@slack_router.post("/interactive")
async def slack_interactive(request: Request):
    """
    Handles Slack Block Kit interactive actions (approve, reject, view diff modal).
    Enforces in-place state mutation for permanent audit records.
    """
    body_bytes = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    if not verify_slack_signature(body_bytes, headers):
        emit_telemetry(
            action="SIGNATURE_INVALID",
            category="SECURITY",
            level="ERROR",
            message="Slack interactive request failed signature validation.",
        )
        return JSONResponse(status_code=401, content={"error": "invalid_signature"})

    try:
        import urllib.parse
        parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8"))
        payload_raw = parsed.get("payload", [""])[0]
        if not payload_raw:
            return JSONResponse(status_code=400, content={"error": "missing_payload"})
        payload = json.loads(payload_raw)
    except Exception as parse_err:
        logger.error(f"Error parsing Slack interactive payload: {parse_err}")
        return JSONResponse(status_code=400, content={"error": "invalid_payload"})

    payload_type = payload.get("type")
    interactive_team_id = (payload.get("team") or {}).get("id") or payload.get("team_id") or ""
    token = get_slack_bot_token(interactive_team_id)

    if payload_type == "block_actions":
        actions = payload.get("actions", [])
        if not actions:
            return JSONResponse(content={"status": "no_action"})

        action = actions[0]
        action_id = action.get("action_id")
        approval_id = action.get("value")
        trigger_id = payload.get("trigger_id")
        user = payload.get("user", {})
        user_id = user.get("id", "")
        channel = payload.get("channel", {})
        channel_id = channel.get("id", "")
        message = payload.get("message", {})
        message_ts = message.get("ts", "")

        default_repo = get_secret("GITHUB_DEFAULT_REPO", "devaxcel/jts-powertool")

        # 1. View Full Diff Modal
        if action_id == "inspect_github_diff":
            pending = get_pending_approval(approval_id)
            if not pending:
                return JSONResponse(content={"status": "not_found"})
            tool_name = pending.get("tool_name", "")
            tool_args = pending.get("tool_arguments", {})
            if tool_name == "update_website" and isinstance(tool_args, dict) and tool_args.get("draft_id"):
                try:
                    from app.services.site_builder_service import update_diff_text
                    tool_args = {**tool_args, "content": update_diff_text(int(tool_args["draft_id"]))}
                except Exception as diff_err:
                    logger.warning(f"Could not build website diff for {approval_id}: {diff_err}")
            if trigger_id:
                modal_view = build_diff_modal_view(
                    approval_id=approval_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        "https://slack.com/api/views.open",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"trigger_id": trigger_id, "view": modal_view},
                    )
            return JSONResponse(content={"status": "modal_opened"})

        # 2. Approve & Apply Action
        elif action_id == "approve_github_action":
            claim_status, claimed_record = claim_approval_for_execution(approval_id, user_id)
            if claim_status == "not_found":
                return JSONResponse(content={"status": "not_found"})

            if claim_status == "expired":
                expired_blocks = build_expired_card_blocks(
                    approval_id=approval_id,
                    tool_name=claimed_record.get("tool_name", "") if claimed_record else "",
                    tool_args=claimed_record.get("tool_arguments", {}) if claimed_record else {},
                    default_repo=default_repo,
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        "https://slack.com/api/chat.update",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "channel": channel_id,
                            "ts": message_ts,
                            "text": "🛡️ GitHub Write Action Expired",
                            "blocks": expired_blocks,
                        },
                    )
                return JSONResponse(content={"status": "expired"})

            if claim_status != "claimed":
                # Already applying, already applied, already rejected, etc. (idempotent double-click guard)
                return JSONResponse(content={"status": "already_processed"})

            # At this point, this worker/request has EXCLUSIVELY and ATOMICALLY claimed execution!
            # Execute the EXACT payload approved and stored in PostgreSQL:
            exec_tool_name = (claimed_record or {}).get("tool_name", "")
            exec_tool_args = (claimed_record or {}).get("tool_arguments", {})
            if isinstance(exec_tool_args, str):
                try:
                    exec_tool_args = json.loads(exec_tool_args)
                except Exception:
                    pass

            if isinstance(exec_tool_args, dict) and exec_tool_name in ["create_or_update_file", "push_files"]:
                if not exec_tool_args.get("branch") or not str(exec_tool_args.get("branch")).strip():
                    exec_tool_args["branch"] = "main"

            # In-place immediate visual feedback: lock out buttons
            loading_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*GitHub Write Permission - Applying...*\n*Approved by:* <@{user_id}>\n*Executing changes on GitHub via MCP...*",
                    },
                }
            ]
            async with httpx.AsyncClient(timeout=45.0) as client:
                await client.post(
                    "https://slack.com/api/chat.update",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "ts": message_ts,
                        "text": "GitHub Write Action in progress...",
                        "blocks": loading_blocks,
                    },
                )

                # Execute tool via GitHub MCP using the verified stored payload, with the client's own GitHub access
                try:
                    exec_channel = (claimed_record or {}).get("channel_id") or channel_id or ""
                    if exec_tool_name == "publish_website":
                        from app.services.site_builder_service import execute_publish_approval
                        output, is_error = await execute_publish_approval(exec_tool_args, exec_channel)
                    elif exec_tool_name == "update_website":
                        from app.services.site_builder_service import execute_update_approval
                        output, is_error = await execute_update_approval(exec_tool_args, exec_channel, approved_by=user_id)
                    elif str(exec_tool_name or "").startswith("jira_"):
                        from app.services.jira_service import execute_write
                        output, is_error = await execute_write(exec_channel, exec_tool_name, exec_tool_args)
                    else:
                        from app.services.github_app_service import github_client_for_channel
                        mcp_client = await github_client_for_channel(exec_channel)
                        output = await mcp_client.execute_tool(exec_tool_name, exec_tool_args)
                        is_error = bool(output and (output.startswith("[GitHub MCP Error]:") or output.startswith("Error executing tool:")))
                except Exception as mcp_err:
                    def _unwrap_exc(err: Exception) -> str:
                        if hasattr(err, "exceptions") and err.exceptions:
                            return "; ".join(_unwrap_exc(e) for e in err.exceptions)
                        return str(err)
                    output = f"Error executing tool: {_unwrap_exc(mcp_err)}"
                    is_error = True

                # Atomic finalize: applying -> applied (or failed)
                finalize_approval_execution(
                    approval_id=approval_id,
                    success=not is_error,
                    execution_result=output,
                )

                # Mutate card into permanent approved state
                final_blocks = build_approved_card_blocks(
                    approval_id=approval_id,
                    tool_name=exec_tool_name,
                    tool_args=exec_tool_args,
                    approved_by=user_id,
                    execution_result=output,
                    default_repo=default_repo,
                )
                await client.post(
                    "https://slack.com/api/chat.update",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "ts": message_ts,
                        "text": "GitHub Write Action Approved & Applied",
                        "blocks": final_blocks,
                    },
                )

                # Post confirmation in thread
                thread_ts = (claimed_record or {}).get("thread_ts")
                target_thread = (
                    thread_ts
                    if (thread_ts and not thread_ts.startswith("channel_") and not thread_ts.startswith("dm_"))
                    else None
                )
                if exec_tool_name == "update_website":
                    confirm_text = (
                        f":globe_with_meridians: <@{user_id}> approved the website change. {output}"
                        if not is_error
                        else f"<@{user_id}> approved the website change, but it failed: {output.replace('[Update Error]: ', '')} Nothing was changed."
                    )
                elif exec_tool_name == "publish_website":
                    confirm_text = (
                        f":globe_with_meridians: <@{user_id}> approved publishing. {output}"
                        if not is_error
                        else f"<@{user_id}> approved publishing, but it failed: {output.replace('[Publish Error]: ', '')} Nothing was published."
                    )
                elif str(exec_tool_name or "").startswith("jira_"):
                    confirm_text = (
                        f":white_check_mark: <@{user_id}> approved it. {output[:1500]}"
                        if not is_error
                        else f"<@{user_id}> approved it, but Jira returned an error: {output.replace('[Jira Error]: ', '')[:1500]} Nothing was changed."
                    )
                elif not is_error:
                    confirm_text = f"<@{user_id}> approved `{exec_tool_name}`. Changes have been committed to GitHub successfully!\n\n> *Next Step:* If your task has remaining steps (e.g. creating files or code structure), reply to continue with the next step."
                else:
                    confirm_text = f"<@{user_id}> approved `{exec_tool_name}`, but execution on GitHub failed: `{output}`"

                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "thread_ts": target_thread,
                        "text": confirm_text,
                    },
                )

                emit_telemetry(
                    action="APPROVAL_GRANTED",
                    category="TOOL",
                    level="INFO",
                    thread_id=thread_ts,
                    user_id=user_id,
                    message=f"User <@{user_id}> approved write action '{exec_tool_name}' ({approval_id}). Executed successfully.",
                    extra={"approval_id": approval_id, "tool_name": exec_tool_name},
                )

            return JSONResponse(content={"status": "approved"})

        # 3. Reject Action
        elif action_id == "reject_github_action":
            reject_status, rejected_record = reject_approval(approval_id, user_id)
            if reject_status == "not_found":
                return JSONResponse(content={"status": "not_found"})

            if reject_status == "expired":
                expired_blocks = build_expired_card_blocks(
                    approval_id=approval_id,
                    tool_name=rejected_record.get("tool_name", "") if rejected_record else "",
                    tool_args=rejected_record.get("tool_arguments", {}) if rejected_record else {},
                    default_repo=default_repo,
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        "https://slack.com/api/chat.update",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "channel": channel_id,
                            "ts": message_ts,
                            "text": "GitHub Write Action Expired",
                            "blocks": expired_blocks,
                        },
                    )
                return JSONResponse(content={"status": "expired"})

            if reject_status != "rejected":
                return JSONResponse(content={"status": "already_processed"})

            rejected_tool_name = (rejected_record or {}).get("tool_name", "")
            rejected_tool_args = (rejected_record or {}).get("tool_arguments", {})
            if isinstance(rejected_tool_args, str):
                try:
                    rejected_tool_args = json.loads(rejected_tool_args)
                except Exception:
                    pass

            rejected_blocks = build_rejected_card_blocks(
                approval_id=approval_id,
                tool_name=rejected_tool_name,
                tool_args=rejected_tool_args,
                rejected_by=user_id,
                default_repo=default_repo,
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "https://slack.com/api/chat.update",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "ts": message_ts,
                        "text": "GitHub Write Action Cancelled",
                        "blocks": rejected_blocks,
                    },
                )
                thread_ts = (rejected_record or {}).get("thread_ts")
                target_thread = (
                    thread_ts
                    if (thread_ts and not thread_ts.startswith("channel_") and not thread_ts.startswith("dm_"))
                    else None
                )
                await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "thread_ts": target_thread,
                        "text": f"<@{user_id}> rejected `{rejected_tool_name}`. No changes were made to GitHub.",
                    },
                )

                emit_telemetry(
                    action="APPROVAL_REJECTED",
                    category="TOOL",
                    level="WARN",
                    thread_id=thread_ts,
                    user_id=user_id,
                    message=f"User <@{user_id}> rejected write action '{rejected_tool_name}' ({approval_id}).",
                    extra={"approval_id": approval_id, "tool_name": rejected_tool_name},
                )

            return JSONResponse(content={"status": "rejected"})

    return JSONResponse(content={"status": "ok"})



