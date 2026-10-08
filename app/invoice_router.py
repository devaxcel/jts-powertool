import asyncio
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth_router import require_session, require_jts_admin
from app.services.invoice_service import (
    InvoiceError,
    create_invoice,
    get_invoice,
    list_invoices,
    mark_invoice_paid,
    preview_invoice,
    void_invoice,
)

invoice_router = APIRouter(prefix="/api/invoices", tags=["Invoices"])


def _client_folder_for_invoices(ctx: dict) -> Optional[int]:
    """JTS admin -> None (all). Client admin -> own folder. Team members can't see invoices."""
    role = ctx.get("role")
    if role == "jts_admin":
        return None
    if role != "client_admin":
        raise HTTPException(status_code=403, detail="Only admins can see invoices.")
    folder_id = ctx.get("client_folder_id")
    if not folder_id:
        raise HTTPException(status_code=403, detail="Your account isn't linked to a client yet.")
    return int(folder_id)


def _user_label(ctx: dict) -> str:
    return ctx.get("username") or "admin"


class InvoiceCreate(BaseModel):
    folder_id: int
    period_start: date
    period_end: date
    organization_id: Optional[int] = None
    markup_percent: float = Field(0, ge=0, le=1000)
    due_days: int = Field(14, ge=0, le=365)
    notes: Optional[str] = Field(None, max_length=2000)


class MarkPaid(BaseModel):
    paid_on: date
    reference: Optional[str] = Field(None, max_length=255)


class VoidInvoice(BaseModel):
    reason: Optional[str] = Field(None, max_length=1000)


@invoice_router.get("")
async def list_client_invoices(request: Request, status: Optional[str] = None, folder_id: Optional[int] = None):
    ctx = require_session(request)
    own_folder = _client_folder_for_invoices(ctx)
    if own_folder is not None:
        folder_id = own_folder
    return JSONResponse(content={"invoices": list_invoices(folder_id=folder_id, status=status)})


@invoice_router.get("/monthly-report")
async def monthly_billing_report(request: Request, month: Optional[str] = None, folder_id: Optional[int] = None):
    """The month's usage and billing as a PDF. JTS Admin: one client (folder_id) or all clients. Client Admin: their own client."""
    from app.services import report_service

    ctx = require_session(request)
    own_folder = _client_folder_for_invoices(ctx)
    if own_folder is not None:
        folder_id = own_folder
    try:
        report = await asyncio.to_thread(report_service.build_report, folder_id, month)
        pdf = await asyncio.to_thread(report_service.render_pdf, report)
    except report_service.ReportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report_service.report_filename(report)}"'},
    )


@invoice_router.get("/preview")
async def preview_client_invoice(
    request: Request, folder_id: int, period_start: date, period_end: date, markup_percent: float = 0
):
    require_jts_admin(request)
    try:
        return JSONResponse(content=preview_invoice(folder_id, period_start, period_end, markup_percent))
    except InvoiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@invoice_router.post("")
async def create_client_invoice(payload: InvoiceCreate, request: Request):
    ctx = require_jts_admin(request)
    try:
        invoice = create_invoice(
            folder_id=payload.folder_id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            organization_id=payload.organization_id,
            markup_percent=payload.markup_percent,
            due_days=payload.due_days,
            notes=payload.notes,
            created_by=_user_label(ctx),
        )
    except InvoiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content={"invoice": invoice, "message": f"Invoice {invoice['invoice_number']} created."})


@invoice_router.get("/{invoice_id}")
async def get_client_invoice(invoice_id: int, request: Request):
    ctx = require_session(request)
    own_folder = _client_folder_for_invoices(ctx)
    invoice = get_invoice(invoice_id)
    if not invoice or (own_folder is not None and invoice.get("folder_id") != own_folder):
        raise HTTPException(status_code=404, detail="That invoice was not found.")
    return JSONResponse(content={"invoice": invoice})


@invoice_router.post("/{invoice_id}/mark-paid")
async def mark_client_invoice_paid(invoice_id: int, payload: MarkPaid, request: Request):
    ctx = require_jts_admin(request)
    try:
        invoice = mark_invoice_paid(invoice_id, payload.paid_on, payload.reference, _user_label(ctx))
    except InvoiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content={"invoice": invoice, "message": f"Invoice {invoice['invoice_number']} marked as paid."})


@invoice_router.post("/{invoice_id}/void")
async def void_client_invoice(invoice_id: int, payload: VoidInvoice, request: Request):
    ctx = require_jts_admin(request)
    try:
        invoice = void_invoice(invoice_id, payload.reason, _user_label(ctx))
    except InvoiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content={"invoice": invoice, "message": f"Invoice {invoice['invoice_number']} voided."})
