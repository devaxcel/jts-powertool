"""QuickBooks Online: send a client invoice from JTS PowerTool to JTS's own QuickBooks company.

One QuickBooks company is connected for the whole platform (the one JTS bills its clients from). The flow is one-way:
an invoice made here is created in QuickBooks (customer and a service item are created if they don't exist yet).
Marking it paid here does not change QuickBooks and the other way round; use QuickBooks' own payment features for that.

Setup (a JTS administrator, once):
  1. Create an app at developer.intuit.com and add the redirect URI  <PUBLIC_BASE_URL>/api/quickbooks/callback
  2. Save QUICKBOOKS_CLIENT_ID and QUICKBOOKS_CLIENT_SECRET on the API Keys page
     (and QUICKBOOKS_ENVIRONMENT = production when you leave the sandbox; the default is sandbox).
  3. Click "Connect QuickBooks" in System settings.
The refresh token Intuit gives back rotates, so it is stored (encrypted) in the database, not in a static secret.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from app.db.session import get_db_connection
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"
MINOR_VERSION = "70"
CONNECT_LINK_TTL_SECONDS = 15 * 60
ITEM_NAME = "JTS PowerTool AI usage"

_ACCESS: Dict[str, Any] = {"token": None, "exp": 0.0}


class QuickBooksError(Exception):
    """A problem with a plain message for the person."""


# --------------------------------------------------------------------------- config

def public_base_url() -> str:
    return (get_secret("PUBLIC_BASE_URL", "https://journeys.pe") or "https://journeys.pe").rstrip("/")


def redirect_uri() -> str:
    return f"{public_base_url()}/api/quickbooks/callback"


def environment() -> str:
    return "production" if (get_secret("QUICKBOOKS_ENVIRONMENT", "sandbox") or "sandbox").strip().lower() == "production" else "sandbox"


def api_base() -> str:
    return "https://quickbooks.api.intuit.com" if environment() == "production" else "https://sandbox-quickbooks.api.intuit.com"


def app_link(txn_id: str) -> str:
    host = "https://app.qbo.intuit.com" if environment() == "production" else "https://app.sandbox.qbo.intuit.com"
    return f"{host}/app/invoice?txnId={txn_id}"


def credentials() -> Dict[str, str]:
    return {"client_id": (get_secret("QUICKBOOKS_CLIENT_ID", "") or "").strip(), "client_secret": (get_secret("QUICKBOOKS_CLIENT_SECRET", "") or "").strip()}


def is_configured() -> bool:
    c = credentials()
    return bool(c["client_id"] and c["client_secret"])


# --------------------------------------------------------------------------- storage

def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            from app.services.invoice_service import _ensure_invoices_table

            _ensure_invoices_table(cur)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quickbooks_connection (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    realm_id VARCHAR(40) NOT NULL,
                    company_name VARCHAR(255),
                    refresh_token_enc TEXT NOT NULL,
                    environment VARCHAR(20),
                    status VARCHAR(20) DEFAULT 'active',
                    connected_by VARCHAR(255),
                    connected_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    refresh_expires_at TIMESTAMP WITH TIME ZONE
                );
                CREATE TABLE IF NOT EXISTS quickbooks_connect_links (
                    nonce VARCHAR(80) PRIMARY KEY,
                    created_by VARCHAR(255),
                    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    used_at TIMESTAMP WITH TIME ZONE
                );
                ALTER TABLE client_invoices ADD COLUMN IF NOT EXISTS qbo_invoice_id VARCHAR(40);
                ALTER TABLE client_invoices ADD COLUMN IF NOT EXISTS qbo_synced_at TIMESTAMP WITH TIME ZONE;
            """)
            result = fn(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _enc(v: str) -> str:
    from app.services import mfa_service

    return mfa_service._encrypt(v)


def _dec(v: str) -> str:
    from app.services import mfa_service

    return mfa_service._decrypt(v)


def get_connection() -> Optional[Dict[str, Any]]:
    def run(cur):
        cur.execute("SELECT * FROM quickbooks_connection WHERE id = 1;")
        r = cur.fetchone()
        return dict(r) if r else None

    return _db(run)


def _save_connection(realm_id: str, company: str, refresh_token: str, expires_in: int, by: str) -> None:
    def run(cur):
        cur.execute(
            """
            INSERT INTO quickbooks_connection (id, realm_id, company_name, refresh_token_enc, environment, status, connected_by, connected_at, refresh_expires_at)
            VALUES (1, %s, %s, %s, %s, 'active', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + (%s || ' seconds')::interval)
            ON CONFLICT (id) DO UPDATE SET realm_id = EXCLUDED.realm_id, company_name = EXCLUDED.company_name,
                refresh_token_enc = EXCLUDED.refresh_token_enc, environment = EXCLUDED.environment, status = 'active',
                connected_by = EXCLUDED.connected_by, connected_at = CURRENT_TIMESTAMP, refresh_expires_at = EXCLUDED.refresh_expires_at;
            """,
            (realm_id, company, _enc(refresh_token), environment(), by, str(int(expires_in or 8640000))),
        )

    _db(run)
    _ACCESS.update(token=None, exp=0.0)


def _rotate_refresh_token(refresh_token: str, expires_in: int) -> None:
    def run(cur):
        cur.execute(
            "UPDATE quickbooks_connection SET refresh_token_enc = %s, refresh_expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval WHERE id = 1;",
            (_enc(refresh_token), str(int(expires_in or 8640000))),
        )

    _db(run)


def _set_status(status: str) -> None:
    _db(lambda cur: cur.execute("UPDATE quickbooks_connection SET status = %s WHERE id = 1;", (status,)))


def disconnect() -> bool:
    _ACCESS.update(token=None, exp=0.0)

    def run(cur):
        cur.execute("DELETE FROM quickbooks_connection WHERE id = 1;")
        return cur.rowcount

    return bool(_db(run))


# --------------------------------------------------------------------------- connect (OAuth)

def create_connect_link(created_by: Optional[str]) -> str:
    if not is_configured():
        raise QuickBooksError("QuickBooks isn't set up yet. Add QUICKBOOKS_CLIENT_ID and QUICKBOOKS_CLIENT_SECRET on the API Keys page first.")
    nonce = secrets.token_urlsafe(32)

    def run(cur):
        cur.execute(
            "INSERT INTO quickbooks_connect_links (nonce, created_by, expires_at) VALUES (%s, %s, CURRENT_TIMESTAMP + (%s || ' seconds')::interval);",
            (nonce, created_by, str(CONNECT_LINK_TTL_SECONDS)),
        )

    _db(run)
    return f"{public_base_url()}/api/quickbooks/connect?{urlencode({'state': nonce})}"


def peek_connect_link(nonce: str) -> bool:
    def run(cur):
        cur.execute("SELECT 1 FROM quickbooks_connect_links WHERE nonce = %s AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP;", (nonce,))
        return cur.fetchone() is not None

    return bool(nonce) and bool(_db(run))


def _consume_connect_link(nonce: str) -> Optional[Dict[str, Any]]:
    def run(cur):
        cur.execute(
            "UPDATE quickbooks_connect_links SET used_at = CURRENT_TIMESTAMP WHERE nonce = %s AND used_at IS NULL AND expires_at > CURRENT_TIMESTAMP RETURNING *;",
            (nonce,),
        )
        r = cur.fetchone()
        return dict(r) if r else None

    return _db(run)


def authorize_url(nonce: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": credentials()["client_id"], "scope": SCOPE, "redirect_uri": redirect_uri(),
        "response_type": "code", "state": nonce,
    })


