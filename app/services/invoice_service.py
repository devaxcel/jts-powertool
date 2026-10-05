"""
Client invoices: monthly (or any period) bills built from billable AI usage in a client folder's channels.
Payment is recorded manually by a JTS admin ("Mark as paid").
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

INVOICE_STATUSES = ("unpaid", "paid", "void")


class InvoiceError(Exception):
    """A user-facing problem (bad period, duplicate invoice, wrong status, ...)."""


def _ensure_invoices_table(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_invoices (
            id SERIAL PRIMARY KEY,
            invoice_number VARCHAR(40) UNIQUE,
            folder_id INTEGER,
            folder_name VARCHAR(255),
            organization_id INTEGER,
            bill_to JSONB DEFAULT '{}'::jsonb,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            replies INTEGER DEFAULT 0,
            total_tokens BIGINT DEFAULT 0,
            usage_cost_usd NUMERIC(14, 6) DEFAULT 0,
            markup_percent NUMERIC(6, 2) DEFAULT 0,
            amount_usd NUMERIC(12, 2) DEFAULT 0,
            line_items JSONB DEFAULT '[]'::jsonb,
            status VARCHAR(20) DEFAULT 'unpaid',
            due_date DATE,
            notes TEXT,
            paid_at DATE,
            payment_reference VARCHAR(255),
            void_reason TEXT,
            created_by VARCHAR(255),
            updated_by VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_client_invoices_folder ON client_invoices (folder_id, period_start);
    """)


