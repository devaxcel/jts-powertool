import difflib
import json
import logging
import time
from datetime import datetime, date
from uuid import UUID
from typing import Optional, Dict, Any, Tuple
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.repositories import (
    get_all_approvals,
    get_pending_approval,
    claim_approval_for_execution,
    finalize_approval_execution,
    reject_approval,
    expire_stale_approvals,
    get_system_stats,
)
from app.services.github_app_service import GitHubAppError, github_client_for_channel, resolve_github_token
from app.tools.secrets_manager import get_secret
from app.slack_router import build_approved_card_blocks, build_rejected_card_blocks
from app.auth_router import (
    get_user_context,
    get_folder_channel_ids,
    require_session,
    client_channel_scope,
    channel_in_scope,
)
from app.services.usage_service import KNOWN_USERS
from app.slack_router import get_slack_user_profile

logger = logging.getLogger(__name__)
api_approvals_router = APIRouter(prefix="/api", tags=["Approvals & System"])

DEFAULT_REPO_FALLBACK = "devaxcel/jts-powertool"
FILE_TOOLS = ("create_or_update_file", "push_files")
APPROVER_ROLES = ("jts_admin", "client_admin")
MAX_LIST_LIMIT = 500


def serialize_data(val: Any) -> Any:
    """Recursively serializes datetimes and UUIDs into JSON-friendly types."""
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, dict):
        return {k: serialize_data(v) for k, v in val.items()}
    if isinstance(val, list):
        return [serialize_data(i) for i in val]
    return val


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def _require_login(request: Request) -> dict:
    return require_session(request)


def _channel_scope(ctx: dict) -> Optional[set]:
    return client_channel_scope(ctx)


def _is_visible(record: dict, scope: Optional[set]) -> bool:
    if scope is None:
        return True
    args = record.get("tool_arguments") or {}
    # Exact matches only: an approval without a channel is never shown to a client.
    return channel_in_scope(
        scope,
        record.get("channel_id"),
        record.get("channel_name"),
        args.get("channel_id") if isinstance(args, dict) else None,
    )


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------

def _full_file_preview(path: str, content: str) -> str:
    lines = [f"--- a/{path} (new or replaced file)", f"+++ b/{path}"]
    lines.extend(f"+{l}" for l in (content or "").splitlines())
    return "\n".join(lines)


def format_diff_preview(tool_name: str, tool_args: dict) -> str:
    """Quick preview used in the list. For file tools the detail endpoint returns a real diff."""
    args = tool_args or {}
    if tool_name == "create_or_update_file":
        return _full_file_preview(args.get("path", "file"), args.get("content", ""))

    if tool_name == "push_files":
        return "\n\n".join(
            _full_file_preview(f.get("path", "file"), f.get("content", "")) for f in args.get("files", []) or []
        )

    if tool_name == "create_issue":
        parts = [f"Title: {args.get('title', '')}"]
        if args.get("labels"):
            parts.append(f"Labels: {', '.join(args['labels'])}")
        if args.get("assignees"):
            parts.append(f"Assignees: {', '.join(args['assignees'])}")
        parts.append("")
        parts.append(args.get("body") or "(no description)")
        return "\n".join(parts)

    if tool_name == "update_issue":
        parts = [f"Issue #{args.get('issue_number', '?')}"]
        for field, label in (("title", "New title"), ("state", "New state")):
            if args.get(field):
                parts.append(f"{label}: {args[field]}")
        if args.get("labels"):
            parts.append(f"Labels: {', '.join(args['labels'])}")
        if args.get("body"):
            parts.extend(["", "New description:", args["body"]])
        return "\n".join(parts)

    if tool_name == "add_issue_comment":
        return f"Comment on issue #{args.get('issue_number', '?')}:\n\n{args.get('body', '')}"

    if tool_name == "create_pull_request":
        return (
            f"Title: {args.get('title', '')}\n"
            f"Merge '{args.get('head', '?')}' into '{args.get('base', 'main')}'\n\n"
            f"{args.get('body') or '(no description)'}"
        )

    if tool_name == "create_branch":
        return f"Create branch '{args.get('branch', '?')}' from '{args.get('from_branch') or 'the default branch'}'"

    return json.dumps(args, indent=2)


_file_cache: Dict[str, Tuple[float, Optional[str]]] = {}
_FILE_CACHE_TTL = 60.0


