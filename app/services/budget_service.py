"""Monthly AI budget per client, with alerts at configurable thresholds.

  - A JTS Admin sets, per client, a monthly limit in dollars and/or tokens (only the usage billed on the JTS key counts:
    a client using its own Anthropic key is not billed, so it has no budget to run out of).
  - When usage crosses a threshold (default 50%, 80% and 100%) an alert goes out ONCE for that month: an email to the JTS
    admins and the client's admins, and a notice in the Slack channel where it happened.
  - Optionally the assistant stops answering once the limit is reached ("hard stop") until the next month or a higher limit.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = [50, 80, 100]
_USAGE_CACHE: Dict[int, Tuple[Dict[str, Any], float]] = {}
_USAGE_TTL = 60.0


class BudgetError(Exception):
    """A user-facing problem with the values."""


def _ensure(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS client_budgets (
            folder_id INTEGER PRIMARY KEY,
            monthly_usd NUMERIC(12, 2),
            monthly_tokens BIGINT,
            thresholds VARCHAR(100) DEFAULT '50,80,100',
            hard_stop BOOLEAN DEFAULT FALSE,
            updated_by VARCHAR(255),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS client_budget_alerts (
            folder_id INTEGER NOT NULL,
            month CHAR(7) NOT NULL,
            threshold INTEGER NOT NULL,
            used_usd NUMERIC(12, 4),
            sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (folder_id, month, threshold)
        );
    """)


