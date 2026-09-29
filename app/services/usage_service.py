"""
Token Usage & API Cost Calculation Service.
Calculates token counts and cost USD per API call, tracked by Channel and User.
"""
import logging
from typing import Dict, List, Optional, Any
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

# Standard model pricing per 1,000,000 tokens (USD)
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # Haiku models (Anthropic - $1.00 / $5.00 per 1M tokens)
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-3-5-haiku": {"input": 1.00, "output": 5.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "haiku": {"input": 1.00, "output": 5.00},
    # Sonnet models
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "sonnet": {"input": 3.00, "output": 15.00},
    # Opus models
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "opus": {"input": 15.00, "output": 75.00},
    # OpenAI models
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}

DEFAULT_PRICING = {"input": 1.00, "output": 5.00}

KNOWN_USERS: Dict[str, str] = {
    "U0AQUL5KQMA": "Admin User",
    "U08SLP9LXUZ": "Admin User",
    "U0BSLP9LXUZ": "Admin User",
    "D08SLP9LXUZ": "Admin User",
    "D0BSLP9LXUZ": "Admin User",
}

def resolve_user_display_name(user_id: Optional[str], conn=None) -> str:
    if not user_id or user_id == "unknown":
        return "Unknown User"
    if user_id in KNOWN_USERS:
        return KNOWN_USERS[user_id]
    
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_name FROM claude_context_snapshots WHERE user_id = %s AND user_name IS NOT NULL AND user_name != '' LIMIT 1;",
                    (user_id,)
                )
                row = cur.fetchone()
                if row and row.get("user_name"):
                    return row["user_name"]
        except Exception as e:
            logger.debug(f"Could not resolve user_name from DB for {user_id}: {e}")
            
    return user_id


CANONICAL_CHANNEL_MAP: Dict[str, str] = {}

AUTHORITATIVE_CHANNEL_WORKSPACES: Dict[str, tuple[str, str]] = {
    "C08MV3EM9PY": ("T5ZMF56H5", "Axcel World"),
    "C0BMV3EM9PY": ("T5ZMF56H5", "Axcel World"),
    "C08V6S5UJ0P": ("T5ZMF56H5", "Axcel World"),
    "C0BV6S5UJ0P": ("T5ZMF56H5", "Axcel World"),
    "D08SLP9LXUZ": ("T5ZMF56H5", "Axcel World"),
    "D0BSLP9LXUZ": ("T5ZMF56H5", "Axcel World"),
    "C0C28B8V2PK": ("T02HKMBE09K", "JTS Team"),
}

AUTHORITATIVE_CHANNEL_NAMES: Dict[str, str] = {
    "C08MV3EM9PY": "#jts_powertool",
    "C0BMV3EM9PY": "#jts_powertool",
    "C08V6S5UJ0P": "#agents_working_projects",
    "C0BV6S5UJ0P": "#agents_working_projects",
    "D08SLP9LXUZ": "@Admin User (DM)",
    "D0BSLP9LXUZ": "@Admin User (DM)",
    "C0C28B8V2PK": "#jts_powertool",
}

def canonical_channel_id(
    channel_id: Optional[str],
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    channel_name: Optional[str] = None,
) -> str:
    """Preserves and normalizes Slack channel ID per-workspace."""
    if not channel_id:
        return ""
    cid = str(channel_id).strip()
    return CANONICAL_CHANNEL_MAP.get(cid.upper(), cid)


def resolve_channel_display_name(
    channel_id: Optional[str],
    db_channel_name: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
) -> str:
    channel_id = canonical_channel_id(channel_id, workspace_id=workspace_id, workspace_name=workspace_name, channel_name=db_channel_name)
    if not channel_id:
        return "unknown"

    clean_cid = channel_id.upper()
    if clean_cid in AUTHORITATIVE_CHANNEL_NAMES:
        return AUTHORITATIVE_CHANNEL_NAMES[clean_cid]
        
    if db_channel_name and db_channel_name != channel_id and db_channel_name not in ("jts-test", "#jts-test"):
        return db_channel_name

    try:
        from app.services.channel_secrets_service import resolve_slack_channel_name
        resolved = resolve_slack_channel_name(channel_id)
        if resolved and resolved != channel_id and resolved not in ("jts-test", "#jts-test"):
            return resolved
    except Exception:
        pass

    if db_channel_name and db_channel_name != channel_id:
        return db_channel_name
        
    if channel_id.startswith("D"):
        return f"DM ({channel_id})"
        
    return channel_id


