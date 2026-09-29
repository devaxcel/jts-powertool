from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.auth_router import get_user_context, get_folder_channel_ids, get_user_from_db
from app.db.session import get_db_connection
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


@usage_router.get("/summary")
@usage_router.get("/billing")
async def fetch_usage_summary(request: Request):
    user_ctx = get_user_context(request)
    summary = get_usage_summary()

    if user_ctx.get("role") in ("client_admin", "client_standard"):
        folder_id = user_ctx.get("client_folder_id")
        if not folder_id and user_ctx.get("username"):
            db_u = get_user_from_db(user_ctx["username"])
            if db_u and db_u.get("client_folder_id"):
                folder_id = db_u["client_folder_id"]

        if not folder_id:
            folder_id = 2  # Fallback to default client folder if unassigned

        folder_channels = set(get_folder_channel_ids(folder_id)) if folder_id else set()
        folder_channels_lower = {x.lower() for x in folder_channels}

        def matches_folder(item: dict) -> bool:
            cid = str(item.get("channel_id") or "").strip()
            cname = str(item.get("channel_name") or "").strip()
            if cid and (cid in folder_channels or cid.lower() in folder_channels_lower or cid.lstrip("#@").lower() in folder_channels_lower):
                return True
            if cname and (cname in folder_channels or cname.lower() in folder_channels_lower or cname.lstrip("#@").lower() in folder_channels_lower):
                return True
            return False

        summary["by_channel"] = [c for c in summary.get("by_channel", []) if matches_folder(c)]
        summary["by_channel_user"] = [cu for cu in summary.get("by_channel_user", []) if matches_folder(cu)]
        summary["active_channels_count"] = len(summary["by_channel"])

        # Recompute by_user from folder-scoped channels only
        user_map = {}
        for cu in summary["by_channel_user"]:
            u_id = cu.get("user_id")
            if u_id not in user_map:
                user_map[u_id] = {
                    "workspace_id": cu.get("workspace_id"),
                    "workspace_name": cu.get("workspace_name"),
                    "user_id": u_id,
                    "user_name": cu.get("user_name"),
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0,
                }
            user_map[u_id]["calls"] += cu.get("calls", 0)
            user_map[u_id]["input_tokens"] += cu.get("input_tokens", 0)
            user_map[u_id]["output_tokens"] += cu.get("output_tokens", 0)
            user_map[u_id]["total_tokens"] += cu.get("total_tokens", 0)
            user_map[u_id]["total_cost_usd"] += cu.get("total_cost_usd", 0.0)

        summary["by_user"] = list(user_map.values())
        summary["active_users_count"] = len(user_map)

        # Also query conversation_messages directly for bot telemetry in folder channels
        conv_toks = 0
        conv_in_toks = 0
        conv_out_toks = 0
        conv_cost = 0.0
        conv_calls = 0
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        COALESCE(SUM(input_tokens), 0) as in_toks,
                        COALESCE(SUM(output_tokens), 0) as out_toks,
                        COALESCE(SUM(total_tokens), 0) as tot_toks,
                        COALESCE(SUM(cost_usd), 0.0) as cost,
                        COUNT(*) as cnt
                    FROM conversation_messages
                    WHERE (role = 'assistant' OR user_id = 'bot' OR user_name ILIKE '%Assistant%' OR user_name ILIKE '%Agent%')
                      AND COALESCE(cost_usd, 0) > 0
                      AND (
                          channel_id = ANY(%s) OR LOWER(channel_id) = ANY(%s)
                          OR LOWER(channel_id) IN (SELECT LOWER(channel_id) FROM channel_metadata WHERE folder_id = %s OR folder_id::text = %s)
                          OR LOWER(channel_id) IN (SELECT LOWER(channel_name) FROM channel_metadata WHERE folder_id = %s OR folder_id::text = %s)
                      );
                """, (list(folder_channels), list(folder_channels_lower), folder_id, str(folder_id), folder_id, str(folder_id)))
                crow = cur.fetchone()
                if crow:
                    conv_in_toks = int(crow.get("in_toks") or 0)
                    conv_out_toks = int(crow.get("out_toks") or 0)
                    conv_toks = int(crow.get("tot_toks") or 0)
                    conv_cost = float(crow.get("cost") or 0.0)
                    conv_calls = int(crow.get("cnt") or 0)
        except Exception as e:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        # Combine usage log channels sum with conversation messages sum
        log_in_toks = sum(c.get("input_tokens", 0) for c in summary["by_channel"])
        log_out_toks = sum(c.get("output_tokens", 0) for c in summary["by_channel"])
        log_toks = sum(c.get("total_tokens", 0) for c in summary["by_channel"])
        log_cost = sum(c.get("total_cost_usd", 0.0) for c in summary["by_channel"])
        log_calls = sum(c.get("calls", 0) for c in summary["by_channel"])

        total_in = max(log_in_toks, conv_in_toks)
        total_out = max(log_out_toks, conv_out_toks)
        total_toks = max(log_toks, conv_toks)
        total_cost = max(log_cost, conv_cost)
        total_c = max(log_calls, conv_calls)

        summary["total_calls"] = total_c
        summary["total_input_tokens"] = total_in
        summary["total_output_tokens"] = total_out
        summary["total_tokens"] = total_toks
        summary["total_cost_usd"] = round(total_cost, 6)

        # Scope workspaces array to workspaces present in the folder channels
        allowed_ws_ids = {c["workspace_id"] for c in summary["by_channel"] if c.get("workspace_id")}
        summary["workspaces"] = [w for w in summary.get("workspaces", []) if w.get("workspace_id") in allowed_ws_ids]

    return JSONResponse(content=summary)


@usage_router.get("/logs")
async def fetch_usage_logs(request: Request, limit: int = 100):
    user_ctx = get_user_context(request)
    logs = get_usage_logs(limit=limit)

    if user_ctx.get("role") in ("client_admin", "client_standard"):
        folder_id = user_ctx.get("client_folder_id")
        if not folder_id and user_ctx.get("username"):
            db_u = get_user_from_db(user_ctx["username"])
            if db_u and db_u.get("client_folder_id"):
                folder_id = db_u["client_folder_id"]

        if not folder_id:
            folder_id = 2  # Fallback to default client folder if unassigned

        folder_channels = set(get_folder_channel_ids(folder_id)) if folder_id else set()
        folder_channels_lower = {x.lower() for x in folder_channels}

        def matches_folder_log(l: dict) -> bool:
            cid = str(l.get("channel_id") or "").strip()
            cname = str(l.get("channel_name") or "").strip()
            if cid and (cid in folder_channels or cid.lower() in folder_channels_lower or cid.lstrip("#@").lower() in folder_channels_lower):
                return True
            if cname and (cname in folder_channels or cname.lower() in folder_channels_lower or cname.lstrip("#@").lower() in folder_channels_lower):
                return True
            return False

        logs = [l for l in logs if matches_folder_log(l)]

    return JSONResponse(content=logs)


@usage_router.delete("/clear")
@usage_router.delete("/billing/clear")
async def clear_usage_billing_logs(request: Request):
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        return JSONResponse(status_code=403, content={"detail": "Access denied: Only JTS Admin can manage billing records."})

    res = clear_all_usage_logs()
    if res.get("status") == "error":
        return JSONResponse(status_code=500, content={"detail": res.get("error", "Failed to clear billing logs")})
    return JSONResponse(content=res)


@usage_router.delete("/channel/{channel_id}")
async def delete_channel_billing(channel_id: str, request: Request):
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        return JSONResponse(status_code=403, content={"detail": "Access denied: Only JTS Admin can manage billing records."})

    res = delete_usage_by_channel(channel_id)
    if res.get("status") == "error":
        return JSONResponse(status_code=500, content={"detail": res.get("error")})
    return JSONResponse(content=res)


@usage_router.delete("/user/{user_id}")
async def delete_user_billing(user_id: str, request: Request):
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        return JSONResponse(status_code=403, content={"detail": "Access denied: Only JTS Admin can manage billing records."})

    res = delete_usage_by_user(user_id)
    if res.get("status") == "error":
        return JSONResponse(status_code=500, content={"detail": res.get("error")})
    return JSONResponse(content=res)


@usage_router.delete("/channel/{channel_id}/user/{user_id}")
async def delete_channel_user_billing(channel_id: str, user_id: str, request: Request):
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        return JSONResponse(status_code=403, content={"detail": "Access denied: Only JTS Admin can manage billing records."})

    res = delete_usage_by_channel_and_user(channel_id, user_id)
    if res.get("status") == "error":
        return JSONResponse(status_code=500, content={"detail": res.get("error")})
    return JSONResponse(content=res)


@usage_router.delete("/logs/{log_id}")
async def delete_single_log_row(log_id: int, request: Request):
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        return JSONResponse(status_code=403, content={"detail": "Access denied: Only JTS Admin can manage billing records."})

    res = delete_single_usage_log(log_id)
    if res.get("status") == "error":
        return JSONResponse(status_code=500, content={"detail": res.get("error")})
    return JSONResponse(content=res)


@usage_router.post("/recalculate")
async def recalculate_usage_costs(request: Request):
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        return JSONResponse(status_code=403, content={"detail": "Access denied: Only JTS Admin can manage billing records."})

    res = recalculate_all_usage_costs()
    if res.get("status") == "error":
        return JSONResponse(status_code=500, content={"detail": res.get("error")})
    return JSONResponse(content=res)



