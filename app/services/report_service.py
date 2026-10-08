"""Monthly billing report (PDF).

One calendar month (UTC) of AI usage per client: how many replies, tokens and dollars on the JTS key (the billed part), by channel
and by person, the invoices for the month, and the client's monthly budget. It uses exactly the same figures as the invoices
(`invoice_service.get_folder_usage`), so the report, the Billing page and the invoice always agree.
A JTS Admin can produce it for one client or for all clients; a Client Admin only for their own client.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.db.session import get_db_connection

logger = logging.getLogger(__name__)


class ReportError(Exception):
    """A problem with a plain message for the person (bad month, unknown client)."""


# --------------------------------------------------------------------------- the month

def month_bounds(month: Optional[str], today: Optional[date] = None) -> Tuple[date, date, str]:
    """('2026-09' -> 1 Sep..30 Sep, 'September 2026'). Empty = the last full month. The current month is month-to-date.
    A future month is refused."""
    today = today or datetime.now(timezone.utc).date()
    if not month:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        year, mon = last_prev.year, last_prev.month
    else:
        try:
            year_s, mon_s = str(month).strip().split("-")
            year, mon = int(year_s), int(mon_s)
            if not (2000 <= year <= 2100 and 1 <= mon <= 12):
                raise ValueError
        except ValueError:
            raise ReportError("Please choose a month like 2026-09.")
    start = date(year, mon, 1)
    if start > today:
        raise ReportError("That month hasn't started yet.")
    nxt = date(year + (mon == 12), (mon % 12) + 1, 1)
    end = min(nxt - timedelta(days=1), today)
    return start, end, start.strftime("%B %Y")


# --------------------------------------------------------------------------- data

def _scope(cur, folder_id: int):
    """The channels and Slack workspaces whose usage counts for this client (same rule as invoices)."""
    from app.services.channel_secrets_service import _ensure_workspace_folder_column, channel_id_variants

    cur.execute("SELECT channel_id FROM channel_metadata WHERE folder_id = %s;", (folder_id,))
    stored = [str(r["channel_id"]).upper() for r in cur.fetchall() or [] if r.get("channel_id")]
    _ensure_workspace_folder_column(cur)
    cur.execute("SELECT team_id FROM slack_workspaces WHERE folder_id = %s;", (folder_id,))
    teams = [r["team_id"] for r in cur.fetchall() or []]
    match_ids: List[str] = []
    for c in stored:
        for v in channel_id_variants(c):
            if v not in match_ids:
                match_ids.append(v)
    return match_ids, teams


def get_folder_people(cur, folder_id: int, start: date, end: date) -> List[Dict[str, Any]]:
    """Billed (JTS-key) usage per person for the client in the period."""
    from app.services.invoice_service import _period_bounds
    from app.services.usage_service import resolve_user_display_name

    start_dt, end_dt = _period_bounds(start, end)
    match_ids, teams = _scope(cur, folder_id)
    if not match_ids and not teams:
        return []
    cur.execute(
        """
        SELECT user_id, COUNT(*) AS replies, COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost_usd), 0) AS cost
        FROM api_usage_logs
        WHERE COALESCE(key_source, 'jts') = 'jts'
          AND (UPPER(channel_id) = ANY(%s)
               OR (workspace_id = ANY(%s) AND NOT EXISTS (
                     SELECT 1 FROM channel_metadata cm
                     WHERE UPPER(cm.channel_id) = UPPER(api_usage_logs.channel_id) AND cm.folder_id IS NOT NULL)))
          AND created_at >= %s AND created_at < %s
        GROUP BY user_id ORDER BY cost DESC;
        """,
        (match_ids, teams, start_dt, end_dt),
    )
    rows = []
    for r in cur.fetchall() or []:
        uid = r["user_id"]
        rows.append({
            "person": resolve_user_display_name(uid) if uid else "Unknown",
            "replies": int(r["replies"]), "tokens": int(r["tokens"]), "cost_usd": float(r["cost"]),
        })
    return rows


def invoices_for_period(cur, folder_id: int, start: date, end: date) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT invoice_number, amount_usd, usage_cost_usd, markup_percent, status, due_date, period_start, period_end, paid_at
        FROM client_invoices
        WHERE folder_id = %s AND status <> 'void' AND period_start <= %s AND period_end >= %s
        ORDER BY period_start, id;
        """,
        (folder_id, end, start),
    )
    out = []
    for r in cur.fetchall() or []:
        out.append({
            "number": r["invoice_number"], "amount_usd": float(r["amount_usd"] or 0), "usage_cost_usd": float(r["usage_cost_usd"] or 0),
            "markup_percent": float(r["markup_percent"] or 0), "status": r["status"], "due_date": r["due_date"],
            "period": f"{r['period_start']} to {r['period_end']}", "paid_at": r.get("paid_at"),
        })
    return out


