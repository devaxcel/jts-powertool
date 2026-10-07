import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth_router import require_session
from app.github_router import _page, _post_to_slack
from app.services import jira_service as jira

logger = logging.getLogger(__name__)

# Public: /api/jira/connect and /api/jira/callback (protected by the single-use state link)
jira_public_router = APIRouter(prefix="/api/jira", tags=["Jira connection"])
# Signed-in dashboard users
jira_folder_router = APIRouter(prefix="/api/channels/folders", tags=["Jira connection"])


@jira_public_router.get("/connect")
async def start_connect(state: str = ""):
    if not jira.is_configured():
        return _page("Jira isn't set up yet", "Please ask your JTS administrator to finish the Jira setup.", False)
    if not jira.peek_connect_link(state):
        return _page("This link has expired", "Connect links work once and for 15 minutes. Ask the bot in Slack for a new one.", False)
    return RedirectResponse(jira.authorize_url(state), status_code=302)


@jira_public_router.get("/callback")
async def finish_connect(state: str = "", code: Optional[str] = None, error: Optional[str] = None):
    if error:
        return _page("Jira wasn't connected", "Access wasn't granted in Atlassian. Ask the bot for a new link and click Accept.", False)
    try:
        info = await jira.complete_connection(state, code)
    except jira.JiraError as e:
        return _page("Jira wasn't connected", str(e), False)
    except Exception as e:
        logger.error(f"[JIRA] Unexpected error completing connection: {e}", exc_info=True)
        return _page("Jira wasn't connected", "Something went wrong on our side. Please try again in a minute.", False)

    who = f"<@{info['slack_user']}>" if info.get("slack_user") else "an admin"
    extra = " (the first of several sites you offered; reconnect to pick another)" if info.get("site_count", 1) > 1 else ""
    await _post_to_slack(
        info.get("channel_id"),
        f":white_check_mark: Jira connected by {who}: *{info.get('site_name') or info['site_url']}*{extra}. "
        "I can now search and read issues here. Every change (create, update, comment, move) still needs approval.",
    )
    return _page(
        "Jira connected",
        f"JTS PowerTool can now work with {info.get('site_name') or info['site_url']}. You can close this tab and go back to Slack.",
        True,
    )


# --------------------------------------------------------------------------- dashboard

def _authorize(request: Request, folder_id: int, manage: bool) -> dict:
    """JTS admin: any client. Client admin: own client (view + manage). Team member: own client, view only."""
    ctx = require_session(request)
    role = ctx.get("role")
    if role == "jts_admin":
        return ctx
    if role in ("client_admin", "client_standard") and ctx.get("client_folder_id") and int(ctx["client_folder_id"]) == int(folder_id):
        if manage and role != "client_admin":
            raise HTTPException(status_code=403, detail="Only your Client Admin can change the Jira connection.")
        return ctx
    raise HTTPException(status_code=403, detail="You can only see your own organization's Jira connection.")


class DefaultProjectPayload(BaseModel):
    key: Optional[str] = Field(None, max_length=40)


@jira_folder_router.get("/{folder_id}/jira")
async def jira_status(folder_id: int, request: Request):
    _authorize(request, folder_id, manage=False)
    conn = jira.get_connection(folder_id)
    out = {"configured": jira.is_configured(), "connected": False, "connection": None, "projects": [], "error": None}
    if not conn:
        return JSONResponse(content=out)
    out["connected"] = conn.get("status") == "active"
    out["auto_rules"] = jira.auto_rules_enabled(conn)
    out["connection"] = {
        k: conn.get(k) for k in ("site_url", "site_name", "default_project", "connected_by", "status", "created_at", "updated_at")
    }
    if out["connected"]:
        try:
            out["projects"] = await jira.list_projects(folder_id)
        except jira.JiraError as e:
            out["error"] = str(e)
    return JSONResponse(content=out)


@jira_folder_router.post("/{folder_id}/jira/connect-link")
async def jira_connect_link(folder_id: int, request: Request):
    ctx = _authorize(request, folder_id, manage=True)
    try:
        url = jira.create_connect_link(folder_id, created_by=ctx.get("username"))
    except jira.JiraError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"url": url, "expires_in_minutes": jira.CONNECT_LINK_TTL_SECONDS // 60}


@jira_folder_router.put("/{folder_id}/jira/default-project")
async def jira_default_project(folder_id: int, payload: DefaultProjectPayload, request: Request):
    _authorize(request, folder_id, manage=True)
    conn = jira.get_connection(folder_id)
    if not conn or conn.get("status") != "active":
        raise HTTPException(status_code=400, detail="Connect Jira first.")
    key = (payload.key or "").strip().upper() or None
    if key:
        try:
            keys = {p["key"] for p in await jira.list_projects(folder_id)}
        except jira.JiraError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if key not in keys:
            raise HTTPException(status_code=400, detail="That project isn't part of this Jira connection.")
    jira.set_default_project(folder_id, key)
    return {"ok": True, "default_project": key}


class AutoRulesPayload(BaseModel):
    enabled: bool


@jira_folder_router.put("/{folder_id}/jira/auto-rules")
async def jira_auto_rules(folder_id: int, payload: AutoRulesPayload, request: Request):
    _authorize(request, folder_id, manage=True)
    conn = jira.get_connection(folder_id)
    if not conn or conn.get("status") != "active":
        raise HTTPException(status_code=400, detail="Connect Jira first.")
    jira.set_auto_rules(folder_id, payload.enabled)
    return {"ok": True, "auto_rules": payload.enabled}


@jira_folder_router.delete("/{folder_id}/jira")
async def jira_disconnect(folder_id: int, request: Request):
    _authorize(request, folder_id, manage=True)
    removed = jira.disconnect(folder_id)
    return {
        "ok": True,
        "removed": removed,
        "message": "Jira disconnected. To fully remove access, also remove “JTS PowerTool” under Connected apps in your Atlassian account.",
    }
