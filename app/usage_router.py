import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.auth_router import require_session, require_jts_admin, client_channel_scope, channel_in_scope
from app.services.usage_service import (
    get_usage_summary,
    get_usage_logs,
    clear_all_usage_logs,
    delete_usage_by_channel,
    delete_usage_by_user,
    delete_usage_by_channel_and_user,
    delete_single_usage_log,
    recalculate_all_usage_costs,
)

usage_router = APIRouter(prefix="/api/usage", tags=["Usage Telemetry"])

MAX_LOG_ROWS = 5000


def _parse_range(start: Optional[str], end: Optional[str]) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Inclusive YYYY-MM-DD dates (UTC) -> [start, end) datetimes. Missing values mean open-ended."""
    def parse(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"'{value}' is not a valid date (use YYYY-MM-DD).")

    s, e = parse(start), parse(end)
    if s and e and e < s:
        raise HTTPException(status_code=400, detail="The end date must be on or after the start date.")
    start_dt = datetime(s.year, s.month, s.day, tzinfo=timezone.utc) if s else None
    end_dt = datetime(e.year, e.month, e.day, tzinfo=timezone.utc) + timedelta(days=1) if e else None
    return start_dt, end_dt


def _in_scope(scope: Optional[set], item: dict) -> bool:
    return channel_in_scope(scope, item.get("channel_id"), item.get("channel_name"))


def _scoped_summary(request: Request, start: Optional[str], end: Optional[str]) -> dict:
    ctx = require_session(request)
    start_dt, end_dt = _parse_range(start, end)
    summary = get_usage_summary(start=start_dt, end=end_dt)
    scope = client_channel_scope(ctx)
    if scope is None:
        return summary

    by_channel = [c for c in summary.get("by_channel", []) if _in_scope(scope, c)]
    by_channel_user = [cu for cu in summary.get("by_channel_user", []) if _in_scope(scope, cu)]

    # People totals rebuilt from the client's own channels only.
    user_map: dict = {}
    for cu in by_channel_user:
        u = user_map.setdefault(cu.get("user_id"), {
            "workspace_id": cu.get("workspace_id"),
            "workspace_name": cu.get("workspace_name"),
            "user_id": cu.get("user_id"),
            "user_name": cu.get("user_name"),
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "total_cost_usd": 0.0,
        })
        for k in ("calls", "input_tokens", "output_tokens", "total_tokens"):
            u[k] += cu.get(k, 0) or 0
        u["total_cost_usd"] = round(u["total_cost_usd"] + float(cu.get("total_cost_usd") or 0), 6)

    # Totals come from exactly the rows the client can see, so cards and tables always agree.
    summary["by_channel"] = by_channel
    summary["by_channel_user"] = by_channel_user
    summary["by_user"] = sorted(user_map.values(), key=lambda x: x["total_cost_usd"], reverse=True)
    summary["total_calls"] = sum(c.get("calls", 0) or 0 for c in by_channel)
    summary["total_input_tokens"] = sum(c.get("input_tokens", 0) or 0 for c in by_channel)
    summary["total_output_tokens"] = sum(c.get("output_tokens", 0) or 0 for c in by_channel)
    summary["total_tokens"] = sum(c.get("total_tokens", 0) or 0 for c in by_channel)
    summary["total_cost_usd"] = round(sum(float(c.get("total_cost_usd") or 0) for c in by_channel), 6)
    summary["active_channels_count"] = len(by_channel)
    summary["active_users_count"] = len(user_map)
    allowed_ws = {c.get("workspace_id") for c in by_channel if c.get("workspace_id")}
    summary["workspaces"] = [w for w in summary.get("workspaces", []) if w.get("workspace_id") in allowed_ws]
    return summary


def _scoped_logs(request: Request, limit: int, start: Optional[str], end: Optional[str]) -> list:
    ctx = require_session(request)
    start_dt, end_dt = _parse_range(start, end)
    limit = max(1, min(int(limit or 100), MAX_LOG_ROWS))
    scope = client_channel_scope(ctx)
    # Clients need a wider fetch because other clients' rows are filtered out afterwards.
    logs = get_usage_logs(limit=MAX_LOG_ROWS if scope is not None else limit, start=start_dt, end=end_dt)
    return [l for l in logs if _in_scope(scope, l)][:limit]


@usage_router.get("/summary")
@usage_router.get("/billing")
async def fetch_usage_summary(request: Request, start: Optional[str] = None, end: Optional[str] = None):
    return JSONResponse(content=_scoped_summary(request, start, end))


@usage_router.get("/logs")
async def fetch_usage_logs(request: Request, limit: int = 100, start: Optional[str] = None, end: Optional[str] = None):
    return JSONResponse(content=_scoped_logs(request, limit, start, end))


@usage_router.get("/export")
async def export_usage_csv(
    request: Request,
    view: str = "channel",
    start: Optional[str] = None,
    end: Optional[str] = None,
    workspace_id: Optional[str] = None,
):
    """CSV download of the chosen view for the chosen period (clients get their own channels only)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    ws_ok = lambda row: not workspace_id or workspace_id == "ALL" or row.get("workspace_id") == workspace_id  # noqa: E731

    if view == "logs":
        rows = [r for r in _scoped_logs(request, MAX_LOG_ROWS, start, end) if ws_ok(r)]
        writer.writerow(["id", "time_utc", "workspace", "channel", "channel_id", "person", "slack_user_id", "model",
                         "tokens_read", "tokens_written", "total_tokens", "cost_usd"])
        for r in rows:
            writer.writerow([r["id"], r.get("created_at"), r.get("workspace_name"), r.get("channel_name"), r.get("channel_id"),
                             r.get("user_name"), r.get("user_id"), r.get("model"), r.get("input_tokens"),
                             r.get("output_tokens"), r.get("total_tokens"), f"{float(r.get('cost_usd') or 0):.6f}"])
    elif view in ("channel", "user", "channel_user"):
        summary = _scoped_summary(request, start, end)
        key = {"channel": "by_channel", "user": "by_user", "channel_user": "by_channel_user"}[view]
        rows = [r for r in summary.get(key, []) if ws_ok(r)]
        head = ["workspace"]
        if view in ("channel", "channel_user"):
            head += ["channel", "channel_id"]
        if view in ("user", "channel_user"):
            head += ["person", "slack_user_id"]
        writer.writerow(head + ["ai_replies", "tokens_read", "tokens_written", "total_tokens", "cost_usd"])
        for r in rows:
            line = [r.get("workspace_name")]
            if view in ("channel", "channel_user"):
                line += [r.get("channel_name"), r.get("channel_id")]
            if view in ("user", "channel_user"):
                line += [r.get("user_name"), r.get("user_id")]
            writer.writerow(line + [r.get("calls"), r.get("input_tokens"), r.get("output_tokens"), r.get("total_tokens"),
                                    f"{float(r.get('total_cost_usd') or 0):.6f}"])
    else:
        raise HTTPException(status_code=400, detail="view must be one of: channel, user, channel_user, logs")

    period = f"{start or 'start'}_to_{end or 'today'}"
    filename = f"jts-usage-{view}-{period}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _result(res: dict, fallback: str) -> JSONResponse:
    if res.get("status") == "error":
        return JSONResponse(status_code=500, content={"detail": res.get("error") or fallback})
    return JSONResponse(content=res)