def build_client_section(cur, folder_id: int, start: date, end: date) -> Dict[str, Any]:
    from app.services import budget_service
    from app.services.invoice_service import get_folder_usage

    usage = get_folder_usage(cur, folder_id, start, end)
    people = get_folder_people(cur, folder_id, start, end)
    invoices = invoices_for_period(cur, folder_id, start, end)
    budget = None
    try:
        b = budget_service.get_budget(folder_id)
        if b.get("configured"):
            pct = budget_service.percent_used(b, {"used_usd": usage["usage_cost_usd"], "used_tokens": usage["total_tokens"]})
            budget = {"monthly_usd": b.get("monthly_usd"), "monthly_tokens": b.get("monthly_tokens"), "percent": round(pct, 1) if pct is not None else None,
                      "hard_stop": b.get("hard_stop")}
    except Exception as e:
        logger.debug(f"[REPORT] Budget lookup skipped for client {folder_id}: {e}")
    return {
        "folder_id": folder_id, "client": usage["folder_name"], "replies": usage["replies"], "tokens": usage["total_tokens"],
        "usage_cost_usd": usage["usage_cost_usd"], "own_key_replies": usage["non_billable_replies"],
        "channels": usage["line_items"], "people": people, "invoices": invoices, "budget": budget,
        "invoiced_usd": round(sum(i["amount_usd"] for i in invoices), 2),
    }


def build_report(folder_id: Optional[int], month: Optional[str]) -> Dict[str, Any]:
    """folder_id None = every client. Returns the data the PDF is drawn from."""
    start, end, label = month_bounds(month)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            from app.services.invoice_service import _ensure_invoices_table

            _ensure_invoices_table(cur)
            conn.commit()
            if folder_id is not None:
                cur.execute("SELECT id FROM channel_folders WHERE id = %s;", (folder_id,))
                if not cur.fetchone():
                    raise ReportError("That client was not found.")
                ids = [folder_id]
            else:
                cur.execute("SELECT id FROM channel_folders ORDER BY name;")
                ids = [r["id"] for r in cur.fetchall() or []]
            sections = [build_client_section(cur, i, start, end) for i in ids]
    finally:
        conn.close()
    return {
        "month_label": label, "period_start": start, "period_end": end, "partial": end < (date(start.year + (start.month == 12), (start.month % 12) + 1, 1) - timedelta(days=1)),
        "scope_label": sections[0]["client"] if folder_id is not None and sections else "All clients",
        "generated_at": datetime.now(timezone.utc), "sections": sections,
    }


# --------------------------------------------------------------------------- PDF

def _t(value: Any, limit: int = 60) -> str:
    """Text safe for the built-in PDF fonts (Latin-1), shortened."""
    text = str(value if value is not None else "")
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _usd(v: Any) -> str:
    v = float(v or 0)
    if v != 0 and abs(v) < 0.01:
        return f"${v:,.4f}"
    return f"${v:,.2f}"