async def _token_request(form: Dict[str, str]) -> Dict[str, Any]:
    c = credentials()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            TOKEN_URL, data=form, auth=(c["client_id"], c["client_secret"]),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200 or not data.get("access_token"):
        err = data.get("error") or f"HTTP {resp.status_code}"
        logger.warning(f"[QUICKBOOKS] Token request failed: {err}")
        raise QuickBooksError("invalid_grant" if err == "invalid_grant" else "QuickBooks didn't accept the sign-in. Please try again.")
    return data


async def complete_connection(state: str, code: Optional[str], realm_id: Optional[str]) -> Dict[str, Any]:
    link = _consume_connect_link(state)
    if not link:
        raise QuickBooksError("This link has expired or was already used. Start again from System settings.")
    if not code or not realm_id:
        raise QuickBooksError("QuickBooks didn't return the company details. Please try again and choose a company.")
    data = await _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()})
    company = ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{api_base()}/v3/company/{realm_id}/companyinfo/{realm_id}", params={"minorversion": MINOR_VERSION},
                headers={"Authorization": f"Bearer {data['access_token']}", "Accept": "application/json"},
            )
        company = ((r.json().get("CompanyInfo") or {}).get("CompanyName")) or ""
    except Exception as e:
        logger.debug(f"[QUICKBOOKS] Could not read the company name: {e}")
    _save_connection(realm_id, company, data["refresh_token"], int(data.get("x_refresh_token_expires_in") or 8640000), link.get("created_by") or "admin")
    _ACCESS.update(token=data["access_token"], exp=time.time() + int(data.get("expires_in") or 3600) - 120)
    return {"realm_id": realm_id, "company_name": company}


