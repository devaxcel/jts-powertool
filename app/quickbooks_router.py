import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth_router import require_jts_admin
from app.github_router import _page
from app.services import invoice_service
from app.services import quickbooks_service as qb

logger = logging.getLogger(__name__)

# Public (protected by a single-use state link): the browser comes back from Intuit here.
quickbooks_public_router = APIRouter(prefix="/api/quickbooks", tags=["QuickBooks"])
# JTS Admins only
quickbooks_admin_router = APIRouter(prefix="/api", tags=["QuickBooks"])


@quickbooks_public_router.get("/connect")
async def start_connect(state: str = ""):
    if not qb.is_configured():
        return _page("QuickBooks isn't set up yet", "Please add the QuickBooks keys on the API Keys page first.", False)
    if not qb.peek_connect_link(state):
        return _page("This link has expired", "Connect links work once and for 15 minutes. Start again from System settings.", False)
    return RedirectResponse(qb.authorize_url(state), status_code=302)


@quickbooks_public_router.get("/callback")
async def finish_connect(state: str = "", code: Optional[str] = None, realmId: Optional[str] = None, error: Optional[str] = None):
    if error:
        return _page("QuickBooks wasn't connected", "Access wasn't granted in QuickBooks. Start again from System settings.", False)
    try:
        info = await qb.complete_connection(state, code, realmId)
    except qb.QuickBooksError as e:
        return _page("QuickBooks wasn't connected", str(e), False)
    except Exception as e:
        logger.error(f"[QUICKBOOKS] Unexpected error completing the connection: {type(e).__name__}")
        return _page("QuickBooks wasn't connected", "Something went wrong on our side. Please try again in a minute.", False)
    return _page("QuickBooks connected", f"JTS PowerTool is connected to {info.get('company_name') or 'your QuickBooks company'}. You can close this tab.", True)


@quickbooks_admin_router.get("/quickbooks/status")
async def quickbooks_status(request: Request):
    require_jts_admin(request)
    conn = qb.get_connection()
    return {
        "configured": qb.is_configured(),
        "environment": qb.environment(),
        "redirect_uri": qb.redirect_uri(),
        "connected": bool(conn and conn.get("status") == "active"),
        "status": conn.get("status") if conn else None,
        "company_name": conn.get("company_name") if conn else None,
        "connected_by": conn.get("connected_by") if conn else None,
        "connected_at": conn["connected_at"].isoformat() if conn and conn.get("connected_at") else None,
    }


@quickbooks_admin_router.post("/quickbooks/start")
async def quickbooks_start(request: Request):
    ctx = require_jts_admin(request)
    try:
        return {"url": qb.create_connect_link(ctx.get("username")), "expires_in_minutes": qb.CONNECT_LINK_TTL_SECONDS // 60}
    except qb.QuickBooksError as e:
        raise HTTPException(status_code=400, detail=str(e))


@quickbooks_admin_router.delete("/quickbooks")
async def quickbooks_disconnect(request: Request):
    require_jts_admin(request)
    return {"ok": True, "message": "QuickBooks was disconnected. Invoices already sent stay in QuickBooks." if qb.disconnect() else "QuickBooks wasn't connected."}


@quickbooks_admin_router.post("/invoices/{invoice_id}/quickbooks")
async def send_invoice_to_quickbooks(invoice_id: int, request: Request):
    require_jts_admin(request)
    invoice = invoice_service.get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    try:
        result = await qb.push_invoice(invoice)
    except qb.QuickBooksError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[QUICKBOOKS] Sending invoice {invoice_id} failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="QuickBooks didn't answer. Nothing was sent; please try again in a minute.")
    return {"ok": True, "message": f"Invoice {invoice.get('invoice_number')} was created in QuickBooks.", **result}
