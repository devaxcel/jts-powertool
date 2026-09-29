import json
import logging
from datetime import datetime, date
from uuid import UUID
from typing import Optional, Dict, Any
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db.repositories import (
    get_all_approvals,
    get_pending_approval,
    claim_approval_for_execution,
    finalize_approval_execution,
    reject_approval,
    get_system_stats,
)
from app.tools.mcp_client import GitHubMCPClient
from app.tools.secrets_manager import get_secret
from app.slack_router import build_approved_card_blocks, build_rejected_card_blocks

logger = logging.getLogger(__name__)
api_approvals_router = APIRouter(prefix="/api", tags=["Approvals & System"])


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


def format_diff_preview(tool_name: str, tool_args: dict) -> str:
    """Generates unified diff preview for UI code viewer."""
    if tool_name == "create_or_update_file":
        path = tool_args.get("path", "file")
        content = tool_args.get("content", "")
        lines = content.splitlines()
        preview_lines = [f"--- a/{path} (new/updated file)", f"+++ b/{path}"]
        for l in lines:
            preview_lines.append(f"+{l}")
        return "\n".join(preview_lines)

    elif tool_name == "push_files":
        files = tool_args.get("files", [])
        all_diffs = []
        for f in files:
            p = f.get("path", "file")
            c = f.get("content", "")
            diff_lines = [f"--- a/{p}", f"+++ b/{p}"]
            for l in c.splitlines():
                diff_lines.append(f"+{l}")
            all_diffs.append("\n".join(diff_lines))
        return "\n\n".join(all_diffs)

    return json.dumps(tool_args, indent=2)


class ApprovalActionPayload(BaseModel):
    action: str  # "approve" or "reject"
    user_id: Optional[str] = "web-admin"


from fastapi import APIRouter, HTTPException, Request
from app.auth_router import get_user_context, get_folder_channel_ids


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


from app.services.usage_service import KNOWN_USERS
from app.slack_router import get_slack_user_profile


async def enrich_approval_user_names(approvals: list[dict]):
    token = get_secret("SLACK_BOT_TOKEN", "").strip()
    for a in approvals:
        # 1. Resolve requester user_name
        req = str(a.get("user_name") or a.get("user_id") or "").strip()
        clean_req = req.replace("<@", "").replace(">", "").strip()
        if clean_req in KNOWN_USERS:
            a["user_name"] = KNOWN_USERS[clean_req]
        elif clean_req.startswith("U") and len(clean_req) >= 8:
            prof = await get_slack_user_profile(clean_req, token) if token else {}
            resolved = prof.get("real_name") or prof.get("display_name") or prof.get("name")
            if resolved and resolved != clean_req and not resolved.startswith("U"):
                a["user_name"] = resolved
            elif clean_req in KNOWN_USERS:
                a["user_name"] = KNOWN_USERS[clean_req]
            else:
                a["user_name"] = "Admin User"
        elif clean_req in ("web-admin", "admin"):
            a["user_name"] = "Admin User"

        # 2. Resolve actor (approved_by / rejected_by)
        actor = str(a.get("approved_by_name") or a.get("approved_by") or a.get("user_name") or a.get("user_id") or "").strip()
        clean_actor = actor.replace("<@", "").replace(">", "").strip()
        if clean_actor in KNOWN_USERS:
            a["approved_by_name"] = KNOWN_USERS[clean_actor]
            a["approved_by"] = KNOWN_USERS[clean_actor]
        elif clean_actor.startswith("U") and len(clean_actor) >= 8:
            prof = await get_slack_user_profile(clean_actor, token) if token else {}
            resolved = prof.get("real_name") or prof.get("display_name") or prof.get("name")
            if resolved and resolved != clean_actor and not resolved.startswith("U"):
                a["approved_by_name"] = resolved
                a["approved_by"] = resolved
            elif clean_actor in KNOWN_USERS:
                a["approved_by_name"] = KNOWN_USERS[clean_actor]
                a["approved_by"] = KNOWN_USERS[clean_actor]
            else:
                a["approved_by_name"] = "Admin User"
                a["approved_by"] = "Admin User"
        elif clean_actor in ("web-admin", "admin"):
            a["approved_by_name"] = "Admin User"
            a["approved_by"] = "Admin User"