# --------------------------------------------------------------------------- API calls

async def _access_token(conn: Dict[str, Any]) -> str:
    if _ACCESS["token"] and time.time() < _ACCESS["exp"]:
        return _ACCESS["token"]
    try:
        data = await _token_request({"grant_type": "refresh_token", "refresh_token": _dec(conn["refresh_token_enc"])})
    except QuickBooksError as e:
        if str(e) == "invalid_grant":
            _set_status("expired")
            raise QuickBooksError("QuickBooks access expired or was removed. Click “Connect QuickBooks” in System settings again.")
        raise
    if data.get("refresh_token") and data["refresh_token"] != _dec(conn["refresh_token_enc"]):
        _rotate_refresh_token(data["refresh_token"], int(data.get("x_refresh_token_expires_in") or 8640000))
    _ACCESS.update(token=data["access_token"], exp=time.time() + int(data.get("expires_in") or 3600) - 120)
    return data["access_token"]


def _active_connection() -> Dict[str, Any]:
    conn = get_connection()
    if not conn:
        raise QuickBooksError("QuickBooks isn't connected yet. A JTS administrator can connect it in System settings.")
    if conn.get("status") != "active":
        raise QuickBooksError("QuickBooks access expired. Click “Connect QuickBooks” in System settings again.")
    return conn


async def _api(conn: Dict[str, Any], method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{api_base()}/v3/company/{conn['realm_id']}{path}"
    params = {"minorversion": MINOR_VERSION, **(kwargs.pop("params", None) or {})}
    for attempt in (1, 2):
        token = await _access_token(conn)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, url, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, **kwargs)
        if resp.status_code == 401 and attempt == 1:
            _ACCESS.update(token=None, exp=0.0)
            continue
        return resp
    return resp


def _fault(resp: httpx.Response) -> str:
    try:
        faults = (resp.json().get("Fault") or {}).get("Error") or []
        if faults:
            return "; ".join(f"{f.get('Message')}: {f.get('Detail')}" for f in faults)[:400]
    except ValueError:
        pass
    return f"HTTP {resp.status_code}"


