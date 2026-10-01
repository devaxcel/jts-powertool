from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.auth_router import require_session
from app.services import site_builder_service as sb

# Public, but every URL carries a signed, expiring token for exactly one draft.
site_preview_router = APIRouter(prefix="/api/site-preview", tags=["Website builder"])
# Signed-in dashboard users.
site_drafts_router = APIRouter(prefix="/api/site-drafts", tags=["Website builder"])

# The draft's HTML/JS is untrusted. "sandbox" without allow-same-origin gives it an opaque origin, so it can't read
# the dashboard's cookies/storage or call our API as the viewer, even though it's served from our domain.
_PREVIEW_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
}


@site_preview_router.get("/{token}/{path:path}")
async def preview(token: str, path: str = "index.html"):
    try:
        content, mime = sb.preview_file(token, path)
    except sb.SiteBuilderError as e:
        return Response(content=str(e), status_code=404, media_type="text/plain", headers=_PREVIEW_HEADERS)
    charset = "; charset=utf-8" if mime.startswith("text/") or mime in ("application/javascript", "application/json", "image/svg+xml") else ""
    return Response(content=content, media_type=f"{mime}{charset}", headers=_PREVIEW_HEADERS)


@site_drafts_router.get("/{draft_id}/preview-link")
async def preview_link(draft_id: int, request: Request):
    ctx = require_session(request)
    draft = sb.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="That website draft was not found.")
    if ctx.get("role") != "jts_admin":
        folder = ctx.get("client_folder_id")
        if not folder or int(folder) != int(draft.get("folder_id") or 0):
            raise HTTPException(status_code=404, detail="That website draft was not found.")
    return {"url": sb.preview_url(draft_id), "status": draft["status"], "site_url": draft.get("site_url")}


websites_router = APIRouter(prefix="/api/websites", tags=["Website builder"])


@websites_router.get("")
async def list_client_websites(request: Request, folder_id: int | None = None):
    """JTS admin: all websites (or one client's). Client users: only their own client's websites."""
    ctx = require_session(request)
    if ctx.get("role") != "jts_admin":
        own = ctx.get("client_folder_id")
        if not own or int(own) < 1:
            return {"websites": []}
        folder_id = int(own)
    return {"websites": sb.list_websites(folder_id)}
