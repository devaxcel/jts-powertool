import io
import logging
import os
import zipfile

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth_router import require_session
from app.services import wordpress_service as wp

logger = logging.getLogger(__name__)

# Signed-in dashboard users
wordpress_folder_router = APIRouter(prefix="/api/channels/folders", tags=["WordPress connection"])
wordpress_plugin_router = APIRouter(prefix="/api/wordpress", tags=["WordPress connection"])

PLUGIN_FILE = os.path.join(os.path.dirname(__file__), "assets", "jts-powertool-connector.php")


def _authorize(request: Request, folder_id: int, manage: bool) -> dict:
    """JTS admin: any client. Client admin: own client (view + manage). Team member: own client, view only."""
    ctx = require_session(request)
    role = ctx.get("role")
    if role == "jts_admin":
        return ctx
    if role in ("client_admin", "client_standard") and ctx.get("client_folder_id") and int(ctx["client_folder_id"]) == int(folder_id):
        if manage and role != "client_admin":
            raise HTTPException(status_code=403, detail="Only your Client Admin can change the WordPress connection.")
        return ctx
    raise HTTPException(status_code=403, detail="You can only see your own organization's WordPress connection.")


class ConnectPayload(BaseModel):
    site_url: str = Field(..., max_length=300)
    username: str = Field(..., max_length=150)
    app_password: str = Field(..., max_length=200)


@wordpress_folder_router.get("/{folder_id}/wordpress")
async def wordpress_status(folder_id: int, request: Request):
    _authorize(request, folder_id, manage=False)
    conn = wp.get_connection(folder_id)
    out = {"connected": False, "connection": None, "editors": {}, "connector": None, "error": None}
    if not conn:
        return JSONResponse(content=out)
    out["connected"] = conn.get("status") == "active"
    out["connection"] = {k: conn.get(k) for k in ("site_url", "site_name", "wp_user", "wp_user_name", "wp_roles", "status", "connected_by",
                                                   "created_at", "updated_at", "editors_checked_at")}
    out["editors"] = conn.get("editors") or {}
    if out["connected"]:
        try:
            out["connector"] = await wp.connector_info(conn)
        except Exception as e:
            out["error"] = "Couldn't reach the site just now."
            logger.debug(f"[WORDPRESS] Status check failed: {type(e).__name__}")
    return JSONResponse(content=out)


@wordpress_folder_router.post("/{folder_id}/wordpress")
async def wordpress_connect(folder_id: int, payload: ConnectPayload, request: Request):
    ctx = _authorize(request, folder_id, manage=True)
    try:
        info = await wp.connect_site(folder_id, payload.site_url, payload.username, payload.app_password, by=ctx.get("username") or "admin")
    except wp.WordPressError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=400, detail="The site didn't answer. Check the address and try again.")
    except Exception as e:
        logger.error(f"[WORDPRESS] Connecting client {folder_id} failed: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Something went wrong on our side. Please try again in a minute.")
    return {"ok": True, "message": f"Connected to {info['site_name']}.", **info}


@wordpress_folder_router.post("/{folder_id}/wordpress/check")
async def wordpress_check(folder_id: int, request: Request):
    _authorize(request, folder_id, manage=True)
    try:
        res = await wp.refresh_status(folder_id)
    except wp.WordPressError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=400, detail="The site didn't answer. Try again in a minute.")
    return {"ok": True, **res}


@wordpress_folder_router.delete("/{folder_id}/wordpress")
async def wordpress_disconnect(folder_id: int, request: Request):
    _authorize(request, folder_id, manage=True)
    removed = wp.disconnect(folder_id)
    return {"ok": True, "message": "WordPress was disconnected. Remember to revoke the application password in WordPress too." if removed else "WordPress wasn't connected."}


@wordpress_plugin_router.get("/connector.zip")
async def download_connector(request: Request):
    """The small plugin that lets the assistant change text inside Elementor pages."""
    ctx = require_session(request)
    if ctx.get("role") not in ("jts_admin", "client_admin"):
        raise HTTPException(status_code=403, detail="Only admins can download the plugin.")
    try:
        with open(PLUGIN_FILE, "rb") as f:
            code = f.read()
    except OSError:
        raise HTTPException(status_code=404, detail="The plugin file isn't on the server.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("jts-powertool-connector/jts-powertool-connector.php", code)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="jts-powertool-connector.zip"'})