async def _fetch_github_file(owner: str, repo: str, path: str, ref: str, token: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Returns (ok, content). ok=False means GitHub could not be reached or refused;
    (True, None) means the file does not exist yet on that branch.
    """
    cache_key = f"{owner}/{repo}@{ref}:{path}:{hash(token or 'jts')}"
    cached = _file_cache.get(cache_key)
    if cached and time.time() - cached[0] < _FILE_CACHE_TTL:
        return True, cached[1]

    token = (token or get_secret("GITHUB_PERSONAL_ACCESS_TOKEN", "") or "").strip()
    if not token:
        return False, None
    url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path)}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"ref": ref},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.raw+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if resp.status_code == 404:
            _file_cache[cache_key] = (time.time(), None)
            return True, None
        if resp.status_code != 200:
            logger.warning(f"GitHub file fetch for diff failed ({resp.status_code}) for {cache_key}")
            return False, None
        _file_cache[cache_key] = (time.time(), resp.text)
        return True, resp.text
    except Exception as e:
        logger.warning(f"GitHub file fetch for diff failed for {cache_key}: {e}")
        return False, None


def _repo_parts(args: dict) -> Tuple[str, str]:
    owner = str(args.get("owner") or "").strip()
    repo = str(args.get("repo") or "").strip()
    if "/" in repo:
        owner, repo = repo.split("/", 1)
    if not owner or not repo:
        default_owner, _, default_repo = get_secret("GITHUB_DEFAULT_REPO", DEFAULT_REPO_FALLBACK).partition("/")
        owner = owner or default_owner.strip()
        repo = repo or default_repo.strip()
    return owner, repo


async def _file_diff(owner: str, repo: str, branch: str, path: str, new_content: str, token: Optional[str] = None) -> Tuple[str, str]:
    """Returns (diff_text, kind) where kind is 'diff', 'new' or 'full' (compare unavailable)."""
    ok, current = await _fetch_github_file(owner, repo, path, branch, token)
    if not ok:
        return _full_file_preview(path, new_content), "full"
    if current is None:
        return _full_file_preview(path, new_content).replace("(new or replaced file)", "(new file)"), "new"
    if current == new_content:
        return f"--- a/{path}\n+++ b/{path}\n(no changes: the file already has this content)", "diff"
    diff = difflib.unified_diff(
        current.splitlines(),
        (new_content or "").splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
        n=3,
    )
    return "\n".join(diff), "diff"


async def build_real_diff(tool_name: str, tool_args: dict, token: Optional[str] = None) -> Tuple[str, str]:
    """
    Compares proposed file content against what is on GitHub now.
    Returns (preview, kind): kind is 'diff' (real comparison), 'full' (GitHub unreachable,
    whole file shown) or 'text' (not a file change).
    """
    args = tool_args or {}
    if tool_name not in FILE_TOOLS:
        return format_diff_preview(tool_name, args), "text"

    owner, repo = _repo_parts(args)
    branch = str(args.get("branch") or "main").strip() or "main"
    files = (
        [{"path": args.get("path", "file"), "content": args.get("content", "")}]
        if tool_name == "create_or_update_file"
        else (args.get("files") or [])
    )

    chunks, kinds = [], set()
    for f in files:
        text, kind = await _file_diff(owner, repo, branch, f.get("path", "file"), f.get("content", ""), token)
        chunks.append(text)
        kinds.add(kind)
    return "\n\n".join(chunks), ("full" if "full" in kinds else "diff")


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

async def _resolve_person(raw: str, token: str) -> str:
    clean = str(raw or "").replace("<@", "").replace(">", "").strip()
    if not clean:
        return ""
    if clean in ("web-admin", "admin", "web-dashboard"):
        return "Admin User"
    if clean in KNOWN_USERS:
        return KNOWN_USERS[clean]
    if clean.startswith("U") and len(clean) >= 8 and token:
        prof = await get_slack_user_profile(clean, token)
        resolved = prof.get("real_name") or prof.get("display_name") or prof.get("name")
        if resolved and resolved != clean:
            return resolved
    # Unknown: show what we have rather than a misleading generic name.
    return clean


async def enrich_approval_user_names(approvals: list[dict]):
    token = get_secret("SLACK_BOT_TOKEN", "").strip()
    cache: Dict[str, str] = {}

    async def resolve(raw: str) -> str:
        if raw not in cache:
            cache[raw] = await _resolve_person(raw, token)
        return cache[raw]

    for a in approvals:
        requester = a.get("user_name") or a.get("user_id") or ""
        a["user_name"] = await resolve(requester) or "Unknown"

        # Only report an approver once someone actually approved or rejected it.
        actor = a.get("approved_by_name") or a.get("approved_by") or ""
        if a.get("approved_by") and actor:
            name = await resolve(actor)
            a["approved_by_name"] = name
        else:
            a["approved_by_name"] = None


# ---------------------------------------------------------------------------
# Slack messages
# ---------------------------------------------------------------------------

def _describe_action(tool_name: str, args: dict) -> str:
    args = args or {}
    if tool_name == "create_or_update_file":
        return f"update `{args.get('path', 'a file')}`"
    if tool_name == "push_files":
        return f"push {len(args.get('files') or [])} file(s)"
    if tool_name == "create_issue":
        return f"create issue \"{args.get('title', '')}\""
    if tool_name == "update_issue":
        return f"update issue #{args.get('issue_number', '?')}"
    if tool_name == "add_issue_comment":
        return f"comment on issue #{args.get('issue_number', '?')}"
    if tool_name == "create_pull_request":
        return f"open pull request \"{args.get('title', '')}\""
    if tool_name == "create_branch":
        return f"create branch `{args.get('branch', '?')}`"
    return f"run `{tool_name}`"


def _slack_result_text(tool_name: str, args: dict, user_display: str, ok: bool) -> str:
    action = _describe_action(tool_name, args)
    if ok:
        return f":white_check_mark: *{user_display}* approved the request to {action} from the web dashboard. Done on GitHub."
    return (
        f":warning: *{user_display}* approved the request to {action} from the web dashboard, "
        f"but GitHub returned an error, so nothing was changed. Check the Approvals page for details."
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class ApprovalActionPayload(BaseModel):
    action: str  # "approve" or "reject"
    user_id: Optional[str] = None


@api_approvals_router.get("/stats")
async def get_dashboard_stats(request: Request):
    """Returns high-level statistics for the Next.js overview dashboard."""
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        folder_id = user_ctx.get("client_folder_id")
        folder_channels = get_folder_channel_ids(folder_id) if folder_id else []
        stats = get_system_stats(folder_channel_ids=folder_channels)
        stats["active_channels_count"] = len(folder_channels)
    else:
        stats = get_system_stats()
    return JSONResponse(content=stats)


@api_approvals_router.get("/approvals")
async def list_approvals(request: Request, status: Optional[str] = None, limit: int = 200):
    """Lists approvals the caller may see, optionally filtered by status (pending, applied, failed, rejected, expired)."""
    ctx = _require_login(request)
    expire_stale_approvals()

    limit = max(1, min(int(limit or 200), MAX_LIST_LIMIT))
    scope = _channel_scope(ctx)
    # Clients need a wider fetch because other clients' rows are filtered out afterwards.
    fetch_limit = limit if scope is None else MAX_LIST_LIMIT
    approvals = [a for a in get_all_approvals(status=status, limit=fetch_limit) if _is_visible(a, scope)][:limit]

    await enrich_approval_user_names(approvals)
    for a in approvals:
        a["diff_preview"] = format_diff_preview(a.get("tool_name", ""), a.get("tool_arguments") or {})
        a["diff_kind"] = "full" if a.get("tool_name") in FILE_TOOLS else "text"

    return JSONResponse(content={
        "approvals": serialize_data(approvals),
        "can_approve": ctx.get("role") in APPROVER_ROLES,
    })


@api_approvals_router.get("/approvals/{approval_id}")
async def get_approval_details(approval_id: str, request: Request):
    """Returns one approval with a real before/after diff for file changes."""
    ctx = _require_login(request)
    expire_stale_approvals()

    record = get_pending_approval(approval_id)
    if not record or not _is_visible(record, _channel_scope(ctx)):
        raise HTTPException(status_code=404, detail="This approval request was not found.")

    await enrich_approval_user_names([record])
    gh_ctx = await resolve_github_token(record.get("channel_id") or "")
    preview, kind = await build_real_diff(record.get("tool_name", ""), record.get("tool_arguments") or {}, gh_ctx.get("token"))
    record["diff_preview"] = preview
    record["diff_kind"] = kind
    return JSONResponse(content={"approval": serialize_data(record)})


@api_approvals_router.post("/approvals/{approval_id}/action")
async def handle_approval_action(approval_id: str, payload: ApprovalActionPayload, request: Request):
    """
    Approves (and runs) or rejects an approval request from the web dashboard,
    then updates the matching Slack card.
    """
    ctx = _require_login(request)
    if ctx.get("role") not in APPROVER_ROLES:
        raise HTTPException(status_code=403, detail="Only admins can approve or reject changes.")

    action = payload.action.strip().lower()
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")

    existing = get_pending_approval(approval_id)
    if not existing or not _is_visible(existing, _channel_scope(ctx)):
        raise HTTPException(status_code=404, detail="This approval request was not found.")

    # Record the signed-in user, not whatever the browser sent.
    raw_user = ctx.get("username") or "Admin User"
    user_display = "Admin User" if raw_user in ("web-admin", "admin", "web-dashboard", "unknown") else raw_user

    token = get_secret("SLACK_BOT_TOKEN", "").strip()
    default_repo = get_secret("GITHUB_DEFAULT_REPO", DEFAULT_REPO_FALLBACK)

    if action == "approve":
        # Resolve the client's GitHub access BEFORE claiming, so a missing connection doesn't burn the approval.
        try:
            mcp_client = await github_client_for_channel(existing.get("channel_id") or "")
        except GitHubAppError as e:
            return JSONResponse(status_code=409, content={"ok": False, "status": "github_not_connected", "message": str(e)})

        claim_result, claimed_record = claim_approval_for_execution(approval_id, user_id=user_display)
        if claim_result != "claimed" or not claimed_record:
            messages = {
                "expired": "This request expired (requests are valid for 24 hours). Ask in Slack again if the change is still needed.",
                "applied": "This request was already approved.",
                "applying": "Someone else is approving this request right now.",
                "rejected": "This request was already rejected.",
                "failed": "This request was already approved but failed on GitHub.",
            }
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "status": claim_result,
                    "message": messages.get(claim_result, f"This request can no longer be approved ({claim_result})."),
                },
            )

        exec_tool_name = claimed_record.get("tool_name")
        exec_tool_args = claimed_record.get("tool_arguments") or {}

        if isinstance(exec_tool_args, dict) and exec_tool_name in FILE_TOOLS:
            if not str(exec_tool_args.get("branch") or "").strip():
                exec_tool_args["branch"] = "main"

        try:
            output = await mcp_client.execute_tool(exec_tool_name, exec_tool_args)
            is_error = bool(output and (output.startswith("[GitHub MCP Error]:") or output.startswith("Error executing tool:")))
        except Exception as e:
            output = f"Error executing tool: {str(e)}"
            is_error = True

        finalize_approval_execution(approval_id=approval_id, success=not is_error, execution_result=output)

        channel_id = claimed_record.get("channel_id")
        message_ts = claimed_record.get("message_ts")
        if token and channel_id and message_ts:
            try:
                final_blocks = build_approved_card_blocks(
                    approval_id=approval_id,
                    tool_name=exec_tool_name,
                    tool_args=exec_tool_args,
                    approved_by=f"{user_display} (via Web UI)",
                    execution_result=output,
                    default_repo=default_repo,
                )
                thread_ts = claimed_record.get("thread_ts")
                target_thread = (
                    thread_ts
                    if (thread_ts and not thread_ts.startswith("channel_") and not thread_ts.startswith("dm_"))
                    else None
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        "https://slack.com/api/chat.update",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "channel": channel_id,
                            "ts": message_ts,
                            "text": "GitHub change approved" if not is_error else "GitHub change approved but failed",
                            "blocks": final_blocks,
                        },
                    )
                    await client.post(
                        "https://slack.com/api/chat.postMessage",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "channel": channel_id,
                            "thread_ts": target_thread,
                            "text": _slack_result_text(exec_tool_name, exec_tool_args, user_display, not is_error),
                        },
                    )
            except Exception as se:
                logger.warning(f"Failed to update Slack after web approval: {se}")

        return JSONResponse(content={
            "ok": not is_error,
            "status": "applied" if not is_error else "failed",
            "execution_result": output,
            "message": "Approved and applied on GitHub." if not is_error else "Approved, but GitHub returned an error. Nothing was changed.",
        })

    reject_status, reject_record = reject_approval(approval_id, user_id=user_display)
    if reject_status != "rejected" or not reject_record:
        messages = {
            "expired": "This request already expired.",
            "applied": "This request was already approved, so it can't be rejected.",
            "applying": "This request is being applied right now.",
            "failed": "This request was already approved (it failed on GitHub).",
        }
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "status": reject_status,
                "message": messages.get(reject_status, f"This request can no longer be rejected ({reject_status})."),
            },
        )

    channel_id = reject_record.get("channel_id")
    message_ts = reject_record.get("message_ts")
    if token and channel_id and message_ts:
        try:
            rejected_blocks = build_rejected_card_blocks(
                approval_id=approval_id,
                tool_name=reject_record.get("tool_name", ""),
                tool_args=reject_record.get("tool_arguments") or {},
                rejected_by=f"{user_display} (via Web UI)",
                default_repo=default_repo,
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "https://slack.com/api/chat.update",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "ts": message_ts,
                        "text": "GitHub change rejected",
                        "blocks": rejected_blocks,
                    },
                )
        except Exception as se:
            logger.warning(f"Failed to update Slack after web rejection: {se}")

    return JSONResponse(content={
        "ok": True,
        "status": "rejected",
        "message": "Rejected. Nothing was changed on GitHub.",
    })