@usage_router.delete("/clear")
@usage_router.delete("/billing/clear")
async def clear_usage_billing_logs(request: Request):
    require_jts_admin(request)
    return _result(clear_all_usage_logs(), "Failed to clear billing logs")


@usage_router.delete("/channel/{channel_id}")
async def delete_channel_billing(channel_id: str, request: Request):
    require_jts_admin(request)
    return _result(delete_usage_by_channel(channel_id), "Failed to delete channel records")


@usage_router.delete("/user/{user_id}")
async def delete_user_billing(user_id: str, request: Request):
    require_jts_admin(request)
    return _result(delete_usage_by_user(user_id), "Failed to delete user records")


@usage_router.delete("/channel/{channel_id}/user/{user_id}")
async def delete_channel_user_billing(channel_id: str, user_id: str, request: Request):
    require_jts_admin(request)
    return _result(delete_usage_by_channel_and_user(channel_id, user_id), "Failed to delete records")


@usage_router.delete("/logs/{log_id}")
async def delete_single_log_row(log_id: int, request: Request):
    require_jts_admin(request)
    return _result(delete_single_usage_log(log_id), "Failed to delete record")


@usage_router.post("/recalculate")
async def recalculate_usage_costs(request: Request):
    require_jts_admin(request)
    return _result(recalculate_all_usage_costs(), "Failed to recalculate costs")