def calculate_token_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Calculates total cost in USD for a given model, input tokens, and output tokens."""
    model_key = (model_name or "").strip().lower()
    
    # 1. Check exact or substring match in MODEL_PRICING
    pricing = None
    for k, p in MODEL_PRICING.items():
        if k in model_key:
            pricing = p
            break

    # 2. Intelligent model family fallback
    if not pricing:
        if "haiku" in model_key:
            if "3-haiku" in model_key:
                pricing = {"input": 0.25, "output": 1.25}
            else:
                pricing = {"input": 1.00, "output": 5.00}
        elif "opus" in model_key:
            pricing = {"input": 15.00, "output": 75.00}
        elif "mini" in model_key:
            pricing = {"input": 0.15, "output": 0.60}
        elif "sonnet" in model_key:
            pricing = {"input": 3.00, "output": 15.00}
        else:
            pricing = DEFAULT_PRICING

    input_cost = (max(0, input_tokens) / 1_000_000.0) * pricing["input"]
    output_cost = (max(0, output_tokens) / 1_000_000.0) * pricing["output"]
    return round(input_cost + output_cost, 6)


def normalize_workspace_info(
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    channel_id: Optional[str] = None,
    user_id: Optional[str] = None,
    conn=None,
) -> tuple[str, str]:
    """
    Strict workspace resolution based on authoritative channels, Admin User, and Slack's team_id.
    """
    # 1. Authoritative channel check
    if channel_id:
        clean_cid = canonical_channel_id(channel_id).upper()
        if clean_cid in AUTHORITATIVE_CHANNEL_WORKSPACES:
            return AUTHORITATIVE_CHANNEL_WORKSPACES[clean_cid]

    # 2. Authoritative user check (Admin User strictly belongs to Axcel World)
    if user_id and (user_id == "U0AQUL5KQMA" or str(user_id).lower() == "admin user"):
        return "T5ZMF56H5", "Axcel World"

    wid = (workspace_id or "").strip()
    wname = (workspace_name or "").strip()

    if not wid or wid in ("slack-workspace", "slack_workspace", "unknown", "T01...", "T_NEW_WORKSPACE"):
        logger.error("[USAGE_SERVICE] Missing or generic workspace_id provided for API usage logging.")
        return "UNKNOWN", "Unknown Workspace"

    # Fetch exact workspace_name from slack_workspaces database table by team_id
    close_conn = False
    if not conn or (hasattr(conn, 'closed') and conn.closed):
        try:
            conn = get_db_connection()
            close_conn = True
        except Exception:
            conn = None

    db_wname = ""
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT team_name FROM slack_workspaces WHERE team_id = %s LIMIT 1;", (wid,))
                row = cur.fetchone()
                if row and row.get("team_name"):
                    db_wname = row["team_name"]
        except Exception as e:
            logger.error(f"[USAGE_SERVICE] Error looking up workspace_name for team_id '{wid}': {e}")
        finally:
            if close_conn and conn:
                conn.close()

    if db_wname:
        return wid, db_wname
    
    if wname and wname not in ("slack-workspace", "slack_workspace", "unknown", "Second Workspace"):
        return wid, wname

    logger.error(f"[USAGE_SERVICE] Unknown workspace: team_id '{wid}' is not registered in slack_workspaces table.")
    return wid, f"Unknown Workspace ({wid})"





_key_source_column_ready = False


def _ensure_key_source_column(cur) -> None:
    """key_source: 'jts' = JTS key, billed to client; 'client' = client's own key, not billed."""
    global _key_source_column_ready
    if _key_source_column_ready:
        return
    cur.execute("ALTER TABLE api_usage_logs ADD COLUMN IF NOT EXISTS key_source VARCHAR(20) DEFAULT 'jts';")
    _key_source_column_ready = True