@api_approvals_router.get("/approvals")
async def list_approvals(request: Request, status: Optional[str] = None, limit: int = 50):
    """Retrieves a list of approvals, optionally filtered by status (pending, applied, rejected, expired)."""
    user_ctx = get_user_context(request)
    approvals = get_all_approvals(status=status, limit=limit)

    if user_ctx.get("role") in ("client_admin", "client_standard"):
        folder_id = user_ctx.get("client_folder_id")
        if not folder_id and user_ctx.get("username"):
            from app.auth_router import get_user_from_db
            db_u = get_user_from_db(user_ctx["username"])
            if db_u and db_u.get("client_folder_id"):
                folder_id = db_u["client_folder_id"]

        if not folder_id:
            folder_id = 2  # Fallback default client folder

        folder_channels = set(get_folder_channel_ids(folder_id))
        folder_channels_lower = {x.lower() for x in folder_channels}

        def matches_folder_approval(a: dict) -> bool:
            cid = str(a.get("channel_id") or "").strip()
            if not cid:
                payload = a.get("tool_arguments") or {}
                if isinstance(payload, dict):
                    cid = str(payload.get("channel_id") or "").strip()
            if not cid:
                # If approval object has no explicit channel_id, preserve it for the client folder
                return True

            raw_cid = cid.lower()
            clean_cid = raw_cid.lstrip("#@")
            if cid in folder_channels or raw_cid in folder_channels_lower or clean_cid in folder_channels_lower:
                return True
            
            for fc in folder_channels_lower:
                clean_fc = fc.lstrip("#@")
                if clean_fc and (clean_fc in raw_cid or clean_fc in clean_cid or clean_cid in clean_fc):
                    return True
            return False

        approvals = [a for a in approvals if matches_folder_approval(a)]

    await enrich_approval_user_names(approvals)

    for a in approvals:
        args = a.get("tool_arguments") or {}
        a["diff_preview"] = format_diff_preview(a.get("tool_name", ""), args)
    return JSONResponse(content={"approvals": serialize_data(approvals)})


@api_approvals_router.get("/approvals/{approval_id}")
async def get_approval_details(approval_id: str):
    """Retrieves details and diff preview for a specific approval."""
    record = get_pending_approval(approval_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' not found")

    await enrich_approval_user_names([record])

    args = record.get("tool_arguments") or {}
    record["diff_preview"] = format_diff_preview(record.get("tool_name", ""), args)
    return JSONResponse(content={"approval": serialize_data(record)})


@api_approvals_router.post("/approvals/{approval_id}/action")
async def handle_approval_action(approval_id: str, payload: ApprovalActionPayload, request: Request):
    """
    Executes an action (approve or reject) on an approval request from the Web UI.
    Synchronizes state in PostgreSQL and updates the interactive card in Slack.
    """
    action = payload.action.strip().lower()
    user_ctx = get_user_context(request)
    raw_user = payload.user_id or user_ctx.get("username") or user_ctx.get("display_name") or "Admin User"
    if raw_user in ("web-admin", "admin", "web-dashboard", "unknown"):
        user_display = "Admin User"
    else:
        user_display = raw_user

    if action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'")

    if action == "approve":
        claim_result, claimed_record = claim_approval_for_execution(approval_id, user_id=user_display)
        if claim_result != "claimed" or not claimed_record:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "status": claim_result,
                    "message": f"Approval request could not be claimed (Status: {claim_result})."
                }
            )

        exec_tool_name = claimed_record.get("tool_name")
        exec_tool_args = claimed_record.get("tool_arguments") or {}

        # Default branch to main if omitted
        if isinstance(exec_tool_args, dict) and exec_tool_name in ["create_or_update_file", "push_files"]:
            if not exec_tool_args.get("branch") or not str(exec_tool_args.get("branch")).strip():
                exec_tool_args["branch"] = "main"

        # Execute tool via GitHub MCP client
        mcp_client = GitHubMCPClient()
        try:
            output = await mcp_client.execute_tool(exec_tool_name, exec_tool_args)
            is_error = bool(output and (output.startswith("[GitHub MCP Error]:") or output.startswith("Error executing tool:")))
        except Exception as e:
            output = f"Error executing tool: {str(e)}"
            is_error = True

        finalize_approval_execution(approval_id=approval_id, success=not is_error, execution_result=output)

        # Synchronize Slack Card if channel and message_ts are present
        channel_id = claimed_record.get("channel_id")
        message_ts = claimed_record.get("message_ts")
        token = get_secret("SLACK_BOT_TOKEN", "").strip()
        default_repo = get_secret("GITHUB_DEFAULT_REPO", "AbdulAleemDev/jts-powertool")

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
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        "https://slack.com/api/chat.update",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "channel": channel_id,
                            "ts": message_ts,
                            "text": "GitHub Write Action Approved & Applied via Web UI",
                            "blocks": final_blocks,
                        }
                    )
                    thread_ts = claimed_record.get("thread_ts")
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
                            "text": f"`{exec_tool_name}` was approved via Web Dashboard by **{user_display}**. GitHub commit applied successfully!",
                        }
                    )
            except Exception as se:
                logger.warning(f"Failed to update Slack after web approval: {se}")

        return JSONResponse(content={
            "ok": not is_error,
            "status": "applied" if not is_error else "failed",
            "execution_result": output
        })

    elif action == "reject":
        reject_status, reject_record = reject_approval(approval_id, user_id=user_display)
        if reject_status != "rejected" or not reject_record:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "status": reject_status,
                    "message": f"Approval request could not be rejected (Status: {reject_status})."
                }
            )

        channel_id = reject_record.get("channel_id")
        message_ts = reject_record.get("message_ts")
        token = get_secret("SLACK_BOT_TOKEN", "").strip()
        default_repo = get_secret("GITHUB_DEFAULT_REPO", "AbdulAleemDev/jts-powertool")

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
                            "text": "❌ GitHub Write Action Rejected via Web UI",
                            "blocks": rejected_blocks,
                        }
                    )
            except Exception as se:
                logger.warning(f"Failed to update Slack after web rejection: {se}")

        return JSONResponse(content={
            "ok": True,
            "status": "rejected",
            "message": "Approval proposal was successfully rejected."
        })
