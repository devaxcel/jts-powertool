import html
import json
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth_router import require_session
from app.services import github_app_service as gh
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

# Public: /api/github/connect, /api/github/callback, /api/github/webhook (each verified by state or signature)
github_public_router = APIRouter(prefix="/api/github", tags=["GitHub connection"])
# Signed-in dashboard users
github_folder_router = APIRouter(prefix="/api/channels/folders", tags=["GitHub connection"])


def _page(title: str, message: str, ok: bool) -> HTMLResponse:
    color = "#059669" if ok else "#e11d48"
    icon = "✓" if ok else "!"
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · JTS PowerTool</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f5f7fa;color:#1f2937;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;max-width:440px;width:100%;padding:32px;text-align:center;
box-shadow:0 10px 30px rgba(0,0,0,.06)}}
.icon{{width:48px;height:48px;border-radius:50%;margin:0 auto 16px;display:flex;align-items:center;justify-content:center;
font-size:24px;font-weight:700;color:#fff;background:{color}}}
h1{{font-size:20px;margin:0 0 8px}} p{{font-size:14px;line-height:1.5;color:#4b5563;margin:0}}
</style></head><body><div class="card"><div class="icon">{icon}</div><h1>{html.escape(title)}</h1>
<p>{html.escape(message)}</p></div></body></html>"""
    return HTMLResponse(content=body, status_code=200 if ok else 400)


async def _post_to_slack(channel_id: Optional[str], text: str) -> None:
    token = (get_secret("SLACK_BOT_TOKEN", "") or "").strip()
    if not token or not channel_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": channel_id, "text": text},
            )
    except Exception as e:
        logger.warning(f"[GITHUB_APP] Could not post connection message to Slack: {e}")


@github_public_router.get("/connect")
async def start_connect(state: str = ""):
    if not gh.is_configured():
        return _page("GitHub isn't set up yet", "Please ask your JTS administrator to finish the GitHub setup.", False)
    if not gh.peek_connect_link(state):
        return _page("This link has expired", "Connect links work once and for 15 minutes. Ask the bot in Slack for a new one.", False)
    return RedirectResponse(gh.install_url(state), status_code=302)


@github_public_router.get("/callback")
async def finish_connect(
    state: str = "", installation_id: Optional[str] = None, code: Optional[str] = None, setup_action: Optional[str] = None
):
    if setup_action == "request":
        return _page(
            "Waiting for an organization owner",
            "Your GitHub organization needs an owner to approve JTS PowerTool. Once they approve, ask the bot for a new connect link.",
            False,
        )
    try:
        info = await gh.complete_connection(state, installation_id, code)
    except gh.GitHubAppError as e:
        return _page("GitHub wasn't connected", str(e), False)
    except Exception as e:
        logger.error(f"[GITHUB_APP] Unexpected error completing connection: {e}", exc_info=True)
        return _page("GitHub wasn't connected", "Something went wrong on our side. Please try again in a minute.", False)

    who = f"<@{info['slack_user']}>" if info.get("slack_user") else "an admin"
    repos = info["repos"]
    repo_text = (
        f"{len(repos)} repositor{'y' if len(repos) == 1 else 'ies'}: " + ", ".join(f"`{r}`" for r in repos[:5])
        + (" …" if len(repos) > 5 else "")
        if repos else "no repositories selected yet"
    )
    await _post_to_slack(
        info.get("channel_id"),
        f":white_check_mark: GitHub connected by {who}: *{info['account_login']}* ({repo_text}). "
        "I'll use this account for GitHub work in this client's channels. Every change still needs approval.",
    )
    return _page(
        "GitHub connected",
        f"JTS PowerTool can now work with {info['account_login']} ({len(repos)} repositories). You can close this tab and go back to Slack.",
        True,
    )


@github_public_router.post("/webhook")
async def github_webhook(request: Request):
    body = await request.body()
    if not gh.verify_webhook_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    result = gh.handle_webhook(request.headers.get("X-GitHub-Event", ""), payload)
    return {"ok": True, "result": result}


# --------------------------------------------------------------------------- dashboard

def _authorize(request: Request, folder_id: int, manage: bool) -> dict:
    """JTS admin: any client. Client admin: own client (view + manage). Team member: own client, view only."""
    ctx = require_session(request)
    role = ctx.get("role")
    if role == "jts_admin":
        return ctx
    if role in ("client_admin", "client_standard") and ctx.get("client_folder_id") and int(ctx["client_folder_id"]) == int(folder_id):
        if manage and role != "client_admin":
            raise HTTPException(status_code=403, detail="Only your Client Admin can change the GitHub connection.")
        return ctx
    raise HTTPException(status_code=403, detail="You can only see your own organization's GitHub connection.")


class DefaultRepoPayload(BaseModel):
    full_name: Optional[str] = Field(None, max_length=255)


@github_folder_router.get("/{folder_id}/github")
async def github_status(folder_id: int, request: Request):
    _authorize(request, folder_id, manage=False)
    conn = gh.get_connection(folder_id)
    out = {"configured": gh.is_configured(), "connected": False, "connection": None, "repos": [], "error": None}
    if not conn:
        return JSONResponse(content=out)
    out["connected"] = conn.get("status") == "active"
    out["rules_enabled"] = conn.get("rules_enabled") is not False
    out["connection"] = {
        k: conn.get(k)
        for k in ("account_login", "account_type", "repository_selection", "repo_count", "default_repo",
                  "connected_by", "status", "created_at", "updated_at")
    }
    if out["connected"]:
        try:
            out["repos"] = await gh.list_installation_repos(conn["installation_id"])
        except gh.GitHubAppError as e:
            out["error"] = str(e)
    return JSONResponse(content=out)


@github_folder_router.post("/{folder_id}/github/connect-link")
async def github_connect_link(folder_id: int, request: Request):
    ctx = _authorize(request, folder_id, manage=True)
    try:
        url = gh.create_connect_link(folder_id, created_by=ctx.get("username"))
    except gh.GitHubAppError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"url": url, "expires_in_minutes": gh.CONNECT_LINK_TTL_SECONDS // 60}


@github_folder_router.put("/{folder_id}/github/default-repo")
async def github_default_repo(folder_id: int, payload: DefaultRepoPayload, request: Request):
    _authorize(request, folder_id, manage=True)
    conn = gh.get_connection(folder_id)
    if not conn or conn.get("status") != "active":
        raise HTTPException(status_code=400, detail="Connect GitHub first.")
    if payload.full_name:
        repos = {r["full_name"] for r in await gh.list_installation_repos(conn["installation_id"])}
        if payload.full_name not in repos:
            raise HTTPException(status_code=400, detail="That repository isn't part of this GitHub connection.")
    gh.set_default_repo(folder_id, payload.full_name or None)
    return {"ok": True, "default_repo": payload.full_name or None}


class RulesPayload(BaseModel):
    enabled: bool


@github_folder_router.put("/{folder_id}/github/rules")
async def github_rules(folder_id: int, payload: RulesPayload, request: Request):
    _authorize(request, folder_id, manage=True)
    conn = gh.get_connection(folder_id)
    if not conn or conn.get("status") != "active":
        raise HTTPException(status_code=400, detail="Connect GitHub first.")
    gh.set_rules_enabled(folder_id, payload.enabled)
    return {"ok": True, "rules_enabled": payload.enabled}


@github_folder_router.delete("/{folder_id}/github")
async def github_disconnect(folder_id: int, request: Request):
    _authorize(request, folder_id, manage=True)
    removed = gh.disconnect(folder_id)
    return {
        "ok": True,
        "removed": removed,
        "message": "GitHub disconnected. To fully remove access, also uninstall “JTS PowerTool” in your GitHub settings.",
    }