def _q(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("'", "\\'")


async def _query(conn: Dict[str, Any], sql: str) -> List[Dict[str, Any]]:
    resp = await _api(conn, "GET", "/query", params={"query": sql})
    if resp.status_code != 200:
        raise QuickBooksError(f"QuickBooks couldn't be searched ({_fault(resp)}).")
    qr = resp.json().get("QueryResponse") or {}
    for v in qr.values():
        if isinstance(v, list):
            return v
    return []


async def find_or_create_customer(conn: Dict[str, Any], name: str, email: Optional[str], phone: Optional[str], address: Optional[str]) -> str:
    name = (name or "").strip()[:100]
    if not name:
        raise QuickBooksError("The invoice has no customer name (link the client to an organization first).")
    found = await _query(conn, f"select Id from Customer where DisplayName = '{_q(name)}'")
    if found:
        return str(found[0]["Id"])
    body: Dict[str, Any] = {"DisplayName": name}
    if email:
        body["PrimaryEmailAddr"] = {"Address": email}
    if phone:
        body["PrimaryPhone"] = {"FreeFormNumber": phone}
    if address:
        body["BillAddr"] = {"Line1": str(address)[:500]}
    resp = await _api(conn, "POST", "/customer", json=body)
    if resp.status_code not in (200, 201):
        raise QuickBooksError(f"QuickBooks couldn't create the customer '{name}' ({_fault(resp)}).")
    return str(resp.json()["Customer"]["Id"])


async def find_or_create_item(conn: Dict[str, Any]) -> str:
    found = await _query(conn, f"select Id from Item where Name = '{_q(ITEM_NAME)}'")
    if found:
        return str(found[0]["Id"])
    accounts = await _query(conn, "select Id from Account where AccountType = 'Income' and Active = true maxresults 1")
    if not accounts:
        raise QuickBooksError("QuickBooks has no income account to book the invoice to. Add one in QuickBooks and try again.")
    resp = await _api(conn, "POST", "/item", json={
        "Name": ITEM_NAME, "Type": "Service", "IncomeAccountRef": {"value": str(accounts[0]["Id"])},
        "Description": "AI assistant usage billed through JTS PowerTool",
    })
    if resp.status_code not in (200, 201):
        raise QuickBooksError(f"QuickBooks couldn't create the service item ({_fault(resp)}).")
    return str(resp.json()["Item"]["Id"])


def _cents(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_lines(invoice: Dict[str, Any], item_id: str) -> List[Dict[str, Any]]:
    """One line per channel (markup included), adjusted by a few cents so the lines add up exactly to the invoice amount."""
    markup = Decimal(str(invoice.get("markup_percent") or 0))
    total = _cents(invoice.get("amount_usd"))
    items = [i for i in (invoice.get("line_items") or []) if i]
    period = f"{invoice.get('period_start')} to {invoice.get('period_end')}"
    lines: List[Dict[str, Any]] = []
    for i in items:
        amount = _cents(Decimal(str(i.get("cost_usd") or 0)) * (Decimal("1") + markup / Decimal("100")))
        lines.append({"amount": amount, "desc": f"AI usage {period}: {i.get('channel_name') or i.get('channel_id')} - {int(i.get('replies') or 0):,} replies, {int(i.get('total_tokens') or 0):,} tokens"})
    if not lines:
        lines.append({"amount": total, "desc": f"AI usage {period}"})
    diff = total - sum((l["amount"] for l in lines), Decimal("0"))
    if diff != 0:
        biggest = max(lines, key=lambda l: l["amount"])
        biggest["amount"] += diff
    return [
        {"DetailType": "SalesItemLineDetail", "Amount": float(l["amount"]), "Description": l["desc"][:4000],
         "SalesItemLineDetail": {"ItemRef": {"value": item_id}, "Qty": 1, "UnitPrice": float(l["amount"])}}
        for l in lines if l["amount"] != 0 or len(lines) == 1
    ]


def _date_of(value: Any, default: Optional[date] = None) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value[:10]
    return default.isoformat() if default else None


def build_invoice_body(invoice: Dict[str, Any], customer_id: str, item_id: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "CustomerRef": {"value": customer_id},
        "DocNumber": str(invoice.get("invoice_number") or "")[:21],
        "TxnDate": _date_of(invoice.get("created_at"), date.today()),
        "Line": build_lines(invoice, item_id),
        "PrivateNote": f"From JTS PowerTool invoice {invoice.get('invoice_number')} for client {invoice.get('folder_name')}",
    }
    due = _date_of(invoice.get("due_date"))
    if due:
        body["DueDate"] = due
    email = (invoice.get("bill_to") or {}).get("email")
    if email:
        body["BillEmail"] = {"Address": email}
    if invoice.get("notes"):
        body["CustomerMemo"] = {"value": str(invoice["notes"])[:1000]}
    return body


async def push_invoice(invoice: Dict[str, Any]) -> Dict[str, Any]:
    """Creates the invoice in QuickBooks. Raises QuickBooksError (plain message) on any problem."""
    if invoice.get("status") == "void":
        raise QuickBooksError("A voided invoice can't be sent to QuickBooks.")
    if invoice.get("qbo_invoice_id"):
        raise QuickBooksError("This invoice was already sent to QuickBooks.")
    conn = _active_connection()
    bill = invoice.get("bill_to") or {}
    customer_id = await find_or_create_customer(conn, bill.get("name") or invoice.get("folder_name"), bill.get("email"), bill.get("phone"), bill.get("address"))
    item_id = await find_or_create_item(conn)
    body = build_invoice_body(invoice, customer_id, item_id)
    resp = await _api(conn, "POST", "/invoice", json=body)
    if resp.status_code not in (200, 201):
        raise QuickBooksError(f"QuickBooks refused the invoice ({_fault(resp)}).")
    qbo = resp.json().get("Invoice") or {}
    qbo_id = str(qbo.get("Id") or "")

    def run(cur):
        cur.execute("UPDATE client_invoices SET qbo_invoice_id = %s, qbo_synced_at = CURRENT_TIMESTAMP WHERE id = %s;", (qbo_id, invoice["id"]))

    _db(run)
    return {"qbo_invoice_id": qbo_id, "doc_number": qbo.get("DocNumber"), "link": app_link(qbo_id), "total": qbo.get("TotalAmt")}