def _period_bounds(period_start: date, period_end: date):
    """Inclusive dates -> [start, end) UTC datetimes."""
    if period_end < period_start:
        raise InvoiceError("The end date must be on or after the start date.")
    start_dt = datetime(period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc)
    end_dt = datetime(period_end.year, period_end.month, period_end.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start_dt, end_dt


def _money(value: Any, places: str = "0.01") -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def get_folder_usage(cur, folder_id: int, period_start: date, period_end: date) -> Dict[str, Any]:
    """Billable (JTS-key) usage per channel of a client folder within the inclusive period."""
    start_dt, end_dt = _period_bounds(period_start, period_end)

    cur.execute("SELECT id, name FROM channel_folders WHERE id = %s;", (folder_id,))
    folder = cur.fetchone()
    if not folder:
        raise InvoiceError("That client was not found.")

    cur.execute("SELECT channel_id, channel_name FROM channel_metadata WHERE folder_id = %s;", (folder_id,))
    channels = cur.fetchall() or []
    names = {str(c["channel_id"]).upper(): c["channel_name"] for c in channels if c.get("channel_id")}

    # Slack workspaces linked to this client: their chats that aren't in another client's folder (for example personal
    # chats with the bot) are billed to this client too.
    from app.services.channel_secrets_service import _ensure_workspace_folder_column
    _ensure_workspace_folder_column(cur)
    cur.execute("SELECT team_id FROM slack_workspaces WHERE folder_id = %s;", (folder_id,))
    linked_teams = [r["team_id"] for r in cur.fetchall() or []]

    from app.services.channel_secrets_service import channel_id_variants

    # A channel can be known by Slack's real id and by a stored alias (see LEGACY_CHANNEL_ALIASES): match both, and show
    # them as one line under the id stored on the client's folder.
    stored_of: Dict[str, str] = {}
    for stored in names:
        for v in channel_id_variants(stored):
            stored_of.setdefault(v, stored)
    match_ids = list(stored_of.keys())

    line_items: List[Dict[str, Any]] = []
    non_billable_replies = 0
    if names or linked_teams:
        scope = """(
                    UPPER(channel_id) = ANY(%s)
                    OR (workspace_id = ANY(%s) AND NOT EXISTS (
                          SELECT 1 FROM channel_metadata cm
                          WHERE UPPER(cm.channel_id) = UPPER(api_usage_logs.channel_id) AND cm.folder_id IS NOT NULL))
                  )"""
        cur.execute(f"""
            SELECT UPPER(channel_id) AS channel_key,
                   COUNT(*) AS replies,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM api_usage_logs
            WHERE COALESCE(key_source, 'jts') = 'jts'
              AND {scope}
              AND created_at >= %s AND created_at < %s
            GROUP BY UPPER(channel_id)
            ORDER BY cost_usd DESC;
        """, (match_ids, linked_teams, start_dt, end_dt))
        merged: Dict[str, Dict[str, Any]] = {}
        for r in cur.fetchall() or []:
            key = stored_of.get(r["channel_key"], r["channel_key"])
            m = merged.setdefault(key, {"replies": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": Decimal("0")})
            m["replies"] += int(r["replies"])
            m["input_tokens"] += int(r["input_tokens"])
            m["output_tokens"] += int(r["output_tokens"])
            m["total_tokens"] += int(r["total_tokens"])
            m["cost"] += _money(r["cost_usd"], "0.000001")
        missing = [k for k in merged if k not in names]
        if missing:
            cur.execute("SELECT UPPER(channel_id) AS k, channel_name FROM channel_metadata WHERE UPPER(channel_id) = ANY(%s);", (missing,))
            for m in cur.fetchall() or []:
                names[m["k"]] = m["channel_name"]
        for key, m in sorted(merged.items(), key=lambda kv: kv[1]["cost"], reverse=True):
            line_items.append({
                "channel_id": key,
                "channel_name": names.get(key) or key,
                "replies": m["replies"],
                "input_tokens": m["input_tokens"],
                "output_tokens": m["output_tokens"],
                "total_tokens": m["total_tokens"],
                "cost_usd": float(_money(m["cost"], "0.000001")),
            })
        # Replies in the same scope that used the client's own Anthropic key: not billed. Explains an empty invoice.
        cur.execute(f"""
            SELECT COUNT(*) AS replies FROM api_usage_logs
            WHERE COALESCE(key_source, 'jts') <> 'jts'
              AND {scope}
              AND created_at >= %s AND created_at < %s;
        """, (match_ids, linked_teams, start_dt, end_dt))
        nb = cur.fetchone()
        non_billable_replies = int((nb or {}).get("replies") or 0)

    usage_cost = sum((_money(i["cost_usd"], "0.000001") for i in line_items), Decimal("0"))
    return {
        "folder_id": folder["id"],
        "folder_name": folder["name"],
        "channel_count": len(names),
        "channel_names": sorted({str(c.get("channel_name") or c.get("channel_id")) for c in channels}),
        "non_billable_replies": non_billable_replies,
        "line_items": line_items,
        "replies": sum(i["replies"] for i in line_items),
        "total_tokens": sum(i["total_tokens"] for i in line_items),
        "usage_cost_usd": float(usage_cost),
    }


def _suggest_organization_id(cur, folder_id: int) -> Optional[int]:
    """The organization to bill: the one linked to the client itself, else the one of a user assigned to the client."""
    try:
        from app.services.channel_secrets_service import ensure_folder_org_column
        ensure_folder_org_column(cur)
        cur.execute("SELECT organization_id FROM channel_folders WHERE id = %s;", (folder_id,))
        own = cur.fetchone()
        if own and own.get("organization_id"):
            return own["organization_id"]
    except Exception:
        pass
    cur.execute("""
        SELECT organization_id FROM dashboard_users
        WHERE client_folder_id = %s AND organization_id IS NOT NULL
        ORDER BY CASE WHEN role = 'client_admin' THEN 0 ELSE 1 END, id
        LIMIT 1;
    """, (folder_id,))
    row = cur.fetchone()
    return row["organization_id"] if row else None


def _bill_to(cur, organization_id: Optional[int]) -> Dict[str, Any]:
    if not organization_id:
        return {}
    cur.execute(
        "SELECT id, name, poc, email, billing_email, phone, address FROM organizations WHERE id = %s;",
        (organization_id,),
    )
    org = cur.fetchone()
    if not org:
        raise InvoiceError("That organization was not found.")
    return {
        "organization_id": org["id"],
        "name": org["name"],
        "contact": org.get("poc"),
        "email": org.get("billing_email") or org.get("email"),
        "phone": org.get("phone"),
        "address": org.get("address"),
    }


def _amount(usage_cost: float, markup_percent: float) -> Decimal:
    return _money(Decimal(str(usage_cost)) * (Decimal("1") + Decimal(str(markup_percent)) / Decimal("100")))


def preview_invoice(folder_id: int, period_start: date, period_end: date, markup_percent: float = 0) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_invoices_table(cur)
            conn.commit()
            usage = get_folder_usage(cur, folder_id, period_start, period_end)
            usage["markup_percent"] = float(markup_percent or 0)
            usage["amount_usd"] = float(_amount(usage["usage_cost_usd"], markup_percent or 0))
            usage["suggested_organization_id"] = _suggest_organization_id(cur, folder_id)
            usage["overlapping_invoice"] = _find_overlap(cur, folder_id, period_start, period_end)
            return usage
    finally:
        conn.close()


def _find_overlap(cur, folder_id: int, period_start: date, period_end: date) -> Optional[str]:
    cur.execute("""
        SELECT invoice_number FROM client_invoices
        WHERE folder_id = %s AND status <> 'void'
          AND period_start <= %s AND period_end >= %s
        LIMIT 1;
    """, (folder_id, period_end, period_start))
    row = cur.fetchone()
    return row["invoice_number"] if row else None


def _row_to_invoice(row: Dict[str, Any]) -> Dict[str, Any]:
    inv = dict(row)
    for k in ("usage_cost_usd", "markup_percent", "amount_usd"):
        inv[k] = float(inv.get(k) or 0)
    for k in ("line_items", "bill_to"):
        if isinstance(inv.get(k), str):
            try:
                inv[k] = json.loads(inv[k])
            except Exception:
                inv[k] = [] if k == "line_items" else {}
    for k in ("period_start", "period_end", "due_date", "paid_at"):
        if isinstance(inv.get(k), date):
            inv[k] = inv[k].isoformat()
    for k in ("created_at", "updated_at"):
        if isinstance(inv.get(k), datetime):
            inv[k] = inv[k].isoformat()
    due = row.get("due_date")
    inv["display_status"] = (
        "overdue" if inv.get("status") == "unpaid" and isinstance(due, date) and due < date.today() else inv.get("status")
    )
    return inv


def create_invoice(
    *,
    folder_id: int,
    period_start: date,
    period_end: date,
    organization_id: Optional[int] = None,
    markup_percent: float = 0,
    due_days: int = 14,
    notes: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    if markup_percent is None or markup_percent < 0 or markup_percent > 1000:
        raise InvoiceError("Markup must be between 0 and 1000 percent.")
    if due_days is None or due_days < 0 or due_days > 365:
        raise InvoiceError("Payment due days must be between 0 and 365.")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_invoices_table(cur)
            overlap = _find_overlap(cur, folder_id, period_start, period_end)
            if overlap:
                raise InvoiceError(
                    f"Invoice {overlap} already covers part of this period for this client. Void it first if you need to re-issue."
                )
            usage = get_folder_usage(cur, folder_id, period_start, period_end)
            if organization_id is None:
                organization_id = _suggest_organization_id(cur, folder_id)
            bill_to = _bill_to(cur, organization_id)
            amount = _amount(usage["usage_cost_usd"], markup_percent)
            due_date = date.today() + timedelta(days=int(due_days))

            cur.execute("""
                INSERT INTO client_invoices (
                    folder_id, folder_name, organization_id, bill_to, period_start, period_end,
                    replies, total_tokens, usage_cost_usd, markup_percent, amount_usd, line_items,
                    status, due_date, notes, created_by, updated_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'unpaid', %s, %s, %s, %s)
                RETURNING id;
            """, (
                folder_id, usage["folder_name"], organization_id, json.dumps(bill_to), period_start, period_end,
                usage["replies"], usage["total_tokens"], usage["usage_cost_usd"], markup_percent, amount,
                json.dumps(usage["line_items"]), due_date, (notes or "").strip() or None, created_by, created_by,
            ))
            new_id = cur.fetchone()["id"]
            number = f"INV-{period_start.strftime('%Y%m')}-{new_id:04d}"
            cur.execute(
                "UPDATE client_invoices SET invoice_number = %s WHERE id = %s RETURNING *;",
                (number, new_id),
            )
            row = cur.fetchone()
        conn.commit()
        logger.info(f"[INVOICES] Created {number} for folder {folder_id}: ${amount} ({period_start}..{period_end})")
        return _row_to_invoice(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_invoices(folder_id: Optional[int] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_invoices_table(cur)
            conn.commit()
            clauses, params = [], []
            if folder_id is not None:
                clauses.append("folder_id = %s")
                params.append(folder_id)
            if status == "overdue":
                clauses.append("status = 'unpaid' AND due_date < CURRENT_DATE")
            elif status in INVOICE_STATUSES:
                clauses.append("status = %s")
                params.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cur.execute(f"SELECT * FROM client_invoices {where} ORDER BY period_start DESC, id DESC;", params)
            return [_row_to_invoice(r) for r in cur.fetchall() or []]
    finally:
        conn.close()


def get_invoice(invoice_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_invoices_table(cur)
            conn.commit()
            cur.execute("SELECT * FROM client_invoices WHERE id = %s;", (invoice_id,))
            row = cur.fetchone()
            return _row_to_invoice(row) if row else None
    finally:
        conn.close()


def _update_status(invoice_id: int, expected: str, sql: str, params: tuple) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_invoices_table(cur)
            cur.execute("SELECT status FROM client_invoices WHERE id = %s FOR UPDATE;", (invoice_id,))
            current = cur.fetchone()
            if not current:
                raise InvoiceError("That invoice was not found.")
            if current["status"] != expected:
                raise InvoiceError(f"This invoice is already {current['status']}.")
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
        return _row_to_invoice(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_invoice_paid(invoice_id: int, paid_on: date, reference: Optional[str], by: Optional[str]) -> Dict[str, Any]:
    return _update_status(
        invoice_id,
        "unpaid",
        """
        UPDATE client_invoices
        SET status = 'paid', paid_at = %s, payment_reference = %s, updated_by = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s RETURNING *;
        """,
        (paid_on, (reference or "").strip() or None, by, invoice_id),
    )


def void_invoice(invoice_id: int, reason: Optional[str], by: Optional[str]) -> Dict[str, Any]:
    return _update_status(
        invoice_id,
        "unpaid",
        """
        UPDATE client_invoices
        SET status = 'void', void_reason = %s, updated_by = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s RETURNING *;
        """,
        ((reason or "").strip() or None, by, invoice_id),
    )
