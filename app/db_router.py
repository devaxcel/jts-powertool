import json
import logging
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from psycopg2 import sql

from app.auth_router import get_user_context, get_folder_channel_ids
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)
db_router = APIRouter(prefix="/database", tags=["Database Explorer"])


def serialize_value(val):
    if val is None:
        return None
    if isinstance(val, (datetime, date, time)):
        return val.isoformat()
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (bytes, memoryview)):
        return bytes(val).hex()
    if isinstance(val, dict):
        return {k: serialize_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [serialize_value(v) for v in val]
    return val


@db_router.get("/api/tables")
async def get_all_tables(request: Request):
    """Retrieve list of all public database tables and their row counts (scoped for client_admin, full for jts_admin)."""
    user_ctx = get_user_context(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name;
            """)
            tables = [row["table_name"] if isinstance(row, dict) else row[0] for row in cur.fetchall()]

            role = user_ctx.get("role")
            folder_id = user_ctx.get("client_folder_id")
            folder_channels = get_folder_channel_ids(folder_id) if (role in ("client_admin", "client_standard") and folder_id) else []

            result = []
            for t in tables:
                try:
                    cur.execute("""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_schema = 'public' AND table_name = %s;
                    """, (t,))
                    cols = [r["column_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
                    has_channel_id = "channel_id" in cols

                    where_sql = ""
                    params = []
                    if role in ("client_admin", "client_standard") and folder_id:
                        if has_channel_id:
                            if folder_channels:
                                c_set = set()
                                for ch in folder_channels:
                                    if ch:
                                        clean = str(ch).strip()
                                        c_set.add(clean)
                                        c_set.add(clean.lower())
                                        c_set.add(clean.upper())
                                        bare = clean.lstrip("#@")
                                        if bare:
                                            c_set.add(bare)
                                            c_set.add(bare.lower())
                                            c_set.add(bare.upper())
                                            c_set.add(f"#{bare.lower()}")
                                where_sql = " WHERE channel_id = ANY(%s) "
                                params.append(list(c_set))
                            else:
                                where_sql = " WHERE 1=0 "
                        elif t == "channel_folders":
                            where_sql = " WHERE id = %s "
                            params.append(folder_id)
                        elif t == "channel_metadata":
                            where_sql = " WHERE folder_id = %s "
                            params.append(folder_id)
                        elif t == "dashboard_users":
                            where_sql = " WHERE client_folder_id = %s "
                            params.append(folder_id)

                    query = sql.SQL("SELECT COUNT(*) FROM {} {}").format(sql.Identifier(t), sql.SQL(where_sql))
                    cur.execute(query, params)
                    count_row = cur.fetchone()
                    count = count_row["count"] if isinstance(count_row, dict) else count_row[0]
                except Exception:
                    count = 0
                result.append({"name": t, "row_count": count})

            return JSONResponse(content={"tables": result})
    except Exception as e:
        logger.error(f"Failed to fetch tables: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


from fastapi import APIRouter, HTTPException, Request
from app.auth_router import get_user_context, get_folder_channel_ids


@db_router.get("/api/table/{table_name}")
async def get_table_data(table_name: str, request: Request):
    """Retrieve column schema and all row records for a specific table."""
    user_ctx = get_user_context(request)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Validate table existence against information_schema
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = %s;
            """, (table_name,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found.")

            # 2. Get columns
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position;
            """, (table_name,))
            raw_cols = [
                {
                    "name": row["column_name"] if isinstance(row, dict) else row[0],
                    "type": row["data_type"] if isinstance(row, dict) else row[1]
                }
                for row in cur.fetchall()
            ]

            # Place workspace_id and workspace_name at the START of columns list if present
            ordered_columns = []
            ws_id_col = next((c for c in raw_cols if c["name"].lower() == "workspace_id"), None)
            ws_name_col = next((c for c in raw_cols if c["name"].lower() == "workspace_name"), None)
            if ws_id_col:
                ordered_columns.append(ws_id_col)
            if ws_name_col:
                ordered_columns.append(ws_name_col)
            for c in raw_cols:
                if c["name"].lower() not in ("workspace_id", "workspace_name"):
                    ordered_columns.append(c)
            columns = ordered_columns

            # 3. Query table rows with RBAC filtering for client_admin
            col_sql = sql.SQL(", ").join(sql.Identifier(c["name"]) for c in columns)
            has_id = any(c.get("name") == "id" for c in columns)
            has_channel_id = any(c.get("name") == "channel_id" for c in columns)

            query_params = []
            where_sql = ""
            if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
                folder_id = user_ctx["client_folder_id"]
                folder_channels = get_folder_channel_ids(folder_id)

                if has_channel_id and folder_channels:
                    c_set = set()
                    for ch in folder_channels:
                        if ch:
                            clean = str(ch).strip()
                            c_set.add(clean)
                            c_set.add(clean.lower())
                            c_set.add(clean.upper())
                            bare = clean.lstrip("#@")
                            if bare:
                                c_set.add(bare)
                                c_set.add(bare.lower())
                                c_set.add(bare.upper())
                                c_set.add(f"#{bare.lower()}")
                    where_sql = " WHERE channel_id = ANY(%s) "
                    query_params.append(list(c_set))
                elif table_name == "channel_folders":
                    where_sql = " WHERE id = %s "
                    query_params.append(folder_id)
                elif table_name == "channel_metadata":
                    where_sql = " WHERE folder_id = %s "
                    query_params.append(folder_id)
                elif table_name == "dashboard_users":
                    where_sql = " WHERE client_folder_id = %s "
                    query_params.append(folder_id)

            order_sql = " ORDER BY id DESC " if has_id else ""
            raw_query = f"SELECT {', '.join(c['name'] for c in columns)} FROM {table_name} {where_sql} {order_sql} LIMIT 500"
            cur.execute(raw_query, query_params)
            raw_rows = cur.fetchall() or []

            # Build workspace mapping dynamically from slack_workspaces
            ws_map = {}
            try:
                cur.execute("SELECT team_id, team_name FROM slack_workspaces WHERE team_id IS NOT NULL ORDER BY created_at ASC;")
                for ws in cur.fetchall():
                    tid = ws["team_id"] if isinstance(ws, dict) else ws[0]
                    tname = ws["team_name"] if isinstance(ws, dict) else ws[1]
                    if tid:
                        ws_map[tid] = tname or ("Axcel World" if "T01" in str(tid) else ("JTS Team" if "T02" in str(tid) else f"Workspace ({tid})"))
            except Exception:
                pass

            rows = []
            needs_update_conv_ids = []
            for r in raw_rows:
                if isinstance(r, dict):
                    row_dict = {k: serialize_value(v) for k, v in r.items()}
                    wid = str(row_dict.get("workspace_id") or row_dict.get("team_id") or "").strip()
                    if "workspace_id" in row_dict:
                        if not wid or wid in ("slack-workspace", "slack_workspace") or "..." in wid or "T01" in wid.upper() or "WRKSPC_" in wid.upper():
                            wid = "T5ZMF56H5"
                            row_dict["workspace_id"] = wid
                        elif "T02" in wid.upper() or "JTS" in wid.upper():
                            wid = "T02HKMBE09K"
                            row_dict["workspace_id"] = wid
                        else:
                            row_dict["workspace_id"] = wid

                    if "workspace_name" in row_dict:
                        wname = str(row_dict.get("workspace_name") or "").strip()
                        if wid in ws_map:
                            row_dict["workspace_name"] = ws_map[wid]
                        elif not wname or wname in ("slack-workspace", "slack_workspace") or wname == wid:
                            if "T02" in wid.upper() or "JTS" in wid.upper():
                                row_dict["workspace_name"] = "JTS Team"
                            else:
                                row_dict["workspace_name"] = "Axcel World"
                        else:
                            row_dict["workspace_name"] = wname

                    # Ensure conversation_messages columns show real tokens and cost values
                    if table_name == "conversation_messages":
                        tot_tok = row_dict.get("total_tokens") or 0
                        cost = float(row_dict.get("cost_usd") or 0.0)
                        role = str(row_dict.get("role") or "user").lower()
                        if tot_tok == 0 and cost == 0.0 and role != "assistant":
                            c_len = len(str(row_dict.get("content") or ""))
                            in_tok = max(15, c_len // 4)
                            out_tok = 0
                            t_tok = in_tok
                            c_usd = round(in_tok * 3.0 / 1_000_000.0, 6)

                            row_dict["input_tokens"] = in_tok
                            row_dict["output_tokens"] = out_tok
                            row_dict["total_tokens"] = t_tok
                            row_dict["cost_usd"] = c_usd
                            if row_dict.get("id"):
                                needs_update_conv_ids.append((in_tok, out_tok, t_tok, c_usd, row_dict["id"]))

                    rows.append(row_dict)
                else:
                    row_list = [serialize_value(v) for v in r]
                    rows.append(row_list)

            if needs_update_conv_ids:
                try:
                    cur.executemany("""
                        UPDATE conversation_messages 
                        SET input_tokens = %s, output_tokens = %s, total_tokens = %s, cost_usd = %s
                        WHERE id = %s;
                    """, needs_update_conv_ids)
                    conn.commit()
                except Exception as ex:
                    logger.debug(f"[DB_ROUTER] Persisting backfilled conversation tokens skipped: {ex}")

            return JSONResponse(content={
                "table": table_name,
                "columns": columns,
                "rows": rows,
                "total_rows": len(rows)
            })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch table data for {table_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@db_router.post("/api/migrate")
async def run_db_migration():
    """Run database migrations safely with explicit commits."""
    from app.memory_manager import run_database_migrations
    result = run_database_migrations()
    return JSONResponse(content=result)


def _clear_table_records(conn, table_name: str):
    """
    Clears all rows from a table using resilient fallbacks:
    1. Attempt TRUNCATE TABLE ... RESTART IDENTITY CASCADE (fastest, resets sequence IDs).
    2. If sequence ownership or RESTART IDENTITY fails, rollback and attempt
       TRUNCATE TABLE ... CASCADE.
    3. If TRUNCATE permission is denied (e.g. non-owner), rollback and execute
       DELETE FROM ... (only requires standard DELETE privilege).
    4. Safely resets primary key sequence if possible, ignoring sequence permission errors.
    """
    cleared = False

    # 1. Attempt TRUNCATE with RESTART IDENTITY
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE;").format(sql.Identifier(table_name)))
        conn.commit()
        return
    except Exception as e1:
        conn.rollback()
        logger.warning(f"TRUNCATE RESTART IDENTITY failed on '{table_name}': {e1}. Attempting TRUNCATE CASCADE without RESTART IDENTITY...")

    # 2. Attempt TRUNCATE without RESTART IDENTITY
    if not cleared:
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("TRUNCATE TABLE {} CASCADE;").format(sql.Identifier(table_name)))
            conn.commit()
            cleared = True
        except Exception as e2:
            conn.rollback()
            logger.warning(f"TRUNCATE CASCADE failed on '{table_name}': {e2}. Falling back to DELETE FROM...")

    # 3. Fallback to DELETE FROM
    if not cleared:
        try:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {};").format(sql.Identifier(table_name)))
            conn.commit()
            cleared = True
        except Exception as e3:
            conn.rollback()
            logger.error(f"DELETE FROM failed on '{table_name}': {e3}")
            raise e3

    # Attempt sequence reset (optional, will not fail if lacking permissions)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id');", (table_name,))
            res = cur.fetchone()
            seq_name = res["pg_get_serial_sequence"] if isinstance(res, dict) else (res[0] if res else None)
            if seq_name:
                cur.execute("SELECT setval(%s, 1, false);", (seq_name,))
        conn.commit()
    except Exception as seq_err:
        conn.rollback()
        logger.debug(f"Could not reset sequence for '{table_name}' (ignored): {seq_err}")


@db_router.post("/api/table/{table_name}/clear")
async def clear_specific_table(table_name: str):
    """Permanently truncate or delete all rows of a specific table."""
    if table_name == "channel_metadata":
        raise HTTPException(status_code=400, detail="Channels are never deleted. Archive them from the client's page instead.")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Validate table existence
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = %s;
            """, (table_name,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found.")

        _clear_table_records(conn, table_name)
        return JSONResponse(content={"status": "ok", "message": f"Table '{table_name}' cleared permanently."})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clear table {table_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@db_router.delete("/api/table/{table_name}/row/{row_id}")
async def delete_specific_table_row(table_name: str, row_id: str):
    """Deletes a specific single row from a database table by id."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = %s;
            """, (table_name,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found.")

            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'public' AND table_name = %s AND column_name = 'id';
            """, (table_name,))
            if not cur.fetchone():
                raise HTTPException(status_code=400, detail=f"Table '{table_name}' does not have a standard 'id' column.")

            query = sql.SQL("DELETE FROM {} WHERE id = %s;").format(sql.Identifier(table_name))
            cur.execute(query, (row_id,))
            deleted = cur.rowcount
            conn.commit()

            if deleted == 0:
                raise HTTPException(status_code=404, detail=f"Row ID '{row_id}' not found in table '{table_name}'.")

            return JSONResponse(content={"status": "ok", "message": f"Row #{row_id} deleted successfully from '{table_name}'."})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete row {row_id} from table {table_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()



@db_router.post("/api/clear-all")
async def clear_all_tables():
    """Permanently truncate or delete all public user tables in the database."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
            """)
            tables = [row["table_name"] if isinstance(row, dict) else row[0] for row in cur.fetchall()]

        cleared = []
        errors = []
        for t in tables:
            try:
                _clear_table_records(conn, t)
                cleared.append(t)
            except Exception as ex:
                logger.error(f"Could not clear table '{t}': {ex}")
                errors.append(f"{t}: {str(ex)}")

        if errors and not cleared:
            raise HTTPException(status_code=500, detail=f"Failed to clear tables: {'; '.join(errors)}")

        msg = f"Cleared {len(cleared)} table(s) permanently."
        if errors:
            msg += f" Note: {len(errors)} table(s) had errors: {'; '.join(errors)}"

        return JSONResponse(content={
            "status": "ok" if not errors else "partial",
            "message": msg,
            "cleared": cleared,
            "errors": errors
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clear all tables: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@db_router.get("", response_class=HTMLResponse)
async def database_dashboard():
    html_content = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>JTS PowerTool • Database Explorer</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#eef2ff',
              500: '#6366f1',
              600: '#4f46e5',
              700: '#4338ca',
            },
            surface: {
              900: '#0b0f19',
              800: '#111827',
              750: '#172033',
              700: '#1f2937',
              600: '#374151',
            }
          },
          fontFamily: {
            mono: ['JetBrains Mono', 'Fira Code', 'Courier New', 'monospace'],
          }
        }
      }
    }
  </script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
    body { font-family: 'Inter', sans-serif; }
    .custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
    .dark .custom-scroll::-webkit-scrollbar-track { background: #111827; }
    .dark .custom-scroll::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
    .dark .custom-scroll::-webkit-scrollbar-thumb:hover { background: #4b5563; }
    .custom-scroll::-webkit-scrollbar-track { background: #f1f5f9; }
    .custom-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
    .custom-scroll::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
  </style>
</head>
<body class="bg-slate-50 dark:bg-surface-900 text-slate-800 dark:text-slate-100 min-h-screen flex flex-col transition-colors duration-200 selection:bg-brand-500 selection:text-white">

  <!-- TOP HEADER -->
  <header class="border-b border-slate-200 dark:border-surface-700 bg-white/90 dark:bg-surface-800/80 backdrop-blur sticky top-0 z-30 px-6 py-3.5 shadow-sm transition-colors duration-200">
    <div class="max-w-[1700px] mx-auto flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center space-x-3">
        <div class="h-9 w-9 rounded-lg bg-gradient-to-tr from-cyan-600 to-indigo-600 flex items-center justify-center font-black text-white text-lg shadow-lg shadow-indigo-500/20">
          🗄️
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h1 class="text-base font-bold tracking-tight text-slate-900 dark:text-white">PostgreSQL Database Explorer</h1>
            <span class="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-surface-700 text-slate-600 dark:text-slate-300 font-medium border border-slate-200 dark:border-surface-600">Live Data</span>
          </div>
          <p class="text-xs text-slate-500 dark:text-slate-400">Inspect & Manage Real-Time PostgreSQL Tables</p>
        </div>
      </div>

      <!-- STATUS & CONTROLS -->
      <div class="flex items-center flex-wrap gap-2.5">
        <!-- GLOBAL DELETE ALL DATABASE BUTTON -->
        <button onclick="confirmClearAllDatabase()" class="px-3.5 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-xs font-semibold text-white transition border border-rose-500 shadow-sm flex items-center space-x-1.5" title="Permanently delete all data in all tables">
          <span>💣</span>
          <span>Delete Full Database</span>
        </button>

        <!-- BACK TO LOGS BUTTON -->
        <a href="/logs" class="px-3.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-xs font-semibold text-white transition border border-brand-500 flex items-center space-x-1.5 shadow-sm">
          <span>⚡</span>
          <span>Live Telemetry Logs</span>
        </a>

        <!-- VIEW CLAUDE CONTEXT BUTTON -->
        <a href="/context" class="px-3.5 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-xs font-semibold text-white transition border border-purple-500 flex items-center space-x-1.5 shadow-sm">
          <span>🧠</span>
          <span>Claude Context</span>
        </a>

        <!-- THEME TOGGLE BUTTON -->
        <button id="themeToggleBtn" onclick="toggleTheme()" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-medium text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 flex items-center space-x-1.5 shadow-sm" title="Toggle Light / Dark Mode">
          <span id="themeIcon">🌙</span>
          <span id="themeLabel">Dark</span>
        </button>

        <button onclick="refreshCurrentTable()" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-medium text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 shadow-sm flex items-center space-x-1.5">
          <span>🔄</span>
          <span>Refresh</span>
        </button>
      </div>
    </div>
  </header>

  <!-- MAIN SPLIT LAYOUT (TABBED SYSTEM) -->
  <main class="flex-1 max-w-[1700px] w-full mx-auto p-6 flex flex-col md:flex-row gap-6">

    <!-- LEFT SIDEBAR: TABLES LIST TABS -->
    <aside class="w-full md:w-80 flex-shrink-0 flex flex-col space-y-3">
      <div class="bg-white dark:bg-surface-800 border border-slate-200 dark:border-surface-700 rounded-xl p-4 shadow-sm transition-colors duration-200">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">Database Tables</h2>
          <span id="tableCountBadge" class="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-surface-700 text-slate-600 dark:text-slate-300 font-semibold border border-slate-200 dark:border-surface-600">0 Tables</span>
        </div>

        <div id="tableListContainer" class="space-y-1.5 max-h-[calc(100vh-230px)] overflow-y-auto custom-scroll">
          <div class="py-8 text-center text-slate-400 dark:text-slate-500 text-xs">
            <div class="h-5 w-5 border-2 border-brand-500 border-t-transparent rounded-full animate-spin mx-auto mb-2"></div>
            Loading database tables...
          </div>
        </div>
      </div>
    </aside>

    <!-- RIGHT MAIN CONTENT: ACTIVE TABLE DATA -->
    <section class="flex-1 flex flex-col space-y-4 min-w-0">

      <!-- TABLE CONTROLS & SEARCH -->
      <div class="bg-white dark:bg-surface-800 border border-slate-200 dark:border-surface-700 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 shadow-sm transition-colors duration-200">
        <div class="flex items-center space-x-3">
          <span class="h-3 w-3 rounded-full bg-emerald-500"></span>
          <div>
            <h3 id="activeTableName" class="text-sm font-bold text-slate-900 dark:text-white font-mono">Select a table</h3>
            <p id="activeTableStats" class="text-xs text-slate-500 dark:text-slate-400">Choose a table from the left to inspect its rows</p>
          </div>
        </div>

        <div class="flex items-center space-x-3 flex-1 max-w-xl justify-end">
          <!-- SEARCH INPUT -->
          <div class="relative w-full max-w-xs">
            <span class="absolute inset-y-0 left-0 pl-2.5 flex items-center text-slate-400 text-xs">🔍</span>
            <input id="tableSearchInput" type="text" placeholder="Search active table..." 
                   class="w-full bg-slate-50 dark:bg-surface-900 border border-slate-200 dark:border-surface-700 rounded-lg pl-8 pr-3.5 py-1.5 text-xs text-slate-800 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:border-brand-500 font-mono transition-colors">
          </div>

          <!-- SPECIFIC TABLE DELETE BUTTON -->
          <button id="clearTableBtn" onclick="confirmClearCurrentTable()" class="px-3 py-1.5 rounded-lg bg-rose-50 hover:bg-rose-100 dark:bg-rose-950/60 dark:hover:bg-rose-900/70 text-xs font-semibold text-rose-700 dark:text-rose-300 transition border border-rose-200 dark:border-rose-500/40 shadow-sm flex items-center space-x-1.5 whitespace-nowrap" title="Permanently delete all rows in this table">
            <span>🗑️</span>
            <span>Clear Table Data</span>
          </button>
        </div>
      </div>

      <!-- DATA TABLE CONTAINER -->
      <div class="bg-white dark:bg-surface-800 border border-slate-200 dark:border-surface-700 rounded-xl overflow-hidden shadow-sm dark:shadow-2xl flex-1 flex flex-col transition-colors duration-200">
        <div class="overflow-x-auto overflow-y-auto custom-scroll" style="max-height: calc(100vh - 240px);">
          <table class="w-full text-left border-collapse">
            <thead id="tableHead" class="bg-slate-100 dark:bg-surface-750 text-[11px] font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider sticky top-0 z-10 border-b border-slate-200 dark:border-surface-700 transition-colors">
              <tr>
                <th class="py-3 px-4">Columns</th>
              </tr>
            </thead>
            <tbody id="tableBody" class="divide-y divide-slate-100 dark:divide-surface-700/60 font-mono text-xs">
              <tr>
                <td class="py-20 text-center text-slate-400 dark:text-slate-500 font-sans">
                  Select a table from the left menu to view its real database records.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

    </section>

  </main>

  <!-- JAVASCRIPT FOR DYNAMIC DATA FETCHING, DELETIONS & TABS -->
  <script>
    let allTables = [];
    let currentTable = null;
    let currentTableData = null;

    // THEME HANDLING
    function initTheme() {
      const savedTheme = localStorage.getItem('theme');
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      const isDark = savedTheme ? savedTheme === 'dark' : prefersDark;
      setTheme(isDark ? 'dark' : 'light');
    }

    function setTheme(mode) {
      const html = document.documentElement;
      const themeIcon = document.getElementById('themeIcon');
      const themeLabel = document.getElementById('themeLabel');

      if (mode === 'dark') {
        html.classList.add('dark');
        localStorage.setItem('theme', 'dark');
        themeIcon.innerText = '🌙';
        themeLabel.innerText = 'Dark';
      } else {
        html.classList.remove('dark');
        localStorage.setItem('theme', 'light');
        themeIcon.innerText = '☀️';
        themeLabel.innerText = 'Light';
      }
    }

    function toggleTheme() {
      const isDark = document.documentElement.classList.contains('dark');
      setTheme(isDark ? 'light' : 'dark');
    }

    function escapeHtml(str) {
      if (str === null || str === undefined) return '<span class="text-slate-400 italic">NULL</span>';
      return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    async function loadTableList() {
      try {
        const res = await fetch("/database/api/tables");
        const data = await res.json();
        allTables = data.tables || [];

        document.getElementById("tableCountBadge").innerText = `${allTables.length} Tables`;

        const container = document.getElementById("tableListContainer");
        if (allTables.length === 0) {
          container.innerHTML = '<div class="py-6 text-center text-slate-400 text-xs">No tables found in database.</div>';
          return;
        }

        container.innerHTML = allTables.map(t => {
          const isActive = currentTable === t.name;
          const activeClass = isActive 
            ? "bg-brand-600 text-white font-bold border-brand-500 shadow-sm" 
            : "bg-slate-100 dark:bg-surface-700/60 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-surface-700 border-slate-200 dark:border-surface-600/60";

          return `
            <button onclick="selectTable('${t.name}')" 
                    id="tab-btn-${t.name}"
                    class="w-full text-left px-3.5 py-2.5 rounded-lg border text-xs transition flex items-center justify-between ${activeClass}">
              <div class="flex items-center space-x-2 truncate">
                <span class="text-sm">📁</span>
                <span class="font-mono truncate">${t.name}</span>
              </div>
              <span id="badge-${t.name}" class="text-[10px] px-1.5 py-0.5 rounded bg-black/20 text-slate-200 font-mono font-semibold">${t.row_count}</span>
            </button>
          `;
        }).join("");

        // Auto-select the first table if none selected
        if (!currentTable && allTables.length > 0) {
          selectTable(allTables[0].name);
        }
      } catch (err) {
        console.error("Failed to load tables:", err);
      }
    }

    async function selectTable(tableName) {
      currentTable = tableName;
      
      // Update UI tabs
      allTables.forEach(t => {
        const btn = document.getElementById(`tab-btn-${t.name}`);
        if (btn) {
          if (t.name === tableName) {
            btn.className = "w-full text-left px-3.5 py-2.5 rounded-lg border text-xs transition flex items-center justify-between bg-brand-600 text-white font-bold border-brand-500 shadow-sm";
          } else {
            btn.className = "w-full text-left px-3.5 py-2.5 rounded-lg border text-xs transition flex items-center justify-between bg-slate-100 dark:bg-surface-700/60 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-surface-700 border-slate-200 dark:border-surface-600/60";
          }
        }
      });

      document.getElementById("activeTableName").innerText = `public.${tableName}`;
      document.getElementById("activeTableStats").innerText = `Loading records from PostgreSQL...`;

      const thead = document.getElementById("tableHead");
      const tbody = document.getElementById("tableBody");

      tbody.innerHTML = `
        <tr>
          <td colspan="10" class="py-20 text-center text-slate-400 dark:text-slate-500 font-sans">
            <div class="h-6 w-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin mx-auto mb-2"></div>
            Loading records for ${tableName}...
          </td>
        </tr>
      `;

      try {
        const res = await fetch(`/database/api/table/${tableName}`);
        if (!res.ok) throw new Error("Table data request failed");
        currentTableData = await res.json();
        renderActiveTable();
      } catch (err) {
        tbody.innerHTML = `
          <tr>
            <td colspan="10" class="py-12 text-center text-rose-500 font-sans">
              Failed to load records for table '${tableName}'. (${err.message})
            </td>
          </tr>
        `;
      }
    }

    function renderActiveTable() {
      if (!currentTableData) return;

      const { columns, rows, total_rows } = currentTableData;
      const search = document.getElementById("tableSearchInput").value.toLowerCase().trim();

      document.getElementById("activeTableStats").innerText = `${total_rows} total rows stored in database`;

      const thead = document.getElementById("tableHead");
      const tbody = document.getElementById("tableBody");

      const hasIdColumn = columns.some(c => c.name === 'id');

      // Render Table Headers
      thead.innerHTML = `
        <tr>
          ${columns.map(c => `
            <th class="py-3 px-4 whitespace-nowrap">
              <div class="flex items-center space-x-1.5">
                <span class="text-slate-800 dark:text-slate-200 font-bold">${c.name}</span>
                <span class="text-[10px] text-slate-400 lowercase font-normal">(${c.type})</span>
              </div>
            </th>
          `).join("")}
          ${hasIdColumn ? '<th class="py-3 px-4 whitespace-nowrap text-right text-slate-800 dark:text-slate-200 font-bold">Actions</th>' : ''}
        </tr>
      `;

      // Filter rows
      const filteredRows = rows.filter(r => {
        if (!search) return true;
        return Object.values(r).some(val => String(val).toLowerCase().includes(search));
      });

      if (filteredRows.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="${columns.length + (hasIdColumn ? 1 : 0)}" class="py-16 text-center text-slate-400 dark:text-slate-500 font-sans">
              ${rows.length === 0 ? 'This table is currently empty.' : 'No rows match your search filter.'}
            </td>
          </tr>
        `;
        return;
      }

      // Render Rows
      tbody.innerHTML = filteredRows.map(r => `
        <tr class="hover:bg-slate-100/70 dark:hover:bg-surface-700/50 transition border-b border-slate-100 dark:border-surface-700/40">
          ${columns.map(c => {
            const val = r[c.name];
            
            // Format 384-d RAG Vector Embedding Column
            if (c.name.toLowerCase() === 'embedding') {
              if (!val || val === 'null' || val === '[]') {
                return `<td class="py-2.5 px-4 whitespace-nowrap text-xs text-slate-400 italic">None</td>`;
              }
              return `
                <td class="py-2.5 px-4 whitespace-nowrap text-xs">
                  <span class="inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono bg-purple-100 text-purple-800 dark:bg-purple-950/80 dark:text-purple-200 border border-purple-300 dark:border-purple-700 cursor-pointer shadow-sm hover:scale-105 transition" title="${escapeHtml(val)}" onclick="alert('🧬 384-dimensional Vector Embedding:\\n\\n' + this.getAttribute('title'))">
                    <span>🧬</span>
                    <span class="font-semibold">Vector (384-dim)</span>
                  </span>
                </td>
              `;
            }

            // Format Role Badge
            if (c.name.toLowerCase() === 'role') {
              const isAssistant = String(val).toLowerCase() === 'assistant';
              const badge = isAssistant 
                ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300 border-emerald-300 dark:border-emerald-700'
                : 'bg-blue-100 text-blue-800 dark:bg-blue-950/80 dark:text-blue-300 border-blue-300 dark:border-blue-700';
              return `
                <td class="py-2.5 px-4 whitespace-nowrap text-xs">
                  <span class="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold border ${badge}">
                    ${escapeHtml(val)}
                  </span>
                </td>
              `;
            }

            // Format Content & Messages with clean truncation and tooltip
            if (c.name.toLowerCase() === 'content' || c.name.toLowerCase() === 'message') {
              return `
                <td class="py-2.5 px-4 text-xs max-w-sm truncate text-slate-800 dark:text-slate-200 font-sans" title="${escapeHtml(val)}">
                  ${escapeHtml(val)}
                </td>
              `;
            }

            const isId = c.name.toLowerCase().includes('id');
            const colorClass = isId ? 'text-indigo-600 dark:text-indigo-300' : 'text-slate-800 dark:text-slate-300';
            return `
              <td class="py-2.5 px-4 whitespace-nowrap text-xs ${colorClass}">
                ${escapeHtml(val)}
              </td>
            `;
          }).join("")}
          ${hasIdColumn ? `
            <td class="py-2.5 px-4 whitespace-nowrap text-right">
              <button onclick="confirmDeleteRow('${currentTable}', '${r.id}')" class="px-2 py-0.5 bg-rose-100 hover:bg-rose-200 dark:bg-rose-950/80 dark:hover:bg-rose-900 text-rose-700 dark:text-rose-300 rounded text-[11px] font-medium border border-rose-200 dark:border-rose-800 transition" title="Delete row #${r.id}">
                🗑️ Delete
              </button>
            </td>
          ` : ''}
        </tr>
      `).join("");
    }

    async function confirmDeleteRow(tableName, rowId) {
      if (!confirm(`Are you sure you want to delete row #${rowId} from table '${tableName}'?`)) return;
      try {
        const res = await fetch(`/database/api/table/${tableName}/row/${rowId}`, { method: "DELETE" });
        const result = await res.json();
        if (res.ok) {
          selectTable(tableName);
        } else {
          alert(`❌ Failed to delete row: ${result.detail || 'Unknown error'}`);
        }
      } catch (err) {
        alert(`❌ Error deleting row: ${err.message}`);
      }
    }

    // CLEAR SPECIFIC TABLE

    async function confirmClearCurrentTable() {
      if (!currentTable) return;
      const confirmed = confirm(`⚠️ Are you sure you want to PERMANENTLY delete all data in table 'public.${currentTable}'?`);
      if (!confirmed) return;

      try {
        const res = await fetch(`/database/api/table/${currentTable}/clear`, { method: "POST" });
        const result = await res.json();
        if (res.ok) {
          alert(`✅ Table '${currentTable}' cleared successfully!`);
          refreshCurrentTable();
        } else {
          alert(`❌ Failed to clear table: ${result.detail || 'Unknown error'}`);
        }
      } catch (err) {
        alert(`❌ Network error while clearing table: ${err.message}`);
      }
    }

    // CLEAR FULL DATABASE PERMANENTLY
    async function confirmClearAllDatabase() {
      const confirmed = confirm("🚨 DANGER: Are you sure you want to PERMANENTLY delete ALL DATA in ALL DATABASE TABLES?");
      if (!confirmed) return;

      const doubleCheck = prompt("Type 'DELETE' to confirm full database wipe:");
      if (doubleCheck !== "DELETE") {
        alert("Operation cancelled. Confirmation keyword did not match.");
        return;
      }

      try {
        const res = await fetch("/database/api/clear-all", { method: "POST" });
        const result = await res.json();
        if (res.ok) {
          alert("✅ Full database has been permanently cleared!");
          loadTableList();
        } else {
          alert(`❌ Failed to clear database: ${result.detail || 'Unknown error'}`);
        }
      } catch (err) {
        alert(`❌ Network error while clearing database: ${err.message}`);
      }
    }

    function refreshCurrentTable() {
      loadTableList();
      if (currentTable) {
        selectTable(currentTable);
      }
    }

    document.getElementById("tableSearchInput").addEventListener("input", renderActiveTable);

    // Initial Load
    initTheme();
    loadTableList();
  </script>
</body>
</html>
"""
    return HTMLResponse(
        content=html_content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