def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure(cur)
            result = fn(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parse_thresholds(raw: Any) -> List[int]:
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").replace(";", ",").split(",")
    out: List[int] = []
    for item in items:
        text = str(item).strip().rstrip("%").strip()
        if not text:
            continue
        try:
            n = int(text)
        except ValueError:
            raise BudgetError(f"'{item}' isn't a percentage. Use whole numbers like 50, 80, 100.")
        if not 1 <= n <= 500:
            raise BudgetError("Alert levels must be between 1% and 500%.")
        if n not in out:
            out.append(n)
    out.sort()
    if len(out) > 8:
        raise BudgetError("Please use 8 alert levels or fewer.")
    return out or list(DEFAULT_THRESHOLDS)


def get_budget(folder_id: int) -> Dict[str, Any]:
    def run(cur):
        cur.execute("SELECT * FROM client_budgets WHERE folder_id = %s;", (folder_id,))
        return cur.fetchone()

    row = _db(run)
    if not row:
        return {"folder_id": folder_id, "monthly_usd": None, "monthly_tokens": None, "thresholds": list(DEFAULT_THRESHOLDS), "hard_stop": False, "configured": False}
    d = dict(row)
    return {
        "folder_id": folder_id,
        "monthly_usd": float(d["monthly_usd"]) if d.get("monthly_usd") is not None else None,
        "monthly_tokens": int(d["monthly_tokens"]) if d.get("monthly_tokens") is not None else None,
        "thresholds": parse_thresholds(d.get("thresholds")),
        "hard_stop": bool(d.get("hard_stop")),
        "configured": d.get("monthly_usd") is not None or d.get("monthly_tokens") is not None,
    }


def set_budget(folder_id: int, *, monthly_usd: Optional[float], monthly_tokens: Optional[int], thresholds: Any, hard_stop: bool, by: str = "admin") -> Dict[str, Any]:
    if monthly_usd is not None and monthly_usd <= 0:
        raise BudgetError("The dollar limit must be more than 0 (or leave it empty for no dollar limit).")
    if monthly_tokens is not None and monthly_tokens <= 0:
        raise BudgetError("The token limit must be more than 0 (or leave it empty for no token limit).")
    if hard_stop and monthly_usd is None and monthly_tokens is None:
        raise BudgetError("Set a dollar or token limit before turning on 'stop at the limit'.")
    levels = parse_thresholds(thresholds)

    def run(cur):
        cur.execute("SELECT 1 FROM channel_folders WHERE id = %s;", (folder_id,))
        if not cur.fetchone():
            raise LookupError("That client doesn't exist.")
        cur.execute(
            """
            INSERT INTO client_budgets (folder_id, monthly_usd, monthly_tokens, thresholds, hard_stop, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (folder_id) DO UPDATE SET monthly_usd = EXCLUDED.monthly_usd, monthly_tokens = EXCLUDED.monthly_tokens,
                thresholds = EXCLUDED.thresholds, hard_stop = EXCLUDED.hard_stop, updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP;
            """,
            (folder_id, monthly_usd, monthly_tokens, ",".join(str(x) for x in levels), hard_stop, by),
        )

    _db(run)
    _USAGE_CACHE.pop(folder_id, None)
    return get_budget(folder_id)


# --------------------------------------------------------------------------- usage this month

def month_key(today: Optional[date] = None) -> str:
    d = today or datetime.now(timezone.utc).date()
    return f"{d.year:04d}-{d.month:02d}"


def month_usage(folder_id: int, fresh: bool = False) -> Dict[str, Any]:
    """Billed (JTS-key) usage so far this calendar month (UTC)."""
    hit = _USAGE_CACHE.get(folder_id)
    if not fresh and hit and time.time() < hit[1]:
        return dict(hit[0])
    from app.services.invoice_service import get_folder_usage

    today = datetime.now(timezone.utc).date()
    start = today.replace(day=1)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            usage = get_folder_usage(cur, folder_id, start, today)
    finally:
        conn.close()
    out = {"used_usd": float(usage["usage_cost_usd"]), "used_tokens": int(usage["total_tokens"]), "replies": int(usage["replies"]),
           "period_start": start.isoformat(), "period_end": today.isoformat(), "month": month_key(today)}
    _USAGE_CACHE[folder_id] = (out, time.time() + _USAGE_TTL)
    return dict(out)


def percent_used(budget: Dict[str, Any], usage: Dict[str, Any]) -> Optional[float]:
    """The larger of the dollar and token percentages (None when no limit is set)."""
    parts = []
    if budget.get("monthly_usd"):
        parts.append(usage["used_usd"] / float(budget["monthly_usd"]) * 100)
    if budget.get("monthly_tokens"):
        parts.append(usage["used_tokens"] / float(budget["monthly_tokens"]) * 100)
    return max(parts) if parts else None


def status(folder_id: int, fresh: bool = False) -> Dict[str, Any]:
    budget = get_budget(folder_id)
    usage = month_usage(folder_id, fresh=fresh)
    pct = percent_used(budget, usage)
    return {
        "budget": budget,
        "usage": usage,
        "percent": round(pct, 1) if pct is not None else None,
        "level": None if pct is None else ("over" if pct >= 100 else "warning" if pct >= min(budget["thresholds"] or [80]) and pct >= 80 else "ok"),
    }


# --------------------------------------------------------------------------- enforcement + alerts

def _billed_to_jts(channel_id: str, **ctx) -> bool:
    from app.services.channel_secrets_service import resolve_anthropic_key

    try:
        return resolve_anthropic_key(channel_id, **ctx)[1] == "jts"
    except Exception:
        return True


def blocked_message(channel_id: str, **ctx) -> Optional[str]:
    """The notice to post instead of answering when this client's hard-stop budget is used up. Never raises."""
    try:
        from app.services.channel_secrets_service import get_folder_id_for_channel

        folder_id = get_folder_id_for_channel(channel_id)
        if not folder_id:
            return None
        budget = get_budget(folder_id)
        if not (budget["configured"] and budget["hard_stop"]):
            return None
        if not _billed_to_jts(channel_id, **ctx):
            return None  # the client pays Anthropic directly, nothing of ours to run out of
        pct = percent_used(budget, month_usage(folder_id))
        if pct is not None and pct >= 100:
            limit = f"${budget['monthly_usd']:,.2f}" if budget.get("monthly_usd") else f"{budget['monthly_tokens']:,} tokens"
            return (f":no_entry: This client's monthly AI budget ({limit}) has been used up, so I'm paused until next month or until "
                    "a JTS administrator raises the limit.")
    except Exception as e:
        logger.warning(f"[BUDGET] Budget check skipped: {e}")
    return None


def _recipients(folder_id: int) -> List[str]:
    from app.tools.secrets_manager import get_secret

    emails: List[str] = []

    def run(cur):
        cur.execute(
            "SELECT email FROM dashboard_users WHERE email IS NOT NULL AND email <> '' AND (role IN ('jts_admin','admin') OR (role = 'client_admin' AND client_folder_id = %s));",
            (folder_id,),
        )
        return [r["email"] if isinstance(r, dict) else r[0] for r in cur.fetchall() or []]

    try:
        emails.extend(_db(run))
    except Exception as e:
        logger.warning(f"[BUDGET] Could not list alert recipients: {e}")
    extra = get_secret("BUDGET_ALERT_EMAILS", "") or ""
    emails.extend(x.strip() for x in extra.replace(";", ",").split(",") if x.strip())
    seen, out = set(), []
    for e in emails:
        k = e.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(e.strip())
    return out


def _claim_threshold(folder_id: int, month: str, threshold: int, used_usd: float) -> bool:
    def run(cur):
        cur.execute(
            "INSERT INTO client_budget_alerts (folder_id, month, threshold, used_usd) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING threshold;",
            (folder_id, month, threshold, used_usd),
        )
        return cur.fetchone() is not None

    return bool(_db(run))


def alert_text(folder_name: str, threshold: int, budget: Dict[str, Any], usage: Dict[str, Any], pct: float) -> str:
    bits = []
    if budget.get("monthly_usd"):
        bits.append(f"${usage['used_usd']:,.2f} of ${budget['monthly_usd']:,.2f}")
    if budget.get("monthly_tokens"):
        bits.append(f"{usage['used_tokens']:,} of {budget['monthly_tokens']:,} tokens")
    used = " · ".join(bits)
    if pct >= 100:
        tail = " The assistant is now paused for this client." if budget.get("hard_stop") else " The assistant keeps working; usage above the limit is still billed."
        return f"The monthly AI budget for {folder_name} has been reached ({used}).{tail}"
    return f"{folder_name} has used {threshold}% of its monthly AI budget ({used})."


def after_reply(channel_id: str, **ctx) -> Optional[str]:
    """Call after a billed reply. Sends the email for a newly crossed threshold and returns the text to post in Slack (or None)."""
    try:
        from app.services.channel_secrets_service import get_folder_id_for_channel

        folder_id = get_folder_id_for_channel(channel_id)
        if not folder_id:
            return None
        budget = get_budget(folder_id)
        if not budget["configured"] or not _billed_to_jts(channel_id, **ctx):
            return None
        usage = month_usage(folder_id, fresh=True)
        pct = percent_used(budget, usage)
        if pct is None:
            return None
        crossed = [t for t in budget["thresholds"] if pct >= t]
        newly = [t for t in crossed if _claim_threshold(folder_id, usage["month"], t, usage["used_usd"])]
        if not newly:
            return None
        top = max(newly)

        def name(cur):
            cur.execute("SELECT name FROM channel_folders WHERE id = %s;", (folder_id,))
            r = cur.fetchone()
            return (r["name"] if isinstance(r, dict) else r[0]) if r else f"Client {folder_id}"

        folder_name = _db(name)
        text = alert_text(folder_name, top, budget, usage, pct)
        try:
            from app.services.email_service import send_simple_email

            send_simple_email(
                _recipients(folder_id),
                f"AI budget alert: {folder_name} at {top}%",
                text + "\n\nYou can change the limit under Clients & Channels, then the client, then Budget.",
            )
        except Exception as e:
            logger.warning(f"[BUDGET] Alert email failed: {e}")
        return (":warning: " if pct < 100 else ":no_entry: ") + text
    except Exception as e:
        logger.warning(f"[BUDGET] Alert check skipped: {e}")
        return None