def render_pdf(report: Dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    brand = colors.HexColor("#088ADA")
    soft = colors.HexColor("#f3f8fd")
    line = colors.HexColor("#e5e7eb")
    ink = colors.HexColor("#1f2937")
    muted = colors.HexColor("#6b7280")

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=20, textColor=brand, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=13, textColor=brand, spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, textColor=ink, spaceBefore=8, spaceAfter=3)
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName="Helvetica", fontSize=9, textColor=ink, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=muted, leading=10)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=10.5)

    def plain(rows, widths, right_cols=(), total_row=False):
        """A table whose cells are plain strings (right-aligned numbers work as text, not paragraphs)."""
        data = [[_t(c, 80) for c in r] for r in rows]
        t = Table(data, colWidths=widths, repeatRows=1)
        style = [
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("TEXTCOLOR", (0, 1), (-1, -1), ink),
            ("BACKGROUND", (0, 0), (-1, 0), brand), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, line),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if total_row:
            style += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), soft)]
        for c in right_cols:
            style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
        t.setStyle(TableStyle(style))
        return t

    buf = io.BytesIO()
    month_label = report["month_label"]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(16 * mm, 10 * mm, f"JTS PowerTool - Monthly billing report - {month_label} - {_t(report['scope_label'], 40)}")
        canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Monthly billing report {month_label}", author="JTS PowerTool")
    W = A4[0] - 32 * mm
    story: List[Any] = []

    sections = report["sections"]
    gen = report["generated_at"].strftime("%d %B %Y, %H:%M UTC")
    story.append(Paragraph("Monthly AI Usage &amp; Billing Report", h1))
    story.append(Paragraph(f"<b>{_t(month_label)}</b> &nbsp;&middot;&nbsp; {_t(report['scope_label'], 70)}", ParagraphStyle("sub", parent=body, fontSize=11, textColor=ink)))
    period = f"{report['period_start'].strftime('%d %b %Y')} to {report['period_end'].strftime('%d %b %Y')}"
    story.append(Paragraph(f"Period: {period} (UTC){' - month to date, the month is not finished' if report.get('partial') else ''} &nbsp;&middot;&nbsp; Prepared {gen}", small))
    story.append(Spacer(1, 6))

    total_cost = sum(s["usage_cost_usd"] for s in sections)
    total_invoiced = sum(s["invoiced_usd"] for s in sections)
    total_replies = sum(s["replies"] for s in sections)
    total_tokens = sum(s["tokens"] for s in sections)
    kpi = Table(
        [[Paragraph("<b>AI replies</b>", small), Paragraph("<b>Tokens used</b>", small), Paragraph("<b>Usage cost (billed on JTS key)</b>", small), Paragraph("<b>Invoiced</b>", small)],
         [Paragraph(f"<font size=14><b>{total_replies:,}</b></font>", body), Paragraph(f"<font size=14><b>{total_tokens:,}</b></font>", body),
          Paragraph(f"<font size=14><b>{_usd(total_cost)}</b></font>", body), Paragraph(f"<font size=14><b>{_usd(total_invoiced)}</b></font>", body)]],
        colWidths=[W / 4] * 4)
    kpi.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), soft), ("BOX", (0, 0), (-1, -1), 0.6, line), ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.white),
                             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story.append(kpi)

    if len(sections) > 1:
        story.append(Paragraph("Clients at a glance", h2))
        rows = [["Client", "AI replies", "Tokens", "Usage cost", "Invoiced", "Budget used"]]
        for s in sections:
            b = s.get("budget")
            rows.append([s["client"], f"{s['replies']:,}", f"{s['tokens']:,}", _usd(s["usage_cost_usd"]), _usd(s["invoiced_usd"]),
                         (f"{b['percent']}%" if b and b.get("percent") is not None else "-")])
        rows.append(["Total", f"{total_replies:,}", f"{total_tokens:,}", _usd(total_cost), _usd(total_invoiced), ""])
        story.append(plain(rows, [W * 0.30, W * 0.12, W * 0.16, W * 0.16, W * 0.14, W * 0.12], right_cols=(1, 2, 3, 4, 5), total_row=True))
        story.append(Paragraph("Only usage on the JTS key is billed. A client using its own Anthropic key has no billed usage here.", small))

    for s in sections:
        if len(sections) > 1:
            story.append(PageBreak())
        story.append(Paragraph(_t(s["client"], 80), h2))
        facts = [["AI replies (billed)", f"{s['replies']:,}"], ["Tokens used", f"{s['tokens']:,}"], ["Usage cost", _usd(s["usage_cost_usd"])]]
        if s["own_key_replies"]:
            facts.append(["Replies on the client's own key (not billed)", f"{s['own_key_replies']:,}"])
        b = s.get("budget")
        if b:
            limit = []
            if b.get("monthly_usd"):
                limit.append(_usd(b["monthly_usd"]))
            if b.get("monthly_tokens"):
                limit.append(f"{b['monthly_tokens']:,} tokens")
            facts.append(["Monthly budget", f"{' / '.join(limit)} - {b['percent']}% used" if b.get("percent") is not None else " / ".join(limit)])
        story.append(plain([["Summary", ""]] + facts, [W * 0.62, W * 0.38], right_cols=(1,)))

        story.append(Paragraph("Invoices for this month", h3))
        if s["invoices"]:
            rows = [["Invoice", "Period", "Usage cost", "Markup", "Amount", "Status"]]
            for i in s["invoices"]:
                rows.append([i["number"], i["period"], _usd(i["usage_cost_usd"]), f"{i['markup_percent']:g}%", _usd(i["amount_usd"]), i["status"].title()])
            story.append(plain(rows, [W * 0.20, W * 0.28, W * 0.14, W * 0.10, W * 0.14, W * 0.14], right_cols=(2, 3, 4)))
        else:
            story.append(Paragraph("No invoice has been made for this period yet.", small))

        story.append(Paragraph("By channel", h3))
        if s["channels"]:
            rows = [["Channel", "AI replies", "Tokens", "Cost"]]
            for c in s["channels"]:
                rows.append([c.get("channel_name") or c.get("channel_id"), f"{c['replies']:,}", f"{c['total_tokens']:,}", _usd(c["cost_usd"])])
            rows.append(["Total", f"{s['replies']:,}", f"{s['tokens']:,}", _usd(s["usage_cost_usd"])])
            story.append(plain(rows, [W * 0.46, W * 0.16, W * 0.20, W * 0.18], right_cols=(1, 2, 3), total_row=True))
        else:
            story.append(Paragraph("No billed usage in this period.", small))

        if s["people"]:
            story.append(Paragraph("By person", h3))
            rows = [["Person", "AI replies", "Tokens", "Cost"]]
            for p in s["people"][:25]:
                rows.append([p["person"], f"{p['replies']:,}", f"{p['tokens']:,}", _usd(p["cost_usd"])])
            story.append(plain(rows, [W * 0.46, W * 0.16, W * 0.20, W * 0.18], right_cols=(1, 2, 3)))
            if len(s["people"]) > 25:
                story.append(Paragraph(f"Showing the 25 highest of {len(s['people'])} people.", small))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Figures come from the same usage records as the invoices and the Usage &amp; Billing page. Costs are in US dollars and times are UTC. "
                           "Usage cost is what the AI used cost; the invoice amount adds the client's markup.", small))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def report_filename(report: Dict[str, Any]) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in report["scope_label"]).strip("-") or "report"
    return f"jts-billing-report-{report['period_start'].strftime('%Y-%m')}-{slug[:40]}.pdf"