def record_api_usage(
    *,
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    user_id: Optional[str] = None,
    model: str = "claude-3-5-sonnet",
    input_tokens: int = 0,
    output_tokens: int = 0,
    key_source: str = "jts",
) -> Dict[str, Any]:
    """
    Logs an API call with token counts and calculated cost USD into PostgreSQL api_usage_logs table.
    Calls made with a client's own key (key_source='client') are logged with zero cost and excluded from billing.
    """
    channel_id = (channel_id or "unknown").strip()
    user_id = (user_id or "unknown").strip()
    model = (model or "claude-3-5-sonnet").strip()
    input_tokens = max(0, int(input_tokens))
    output_tokens = max(0, int(output_tokens))
    total_tokens = input_tokens + output_tokens
    key_source = "client" if key_source == "client" else "jts"
    cost_usd = 0.0 if key_source == "client" else calculate_token_cost(model, input_tokens, output_tokens)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_key_source_column(cur)
        conn.commit()
        workspace_id, workspace_name = normalize_workspace_info(
            workspace_id, workspace_name, channel_id=channel_id, conn=conn
        )
        channel_id = canonical_channel_id(
            channel_id,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            channel_name=channel_name,
        )

        logger.info(f"[PIPELINE_STEP_3_USAGE_LOG] record_api_usage called: workspace='{workspace_name}' ({workspace_id}), channel='{channel_name}' ({channel_id}), user='{user_id}'")

        # Auto-resolve and register channel metadata
        if channel_id and channel_id != "unknown":
            try:
                from app.services.channel_secrets_service import resolve_slack_channel_name
                resolved_cname = channel_name or resolve_slack_channel_name(channel_id, team_id=workspace_id)
                if resolved_cname:
                    with conn.cursor() as cur:
                        from app.services.channel_secrets_service import _save_channel_metadata
                        _save_channel_metadata(cur, channel_id, resolved_cname, "dm" if channel_id.startswith("D") else "channel")
                    conn.commit()
            except Exception as c_err:
                logger.debug(f"[USAGE_SERVICE] Channel name auto-resolution skipped: {c_err}")

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO api_usage_logs (workspace_id, workspace_name, channel_id, user_id, model, input_tokens, output_tokens, total_tokens, cost_usd, key_source, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id, created_at;
            """, (workspace_id, workspace_name, channel_id, user_id, model, input_tokens, output_tokens, total_tokens, cost_usd, key_source))
            row = cur.fetchone()
            conn.commit()
            logger.info(
                f"[USAGE_SERVICE] Logged API Call ID={row['id']}: workspace='{workspace_name}' ({workspace_id}), channel='{channel_id}', user='{user_id}', model='{model}', "
                f"tokens={total_tokens} (in={input_tokens}, out={output_tokens}), cost=${cost_usd:.6f}"
            )
            return {
                "id": row["id"],
                "workspace_id": workspace_id,
                "workspace_name": workspace_name,
                "channel_id": channel_id,
                "user_id": user_id,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "key_source": key_source,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
    except Exception as e:
        logger.error(f"[USAGE_SERVICE] Error recording API usage log: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            conn.close()


def get_usage_summary() -> Dict[str, Any]:
    """
    Retrieves aggregated token usage and cost statistics overall and grouped by channel & user.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_key_source_column(cur)
            conn.commit()
            # Aggregate totals overall (billable JTS-key calls only)
            cur.execute("""
                SELECT 
                    COUNT(*) as total_calls,
                    COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(cost_usd), 0.0) as total_cost_usd,
                    COUNT(DISTINCT channel_id) as active_channels_count,
                    COUNT(DISTINCT user_id) as active_users_count
                FROM api_usage_logs
                WHERE COALESCE(key_source, 'jts') = 'jts';
            """)
            overall = cur.fetchone() or {}

            # Grouped by channel
            cur.execute("""
                SELECT 
                    COALESCE(sw.team_id, cm.workspace_id, l.workspace_id, 'T5ZMF56H5') as workspace_id,
                    COALESCE(sw.team_name, cm.workspace_name, l.workspace_name, 'Axcel World') as workspace_name,
                    l.channel_id,
                    COALESCE(cm.channel_name, l.channel_id) as channel_name,
                    COUNT(*) as calls,
                    COALESCE(SUM(l.input_tokens), 0) as input_tokens,
                    COALESCE(SUM(l.output_tokens), 0) as output_tokens,
                    COALESCE(SUM(l.total_tokens), 0) as total_tokens,
                    COALESCE(SUM(l.cost_usd), 0.0) as total_cost_usd
                FROM api_usage_logs l
                LEFT JOIN channel_metadata cm ON l.channel_id = cm.channel_id
                LEFT JOIN slack_workspaces sw ON COALESCE(l.workspace_id, cm.workspace_id) = sw.team_id
                WHERE COALESCE(l.key_source, 'jts') = 'jts'
                GROUP BY
                    COALESCE(sw.team_id, cm.workspace_id, l.workspace_id, 'T5ZMF56H5'),
                    COALESCE(sw.team_name, cm.workspace_name, l.workspace_name, 'Axcel World'),
                    l.channel_id,
                    cm.channel_name
                ORDER BY total_cost_usd DESC;
            """)
            by_channel_rows = cur.fetchall() or []

            chan_map = {}
            for r in by_channel_rows:
                raw_wid = r.get("workspace_id")
                raw_wname = r.get("workspace_name")
                wid, wname = normalize_workspace_info(raw_wid, raw_wname, channel_id=r["channel_id"], conn=conn)
                c_id = canonical_channel_id(r["channel_id"], workspace_id=wid, workspace_name=wname, channel_name=r.get("channel_name"))
                c_name = resolve_channel_display_name(c_id, r["channel_name"], workspace_id=wid, workspace_name=wname)
                
                # Merge duplicate rows by unique canonical channel ID so each ID appears exactly once
                key = c_id.upper()
                if not key:
                    continue

                if key not in chan_map:
                    chan_map[key] = {
                        "workspace_id": wid,
                        "workspace_name": wname,
                        "channel_id": c_id,
                        "channel_name": c_name,
                        "calls": r["calls"],
                        "input_tokens": r["input_tokens"],
                        "output_tokens": r["output_tokens"],
                        "total_tokens": r["total_tokens"],
                        "total_cost_usd": float(r["total_cost_usd"]),
                    }
                else:
                    existing = chan_map[key]
                    existing["channel_id"] = canonical_channel_id(existing["channel_id"] or c_id, workspace_id=wid, workspace_name=wname, channel_name=c_name)
                    existing["calls"] += r["calls"]
                    existing["input_tokens"] += r["input_tokens"]
                    existing["output_tokens"] += r["output_tokens"]
                    existing["total_tokens"] += r["total_tokens"]
                    existing["total_cost_usd"] = round(existing["total_cost_usd"] + float(r["total_cost_usd"]), 6)
                    # Always ensure authoritative workspace is maintained
                    if key in AUTHORITATIVE_CHANNEL_WORKSPACES:
                        auth_wid, auth_wname = AUTHORITATIVE_CHANNEL_WORKSPACES[key]
                        existing["workspace_id"] = auth_wid
                        existing["workspace_name"] = auth_wname
                    elif not existing["workspace_id"] or existing["workspace_id"] == "UNKNOWN":
                        existing["workspace_id"] = wid
                        existing["workspace_name"] = wname

            by_channel = sorted(list(chan_map.values()), key=lambda x: x["total_cost_usd"], reverse=True)

            # Grouped by user
            cur.execute("""
                SELECT 
                    COALESCE(sw.team_id, l.workspace_id, 'T5ZMF56H5') as workspace_id,
                    COALESCE(sw.team_name, l.workspace_name, 'Axcel World') as workspace_name,
                    l.user_id,
                    COUNT(*) as calls,
                    COALESCE(SUM(l.input_tokens), 0) as input_tokens,
                    COALESCE(SUM(l.output_tokens), 0) as output_tokens,
                    COALESCE(SUM(l.total_tokens), 0) as total_tokens,
                    COALESCE(SUM(l.cost_usd), 0.0) as total_cost_usd
                FROM api_usage_logs l
                LEFT JOIN slack_workspaces sw ON l.workspace_id = sw.team_id
                WHERE COALESCE(l.key_source, 'jts') = 'jts'
                GROUP BY
                    COALESCE(sw.team_id, l.workspace_id, 'T5ZMF56H5'),
                    COALESCE(sw.team_name, l.workspace_name, 'Axcel World'),
                    l.user_id
                ORDER BY total_cost_usd DESC;
            """)
            by_user_rows = cur.fetchall() or []

            user_map = {}
            for r in by_user_rows:
                u_id = r["user_id"]
                u_name = resolve_user_display_name(u_id, conn)
                wid, wname = normalize_workspace_info(r.get("workspace_id"), r.get("workspace_name"), user_id=u_id, conn=conn)
                
                # Merge duplicate rows by user ID
                key = u_id.strip().upper() if u_id else u_name.strip().upper()
                if not key:
                    continue

                if key not in user_map:
                    user_map[key] = {
                        "workspace_id": wid,
                        "workspace_name": wname,
                        "user_id": u_id,
                        "user_name": u_name,
                        "calls": r["calls"],
                        "input_tokens": r["input_tokens"],
                        "output_tokens": r["output_tokens"],
                        "total_tokens": r["total_tokens"],
                        "total_cost_usd": float(r["total_cost_usd"]),
                    }
                else:
                    existing = user_map[key]
                    existing["calls"] += r["calls"]
                    existing["input_tokens"] += r["input_tokens"]
                    existing["output_tokens"] += r["output_tokens"]
                    existing["total_tokens"] += r["total_tokens"]
                    existing["total_cost_usd"] = round(existing["total_cost_usd"] + float(r["total_cost_usd"]), 6)
                    if u_id == "U0AQUL5KQMA" or (u_name and "admin user" in u_name.lower()):
                        existing["workspace_id"] = "T5ZMF56H5"
                        existing["workspace_name"] = "Axcel World"

            by_user = sorted(list(user_map.values()), key=lambda x: x["total_cost_usd"], reverse=True)

            # Grouped by channel AND user
            cur.execute("""
                SELECT 
                    COALESCE(sw.team_id, cm.workspace_id, l.workspace_id, 'T5ZMF56H5') as workspace_id,
                    COALESCE(sw.team_name, cm.workspace_name, l.workspace_name, 'Axcel World') as workspace_name,
                    l.channel_id,
                    COALESCE(cm.channel_name, l.channel_id) as channel_name,
                    l.user_id,
                    COUNT(*) as calls,
                    COALESCE(SUM(l.input_tokens), 0) as input_tokens,
                    COALESCE(SUM(l.output_tokens), 0) as output_tokens,
                    COALESCE(SUM(l.total_tokens), 0) as total_tokens,
                    COALESCE(SUM(l.cost_usd), 0.0) as total_cost_usd
                FROM api_usage_logs l
                LEFT JOIN channel_metadata cm ON l.channel_id = cm.channel_id
                LEFT JOIN slack_workspaces sw ON COALESCE(l.workspace_id, cm.workspace_id) = sw.team_id
                WHERE COALESCE(l.key_source, 'jts') = 'jts'
                GROUP BY
                    COALESCE(sw.team_id, cm.workspace_id, l.workspace_id, 'T5ZMF56H5'),
                    COALESCE(sw.team_name, cm.workspace_name, l.workspace_name, 'Axcel World'),
                    l.channel_id,
                    cm.channel_name,
                    l.user_id
                ORDER BY total_cost_usd DESC;
            """)
            by_channel_user_rows = cur.fetchall() or []

            cu_map = {}
            for r in by_channel_user_rows:
                raw_wid = r.get("workspace_id")
                raw_wname = r.get("workspace_name")
                wid, wname = normalize_workspace_info(raw_wid, raw_wname, channel_id=r["channel_id"], user_id=r["user_id"], conn=conn)
                c_id = canonical_channel_id(r["channel_id"], workspace_id=wid, workspace_name=wname, channel_name=r.get("channel_name"))
                c_name = resolve_channel_display_name(c_id, r["channel_name"], workspace_id=wid, workspace_name=wname)
                u_id = r["user_id"]
                u_name = resolve_user_display_name(u_id, conn)
                
                # Merge duplicate rows by channel ID + user ID
                key = f"{c_id.upper()}::{u_id.strip().upper()}"
                if not c_id:
                    continue

                if key not in cu_map:
                    cu_map[key] = {
                        "workspace_id": wid,
                        "workspace_name": wname,
                        "channel_id": c_id,
                        "channel_name": c_name,
                        "user_id": u_id,
                        "user_name": u_name,
                        "calls": r["calls"],
                        "input_tokens": r["input_tokens"],
                        "output_tokens": r["output_tokens"],
                        "total_tokens": r["total_tokens"],
                        "total_cost_usd": float(r["total_cost_usd"]),
                    }
                else:
                    existing = cu_map[key]
                    existing["channel_id"] = canonical_channel_id(existing["channel_id"] or c_id, workspace_id=wid, workspace_name=wname, channel_name=c_name)
                    existing["calls"] += r["calls"]
                    existing["input_tokens"] += r["input_tokens"]
                    existing["output_tokens"] += r["output_tokens"]
                    existing["total_tokens"] += r["total_tokens"]
                    existing["total_cost_usd"] = round(existing["total_cost_usd"] + float(r["total_cost_usd"]), 6)
                    if c_id.upper() in AUTHORITATIVE_CHANNEL_WORKSPACES:
                        auth_wid, auth_wname = AUTHORITATIVE_CHANNEL_WORKSPACES[c_id.upper()]
                        existing["workspace_id"] = auth_wid
                        existing["workspace_name"] = auth_wname

            by_channel_user = sorted(list(cu_map.values()), key=lambda x: x["total_cost_usd"], reverse=True)

            # Distinct list of active workspaces
            cur.execute("""
                SELECT team_id as workspace_id, team_name as workspace_name FROM slack_workspaces
                UNION
                SELECT DISTINCT workspace_id, workspace_name FROM api_usage_logs 
                WHERE workspace_id IS NOT NULL AND workspace_id != 'UNKNOWN' AND workspace_id NOT LIKE 'wrkspc_%%';
            """)
            ws_rows = cur.fetchall() or []
            workspaces = []
            seen_ws = set()
            for r in ws_rows:
                raw_wid = r.get("workspace_id")
                raw_wname = r.get("workspace_name")
                wid, wname = normalize_workspace_info(raw_wid, raw_wname, conn=conn)
                if wid and wid != "UNKNOWN" and not wid.startswith("wrkspc_") and wid not in seen_ws:
                    seen_ws.add(wid)
                    workspaces.append({
                        "workspace_id": wid,
                        "workspace_name": wname,
                    })

            return {
                "total_calls": overall.get("total_calls", 0),
                "total_input_tokens": overall.get("total_input_tokens", 0),
                "total_output_tokens": overall.get("total_output_tokens", 0),
                "total_tokens": overall.get("total_tokens", 0),
                "total_cost_usd": float(overall.get("total_cost_usd", 0.0)),
                "active_channels_count": overall.get("active_channels_count", 0),
                "active_users_count": overall.get("active_users_count", 0),
                "workspaces": workspaces,
                "by_channel": by_channel,
                "by_user": by_user,
                "by_channel_user": by_channel_user,
            }
    finally:
        if conn:
            conn.close()


def get_usage_logs(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Returns recent API call usage log records with channel names and user names.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_key_source_column(cur)
            conn.commit()
            cur.execute("""
                SELECT
                    l.id,
                    l.workspace_id,
                    l.workspace_name,
                    l.channel_id,
                    COALESCE(cm.channel_name, l.channel_id) as channel_name,
                    l.user_id,
                    l.model,
                    l.input_tokens,
                    l.output_tokens,
                    l.total_tokens,
                    l.cost_usd,
                    l.created_at,
                    sw.team_name as sw_team_name
                FROM api_usage_logs l
                LEFT JOIN channel_metadata cm ON l.channel_id = cm.channel_id
                LEFT JOIN slack_workspaces sw ON l.workspace_id = sw.team_id
                WHERE COALESCE(l.key_source, 'jts') = 'jts'
                ORDER BY l.created_at DESC
                LIMIT %s;
            """, (limit,))
            rows = cur.fetchall() or []
            logs = []
            seen_ids = set()
            for r in rows:
                if r["id"] in seen_ids:
                    continue
                seen_ids.add(r["id"])

                raw_wid = r.get("workspace_id")
                raw_wname = r.get("sw_team_name") or r.get("workspace_name")
                wid, wname = normalize_workspace_info(raw_wid, raw_wname, channel_id=r["channel_id"], user_id=r["user_id"], conn=conn)

                c_id = canonical_channel_id(r["channel_id"], workspace_id=wid, workspace_name=wname, channel_name=r.get("channel_name"))
                c_name = resolve_channel_display_name(c_id, r["channel_name"], workspace_id=wid, workspace_name=wname)
                u_id = r["user_id"]
                u_name = resolve_user_display_name(u_id, conn)

                logs.append({
                    "id": r["id"],
                    "workspace_id": wid,
                    "workspace_name": wname,
                    "channel_id": c_id,
                    "channel_name": c_name,
                    "user_id": u_id,
                    "user_name": u_name,
                    "model": r["model"],
                    "input_tokens": r["input_tokens"],
                    "output_tokens": r["output_tokens"],
                    "total_tokens": r["total_tokens"],
                    "cost_usd": float(r["cost_usd"]),
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })
            return logs
    finally:
        if conn:
            conn.close()



def clear_all_usage_logs() -> Dict[str, Any]:
    """
    Truncates/deletes all records from api_usage_logs table to reset billing telemetry.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_usage_logs;")
            deleted_count = cur.rowcount
            conn.commit()
            logger.info(f"[USAGE_SERVICE] Cleared {deleted_count} API usage log records.")
            return {"status": "success", "message": f"Cleared {deleted_count} billing log records.", "deleted_count": deleted_count}
    except Exception as e:
        logger.error(f"[USAGE_SERVICE] Error clearing API usage logs: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            conn.close()


def delete_usage_by_channel(channel_id: str) -> Dict[str, Any]:
    """Deletes all billing usage log records for a specific channel."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_usage_logs WHERE channel_id = %s;", (channel_id,))
            deleted_count = cur.rowcount
            conn.commit()
            logger.info(f"[USAGE_SERVICE] Deleted {deleted_count} billing log records for channel '{channel_id}'.")
            return {"status": "success", "message": f"Deleted {deleted_count} records for channel {channel_id}.", "deleted_count": deleted_count}
    except Exception as e:
        logger.error(f"[USAGE_SERVICE] Error deleting billing records for channel {channel_id}: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            conn.close()


def delete_usage_by_user(user_id: str) -> Dict[str, Any]:
    """Deletes all billing usage log records for a specific user."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_usage_logs WHERE user_id = %s;", (user_id,))
            deleted_count = cur.rowcount
            conn.commit()
            logger.info(f"[USAGE_SERVICE] Deleted {deleted_count} billing log records for user '{user_id}'.")
            return {"status": "success", "message": f"Deleted {deleted_count} records for user {user_id}.", "deleted_count": deleted_count}
    except Exception as e:
        logger.error(f"[USAGE_SERVICE] Error deleting billing records for user {user_id}: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            conn.close()


def delete_usage_by_channel_and_user(channel_id: str, user_id: str) -> Dict[str, Any]:
    """Deletes all billing usage log records for a specific user within a specific channel."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_usage_logs WHERE channel_id = %s AND user_id = %s;", (channel_id, user_id))
            deleted_count = cur.rowcount
            conn.commit()
            logger.info(f"[USAGE_SERVICE] Deleted {deleted_count} billing log records for channel '{channel_id}' user '{user_id}'.")
            return {"status": "success", "message": f"Deleted {deleted_count} records.", "deleted_count": deleted_count}
    except Exception as e:
        logger.error(f"[USAGE_SERVICE] Error deleting records for channel {channel_id} user {user_id}: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            conn.close()


def delete_single_usage_log(log_id: int) -> Dict[str, Any]:
    """Deletes a single API call usage log record by ID."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_usage_logs WHERE id = %s;", (log_id,))
            deleted_count = cur.rowcount
            conn.commit()
            logger.info(f"[USAGE_SERVICE] Deleted billing log record ID={log_id}.")
            return {"status": "success", "message": f"Deleted billing log #{log_id}.", "deleted_count": deleted_count}
    except Exception as e:
        logger.error(f"[USAGE_SERVICE] Error deleting billing log #{log_id}: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            conn.close()


def recalculate_all_usage_costs() -> Dict[str, Any]:
    """
    Recalculates and updates the cost_usd for all existing records in api_usage_logs
    using the corrected, accurate model pricing rates.
    """
    conn = get_db_connection()
    updated_count = 0
    try:
        with conn.cursor() as cur:
            _ensure_key_source_column(cur)
            # Client-own-key calls stay at zero cost
            cur.execute("SELECT id, model, input_tokens, output_tokens, cost_usd FROM api_usage_logs WHERE COALESCE(key_source, 'jts') = 'jts';")
            rows = cur.fetchall() or []
            for r in rows:
                row_id = r["id"]
                model = r["model"]
                in_toks = r["input_tokens"] or 0
                out_toks = r["output_tokens"] or 0
                new_cost = calculate_token_cost(model, in_toks, out_toks)
                cur.execute(
                    "UPDATE api_usage_logs SET cost_usd = %s WHERE id = %s;",
                    (new_cost, row_id)
                )
                updated_count += 1
            conn.commit()
            logger.info(f"[USAGE_SERVICE] Successfully recalculated token costs for {updated_count} rows.")
            return {"status": "success", "updated_count": updated_count}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"[USAGE_SERVICE] Error recalculating usage costs: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            conn.close()



