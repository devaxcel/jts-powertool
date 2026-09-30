import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

def record_processed_activity(tenant_id: str, activity_id: str) -> bool:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Record in processed_activities
            cur.execute("""
                INSERT INTO processed_activities (tenant_id, activity_id)
                VALUES (%s, %s)
                ON CONFLICT (tenant_id, activity_id) DO NOTHING
                RETURNING id;
            """, (tenant_id, activity_id))
            row = cur.fetchone()

            # 2. Also record in processed_events (matching exact spec)
            try:
                cur.execute("""
                    INSERT INTO processed_events (team_id, event_id)
                    VALUES (%s, %s)
                    ON CONFLICT (team_id, event_id) DO NOTHING;
                """, (tenant_id, activity_id))
            except Exception:
                pass

            conn.commit()
            return row is not None
    finally:
        conn.close()

def get_or_create_session(
    teams_tenant_id: str,
    teams_conversation_id: str,
    teams_thread_id: str = "",
    teams_user_id: str = None,
    claude_session_id: str = None
) -> str:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # First check if active session exists
            cur.execute(
                """
                SELECT claude_session_id 
                FROM conversation_sessions 
                WHERE teams_tenant_id = %s 
                  AND teams_conversation_id = %s 
                  AND teams_thread_id = %s 
                  AND status = 'active'
                LIMIT 1;
                """,
                (teams_tenant_id, teams_conversation_id, teams_thread_id)
            )
            row = cur.fetchone()
            if row:
                return row[0]

            # Otherwise create a new session
            new_session_id = claude_session_id or f"session_{teams_conversation_id}"
            cur.execute(
                """
                INSERT INTO conversation_sessions (
                    teams_tenant_id, teams_conversation_id, teams_thread_id, teams_user_id, claude_session_id
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (teams_tenant_id, teams_conversation_id, teams_thread_id)
                DO UPDATE SET claude_session_id = EXCLUDED.claude_session_id, updated_at = now()
                RETURNING claude_session_id;
                """,
                (teams_tenant_id, teams_conversation_id, teams_thread_id, teams_user_id, new_session_id)
            )
            res = cur.fetchone()
            conn.commit()
            return res[0]
    finally:
        conn.close()


def enqueue_job(
    event_id: str,
    channel_id: str,
    user_id: str,
    payload: dict,
    team_id: str = "slack-workspace",
    thread_ts: str = "",
) -> int | None:
    """Inserts a new job into the job_queue if it hasn't been queued yet."""
    import json
    from psycopg2.extras import Json
    from app.services.channel_secrets_service import canonical_channel_id

    ws_id = team_id or (payload.get("workspace_id") if isinstance(payload, dict) else "")
    ws_name = payload.get("workspace_name") if isinstance(payload, dict) else ""
    ch_name = payload.get("channel_name") if isinstance(payload, dict) else ""

    channel_id = canonical_channel_id(channel_id, workspace_id=ws_id, workspace_name=ws_name, channel_name=ch_name)
    if isinstance(payload, dict):
        payload["channel_id"] = channel_id
        if ws_id and not payload.get("workspace_id"):
            payload["workspace_id"] = ws_id
        if ws_name and not payload.get("workspace_name"):
            payload["workspace_name"] = ws_name
        if ch_name and not payload.get("channel_name"):
            payload["channel_name"] = ch_name

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_queue (
                    event_id, team_id, channel_id, thread_ts, user_id, payload, status, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', now(), now())
                ON CONFLICT (event_id) DO NOTHING
                RETURNING id;
                """,
                (event_id, ws_id or team_id, channel_id, thread_ts or "", user_id, Json(payload)),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                return row["id"] if isinstance(row, dict) else row[0]
            return None
    finally:
        conn.close()


def claim_next_job() -> dict | None:
    """Atomically claims the next pending job using SKIP LOCKED."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_queue
                SET status = 'processing',
                    locked_at = now(),
                    updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM job_queue
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, event_id, team_id, channel_id, thread_ts, user_id, payload, status, created_at;
                """
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                return dict(row)
            return None
    finally:
        conn.close()


def mark_job_completed(job_id: int) -> bool:
    """Marks a job as completed."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_queue
                SET status = 'completed',
                    updated_at = now()
                WHERE id = %s;
                """,
                (job_id,),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def mark_job_failed(job_id: int, error_message: str) -> bool:
    """Marks a job as failed with the corresponding error message."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_queue
                SET status = 'failed',
                    error_message = %s,
                    updated_at = now()
                WHERE id = %s;
                """,
                (error_message, job_id),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


_context_snapshots_table_initialized = False


def init_context_snapshots_table():
    """Ensure claude_context_snapshots table exists in PostgreSQL."""
    global _context_snapshots_table_initialized
    if _context_snapshots_table_initialized:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS claude_context_snapshots (
                    id SERIAL PRIMARY KEY,
                    channel_id VARCHAR(255) NOT NULL,
                    thread_ts VARCHAR(255) DEFAULT '',
                    user_id VARCHAR(255),
                    user_name VARCHAR(255),
                    session_id VARCHAR(255),
                    model VARCHAR(255),
                    prompt_text TEXT,
                    system_prompt TEXT,
                    messages_sent JSONB NOT NULL,
                    files_included JSONB DEFAULT '[]'::jsonb,
                    context_count INT DEFAULT 0,
                    workspace_id VARCHAR(64),
                    workspace_name VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE claude_context_snapshots ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64);
                ALTER TABLE claude_context_snapshots ADD COLUMN IF NOT EXISTS workspace_name VARCHAR(255);
                CREATE INDEX IF NOT EXISTS idx_context_snapshots_channel_thread ON claude_context_snapshots (channel_id, thread_ts);
                CREATE INDEX IF NOT EXISTS idx_context_snapshots_created_at ON claude_context_snapshots (created_at DESC);
            """)
            conn.commit()
            _context_snapshots_table_initialized = True
    except Exception:
        pass
    finally:
        conn.close()


def save_context_snapshot(
    *,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    user_name: str,
    session_id: str,
    model: str,
    prompt_text: str,
    system_prompt: str,
    messages_sent: list,
    files_included: list = None,
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
) -> int | None:
    """Permanently store a snapshot of messages passed to Claude."""
    from psycopg2.extras import Json
    from app.services.channel_secrets_service import canonical_channel_id
    channel_id = canonical_channel_id(channel_id, workspace_id=workspace_id, workspace_name=workspace_name)

    conn = None
    try:
        init_context_snapshots_table()
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO claude_context_snapshots (
                    channel_id, thread_ts, user_id, user_name, session_id, model,
                    prompt_text, system_prompt, messages_sent, files_included,
                    context_count, workspace_id, workspace_name, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                RETURNING id;
            """, (
                channel_id,
                thread_ts or "",
                user_id,
                user_name,
                session_id or "",
                model,
                prompt_text,
                system_prompt,
                Json(messages_sent),
                Json(files_included or []),
                len(messages_sent),
                workspace_id,
                workspace_name,
            ))
            row = cur.fetchone()
            conn.commit()
            if row:
                return row["id"] if isinstance(row, dict) else row[0]
            return None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def get_latest_context_snapshot() -> dict | None:
    """Retrieve the most recent Claude context snapshot."""
    conn = None
    try:
        init_context_snapshots_table()
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, channel_id, thread_ts, user_id, user_name, session_id,
                       model, prompt_text, system_prompt, messages_sent, files_included,
                       context_count, created_at
                FROM claude_context_snapshots
                ORDER BY id DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def get_context_snapshot_history(limit: int = 20) -> list[dict]:
    """Retrieve history list of recent Claude context snapshots."""
    conn = None
    try:
        init_context_snapshots_table()
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, channel_id, thread_ts, user_id, user_name, session_id,
                       model, prompt_text, system_prompt, messages_sent, files_included, context_count, created_at
                FROM claude_context_snapshots
                ORDER BY id DESC
                LIMIT %s;
            """, (limit,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def get_context_snapshot_by_id(snapshot_id: int) -> dict | None:
    """Retrieve a specific Claude context snapshot by ID."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, channel_id, thread_ts, user_id, user_name, session_id,
                       model, prompt_text, system_prompt, messages_sent, files_included,
                       context_count, created_at
                FROM claude_context_snapshots
                WHERE id = %s;
            """, (snapshot_id,))
            row = cur.fetchone()
            if row:
                return dict(row)
            return None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def init_pending_approvals_table():
    """Ensure pending_approvals table exists with all required columns."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    id SERIAL PRIMARY KEY,
                    approval_id VARCHAR(64) UNIQUE NOT NULL,
                    team_id VARCHAR(255) DEFAULT 'slack-workspace',
                    channel_id VARCHAR(255) NOT NULL,
                    channel_name VARCHAR(255),
                    thread_ts VARCHAR(255) DEFAULT '',
                    message_ts VARCHAR(255) DEFAULT '',
                    user_id VARCHAR(255) NOT NULL,
                    user_name VARCHAR(255),
                    tool_name VARCHAR(100) NOT NULL,
                    tool_arguments JSONB NOT NULL,
                    status VARCHAR(50) DEFAULT 'pending',
                    approved_by VARCHAR(255),
                    execution_result TEXT,
                    expires_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours'),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours');
                ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS user_name VARCHAR(255);
                ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS channel_name VARCHAR(255);
                ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64);
                ALTER TABLE pending_approvals ADD COLUMN IF NOT EXISTS workspace_name VARCHAR(255);
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON pending_approvals (status, created_at);
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_lookup ON pending_approvals (approval_id);
                CREATE INDEX IF NOT EXISTS idx_pending_approvals_expires ON pending_approvals (status, expires_at);
            """)
            conn.commit()
    except Exception as e:
        logger.warning(f"init_pending_approvals_table notice: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def create_pending_approval(
    approval_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    tool_name: str,
    tool_arguments: dict,
    team_id: str = "slack-workspace",
    message_ts: str = "",
    expires_in_hours: int = 24,
    user_name: str = "",
    channel_name: str = "",
) -> int | None:
    """Creates a new pending approval record in PostgreSQL with expiration timestamp."""
    conn = None
    try:
        init_pending_approvals_table()
        conn = get_db_connection()
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    INSERT INTO pending_approvals (
                        approval_id, team_id, channel_id, channel_name, thread_ts, message_ts,
                        user_id, user_name, tool_name, tool_arguments, status,
                        expires_at, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, 'pending',
                        CURRENT_TIMESTAMP + (%s || ' hours')::interval,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (approval_id) DO UPDATE SET
                        tool_arguments = EXCLUDED.tool_arguments,
                        user_name = COALESCE(NULLIF(EXCLUDED.user_name, ''), pending_approvals.user_name),
                        channel_name = COALESCE(NULLIF(EXCLUDED.channel_name, ''), pending_approvals.channel_name),
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id;
                """, (
                    approval_id, team_id, channel_id, channel_name, thread_ts, message_ts,
                    user_id, user_name, tool_name, json.dumps(tool_arguments),
                    str(expires_in_hours),
                ))
            except Exception:
                conn.rollback()
                cur.execute("""
                    INSERT INTO pending_approvals (
                        approval_id, team_id, channel_id, thread_ts, message_ts,
                        user_id, tool_name, tool_arguments, status,
                        expires_at, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, 'pending',
                        CURRENT_TIMESTAMP + (%s || ' hours')::interval,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (approval_id) DO UPDATE SET
                        tool_arguments = EXCLUDED.tool_arguments,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id;
                """, (
                    approval_id, team_id, channel_id, thread_ts, message_ts,
                    user_id, tool_name, json.dumps(tool_arguments),
                    str(expires_in_hours),
                ))
            row = cur.fetchone()
            conn.commit()
            if row:
                return row["id"] if isinstance(row, dict) else row[0]
            return None
    except Exception as e:
        logger.error(f"Error creating pending approval: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def get_pending_approval(approval_id: str) -> dict | None:
    """Fetches a pending approval record by approval_id."""
    conn = None
    try:
        init_pending_approvals_table()
        conn = get_db_connection()
        with conn.cursor() as cur:
            row = None
            try:
                cur.execute("""
                    SELECT p.id, p.approval_id, p.team_id, p.channel_id, p.thread_ts, p.message_ts,
                           p.user_id, p.tool_name, p.tool_arguments, p.status, p.approved_by,
                           p.execution_result, p.expires_at, p.created_at, p.updated_at,
                           COALESCE(NULLIF(p.channel_name, ''), cm.channel_name, p.channel_id) AS channel_name,
                           COALESCE(NULLIF(p.user_name, ''), ccs.user_name, du.display_name, du.username, p.user_id) AS user_name,
                           COALESCE(appr_u.display_name, appr_u.username, appr_ccs.user_name, p.approved_by) AS approved_by_name
                    FROM pending_approvals p
                    LEFT JOIN channel_metadata cm ON p.channel_id = cm.channel_id
                    LEFT JOIN LATERAL (
                        SELECT user_name FROM claude_context_snapshots
                        WHERE user_id = p.user_id AND user_name IS NOT NULL AND user_name != ''
                        ORDER BY id DESC LIMIT 1
                    ) ccs ON true
                    LEFT JOIN dashboard_users du ON p.user_id = du.username
                    LEFT JOIN LATERAL (
                        SELECT user_name FROM claude_context_snapshots
                        WHERE user_id = p.approved_by AND user_name IS NOT NULL AND user_name != ''
                        ORDER BY id DESC LIMIT 1
                    ) appr_ccs ON true
                    LEFT JOIN dashboard_users appr_u ON p.approved_by = appr_u.username
                    WHERE p.approval_id = %s;
                """, (approval_id,))
                row = cur.fetchone()
            except Exception as q_err:
                logger.warning(f"Enriched get_pending_approval query failed, falling back: {q_err}")
                conn.rollback()
                cur.execute("""
                    SELECT p.id, p.approval_id, p.team_id, p.channel_id, p.thread_ts, p.message_ts,
                           p.user_id, p.tool_name, p.tool_arguments, p.status, p.approved_by,
                           p.execution_result, p.expires_at, p.created_at, p.updated_at,
                           cm.channel_name
                    FROM pending_approvals p
                    LEFT JOIN channel_metadata cm ON p.channel_id = cm.channel_id
                    WHERE p.approval_id = %s;
                """, (approval_id,))
                row = cur.fetchone()

            if row:
                rec = dict(row)
                if not rec.get("channel_name"):
                    rec["channel_name"] = rec.get("channel_id") or "general"
                if not rec.get("user_name"):
                    rec["user_name"] = rec.get("user_id") or "web-admin"
                if not rec.get("approved_by_name"):
                    rec["approved_by_name"] = rec.get("approved_by") or rec.get("user_name")
                if isinstance(rec.get("tool_arguments"), str):
                    try:
                        rec["tool_arguments"] = json.loads(rec["tool_arguments"])
                    except Exception:
                        pass
                return rec
            return None
    except Exception as e:
        logger.error(f"Error fetching approval {approval_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


def claim_approval_for_execution(approval_id: str, user_id: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Atomically claims an approval for execution.
    Transitions status: 'pending' -> 'applying'.
    Guarantees that only one process or request can ever claim the approval (no double execution).
    Checks expiration: if expired, transitions status: 'pending' -> 'expired'.
    Returns (result_status, record_dict):
      - ("claimed", record) -> Successfully claimed, proceed with execution.
      - ("expired", record) -> Request expired.
      - ("already_applying", record) -> Already executing.
      - ("already_applied", record) -> Already executed.
      - ("already_rejected", record) -> Already rejected.
      - ("already_failed", record) -> Already failed.
      - ("not_found", None) -> Approval ID not found.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. First, check if expired while still pending
            cur.execute("""
                UPDATE pending_approvals
                SET status = 'expired',
                    updated_at = CURRENT_TIMESTAMP
                WHERE approval_id = %s
                  AND status = 'pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < CURRENT_TIMESTAMP
                RETURNING id, approval_id, team_id, channel_id, thread_ts, message_ts,
                          user_id, tool_name, tool_arguments, status, approved_by,
                          execution_result, expires_at, created_at, updated_at;
            """, (approval_id,))
            expired_row = cur.fetchone()
            if expired_row:
                conn.commit()
                rec = dict(expired_row)
                if isinstance(rec.get("tool_arguments"), str):
                    try:
                        rec["tool_arguments"] = json.loads(rec["tool_arguments"])
                    except Exception:
                        pass
                return ("expired", rec)

            # 2. Atomic claim: transition from pending -> applying
            cur.execute("""
                UPDATE pending_approvals
                SET status = 'applying',
                    approved_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE approval_id = %s
                  AND status = 'pending'
                  AND (expires_at IS NULL OR expires_at >= CURRENT_TIMESTAMP)
                RETURNING id, approval_id, team_id, channel_id, thread_ts, message_ts,
                          user_id, tool_name, tool_arguments, status, approved_by,
                          execution_result, expires_at, created_at, updated_at;
            """, (user_id, approval_id))
            row = cur.fetchone()
            if row:
                conn.commit()
                rec = dict(row)
                if isinstance(rec.get("tool_arguments"), str):
                    try:
                        rec["tool_arguments"] = json.loads(rec["tool_arguments"])
                    except Exception:
                        pass
                return ("claimed", rec)

            # 3. If no row was updated, inspect current state to return precise reason
            cur.execute("""
                SELECT id, approval_id, team_id, channel_id, thread_ts, message_ts,
                       user_id, tool_name, tool_arguments, status, approved_by,
                       execution_result, expires_at, created_at, updated_at
                FROM pending_approvals
                WHERE approval_id = %s;
            """, (approval_id,))
            current = cur.fetchone()
            if not current:
                return ("not_found", None)

            rec = dict(current)
            if isinstance(rec.get("tool_arguments"), str):
                try:
                    rec["tool_arguments"] = json.loads(rec["tool_arguments"])
                except Exception:
                    pass

            current_status = rec.get("status")
            if current_status == "expired":
                return ("expired", rec)
            if current_status == "applying":
                return ("already_applying", rec)
            if current_status in ("approved", "applied"):
                return ("already_applied", rec)
            if current_status == "rejected":
                return ("already_rejected", rec)
            if current_status == "failed":
                return ("already_failed", rec)
            return (f"already_{current_status}", rec)

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error claiming approval {approval_id}: {e}")
        return ("error", None)
    finally:
        if conn:
            conn.close()


def finalize_approval_execution(
    approval_id: str,
    success: bool,
    execution_result: str = "",
) -> bool:
    """
    Finalizes an approval execution from 'applying' to 'applied' or 'failed'.
    """
    final_status = "applied" if success else "failed"
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pending_approvals
                SET status = %s,
                    execution_result = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE approval_id = %s AND status = 'applying'
                RETURNING id;
            """, (final_status, execution_result, approval_id))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error finalizing approval {approval_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()


def reject_approval(approval_id: str, user_id: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Atomically transitions an approval from 'pending' to 'rejected'.
    Checks expiration: if expired, transitions to 'expired'.
    Returns (result_status, record_dict):
      - ("rejected", record)
      - ("expired", record)
      - ("already_processed", record)
      - ("not_found", None)
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. Check if expired
            cur.execute("""
                UPDATE pending_approvals
                SET status = 'expired',
                    updated_at = CURRENT_TIMESTAMP
                WHERE approval_id = %s
                  AND status = 'pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < CURRENT_TIMESTAMP
                RETURNING id, approval_id, team_id, channel_id, thread_ts, message_ts,
                          user_id, tool_name, tool_arguments, status, approved_by,
                          execution_result, expires_at, created_at, updated_at;
            """, (approval_id,))
            expired_row = cur.fetchone()
            if expired_row:
                conn.commit()
                rec = dict(expired_row)
                return ("expired", rec)

            # 2. Atomic transition: pending -> rejected
            cur.execute("""
                UPDATE pending_approvals
                SET status = 'rejected',
                    approved_by = %s,
                    execution_result = 'Rejected by user',
                    updated_at = CURRENT_TIMESTAMP
                WHERE approval_id = %s
                  AND status = 'pending'
                  AND (expires_at IS NULL OR expires_at >= CURRENT_TIMESTAMP)
                RETURNING id, approval_id, team_id, channel_id, thread_ts, message_ts,
                          user_id, tool_name, tool_arguments, status, approved_by,
                          execution_result, expires_at, created_at, updated_at;
            """, (user_id, approval_id))
            row = cur.fetchone()
            if row:
                conn.commit()
                rec = dict(row)
                if isinstance(rec.get("tool_arguments"), str):
                    try:
                        rec["tool_arguments"] = json.loads(rec["tool_arguments"])
                    except Exception:
                        pass
                return ("rejected", rec)

            # 3. If no row updated, inspect current state
            cur.execute("""
                SELECT id, approval_id, team_id, channel_id, thread_ts, message_ts,
                       user_id, tool_name, tool_arguments, status, approved_by,
                       execution_result, expires_at, created_at, updated_at
                FROM pending_approvals
                WHERE approval_id = %s;
            """, (approval_id,))
            current = cur.fetchone()
            if not current:
                return ("not_found", None)

            rec = dict(current)
            if isinstance(rec.get("tool_arguments"), str):
                try:
                    rec["tool_arguments"] = json.loads(rec["tool_arguments"])
                except Exception:
                    pass

            if rec.get("status") == "expired":
                return ("expired", rec)
            return ("already_processed", rec)

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error rejecting approval {approval_id}: {e}")
        return ("error", None)
    finally:
        if conn:
            conn.close()


def update_pending_approval_status(
    approval_id: str,
    status: str,
    approved_by: str = "",
    execution_result: str = "",
) -> bool:
    """Updates the status and resolution of an approval record (backward compatibility)."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pending_approvals
                SET status = %s,
                    approved_by = %s,
                    execution_result = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE approval_id = %s;
            """, (status, approved_by, execution_result, approval_id))
            conn.commit()
            return True
    except Exception:
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def update_pending_approval_message_ts(approval_id: str, message_ts: str) -> bool:
    """Updates the message_ts of the Slack approval card."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pending_approvals
                SET message_ts = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE approval_id = %s;
            """, (message_ts, approval_id))
            conn.commit()
            return True
    except Exception:
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def expire_stale_approvals() -> int:
    """Marks pending approvals whose expires_at has passed as 'expired'. Returns the number updated."""
    conn = None
    try:
        init_pending_approvals_table()
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pending_approvals
                SET status = 'expired', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'pending'
                  AND expires_at IS NOT NULL
                  AND expires_at < CURRENT_TIMESTAMP;
            """)
            count = cur.rowcount or 0
        conn.commit()
        return count
    except Exception as e:
        logger.error(f"Error expiring stale approvals: {e}")
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


def get_all_approvals(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieves a list of approvals ordered by created_at descending, enriched with channel & user metadata."""
    conn = None
    try:
        init_pending_approvals_table()
        conn = get_db_connection()
        with conn.cursor() as cur:
            base_query = """
                SELECT p.id, p.approval_id, p.team_id, p.channel_id, p.thread_ts, p.message_ts,
                       p.user_id, p.tool_name, p.tool_arguments, p.status, p.approved_by,
                       p.execution_result, p.expires_at, p.created_at, p.updated_at,
                       COALESCE(NULLIF(p.channel_name, ''), cm.channel_name, p.channel_id) AS channel_name,
                       COALESCE(NULLIF(p.user_name, ''), ccs.user_name, du.display_name, du.username, p.user_id) AS user_name,
                       COALESCE(appr_u.display_name, appr_u.username, appr_ccs.user_name, p.approved_by) AS approved_by_name
                FROM pending_approvals p
                LEFT JOIN channel_metadata cm ON p.channel_id = cm.channel_id
                LEFT JOIN LATERAL (
                    SELECT user_name FROM claude_context_snapshots
                    WHERE user_id = p.user_id AND user_name IS NOT NULL AND user_name != ''
                    ORDER BY id DESC LIMIT 1
                ) ccs ON true
                LEFT JOIN dashboard_users du ON p.user_id = du.username
                LEFT JOIN LATERAL (
                    SELECT user_name FROM claude_context_snapshots
                    WHERE user_id = p.approved_by AND user_name IS NOT NULL AND user_name != ''
                    ORDER BY id DESC LIMIT 1
                ) appr_ccs ON true
                LEFT JOIN dashboard_users appr_u ON p.approved_by = appr_u.username
            """
            rows = []
            try:
                if status:
                    cur.execute(base_query + " WHERE p.status = %s ORDER BY p.created_at DESC LIMIT %s;", (status, limit))
                else:
                    cur.execute(base_query + " ORDER BY p.created_at DESC LIMIT %s;", (limit,))
                rows = cur.fetchall()
            except Exception as q_err:
                logger.warning(f"Enriched get_all_approvals query failed, falling back: {q_err}")
                conn.rollback()
                fallback_query = """
                    SELECT p.id, p.approval_id, p.team_id, p.channel_id, p.thread_ts, p.message_ts,
                           p.user_id, p.tool_name, p.tool_arguments, p.status, p.approved_by,
                           p.execution_result, p.expires_at, p.created_at, p.updated_at,
                           cm.channel_name
                    FROM pending_approvals p
                    LEFT JOIN channel_metadata cm ON p.channel_id = cm.channel_id
                """
                if status:
                    cur.execute(fallback_query + " WHERE p.status = %s ORDER BY p.created_at DESC LIMIT %s;", (status, limit))
                else:
                    cur.execute(fallback_query + " ORDER BY p.created_at DESC LIMIT %s;", (limit,))
                rows = cur.fetchall()

            results = []
            for r in rows:
                rec = dict(r)
                if not rec.get("channel_name"):
                    rec["channel_name"] = rec.get("channel_id") or "general"
                if not rec.get("user_name"):
                    rec["user_name"] = rec.get("user_id") or "web-admin"
                if not rec.get("approved_by_name"):
                    rec["approved_by_name"] = rec.get("approved_by") or rec.get("user_name")
                if isinstance(rec.get("tool_arguments"), str):
                    try:
                        rec["tool_arguments"] = json.loads(rec["tool_arguments"])
                    except Exception:
                        pass
                results.append(rec)
            return results
    except Exception as e:
        logger.error(f"Error fetching approvals: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_system_stats(folder_channel_ids: Optional[List[str]] = None, folder_id: Optional[Any] = None) -> Dict[str, Any]:
    """Retrieves high level counts for dashboard overview, filtered by channel_id list if provided."""
    conn = None
    stats = {
        "jobs": {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0},
        "approvals": {"total": 0, "pending": 0, "applied": 0, "rejected": 0, "expired": 0},
        "conversations": 0,
        "snapshots": 0,
    }
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if folder_channel_ids is not None:
                if not folder_channel_ids:
                    return stats
                c_set = set()
                for item in folder_channel_ids:
                    if item:
                        clean = str(item).strip()
                        c_set.add(clean)
                        c_set.add(clean.lower())
                        c_set.add(clean.upper())
                        bare = clean.lstrip("#@")
                        if bare:
                            c_set.add(bare)
                            c_set.add(bare.lower())
                            c_set.add(bare.upper())
                            c_set.add(f"#{bare}")
                            c_set.add(f"#{bare.lower()}")
                            c_set.add(f"@{bare}")
                            c_set.add(f"@{bare.lower()}")
                c_ids = list(c_set)

                cur.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM job_queue 
                    WHERE channel_id = ANY(%s) OR (payload->>'channel_id') = ANY(%s)
                    GROUP BY status;
                """, (c_ids, c_ids))
                for r in cur.fetchall():
                    st = r["status"]
                    cnt = r["count"]
                    if st in stats["jobs"]:
                        stats["jobs"][st] = cnt
                    stats["jobs"]["total"] += cnt

                cur.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM pending_approvals 
                    WHERE channel_id = ANY(%s)
                    GROUP BY status;
                """, (c_ids,))
                for r in cur.fetchall():
                    st = r["status"]
                    cnt = r["count"]
                    if st in stats["approvals"]:
                        stats["approvals"][st] = cnt
                    stats["approvals"]["total"] += cnt

                f_id_val = folder_id if folder_id is not None else -1
                cur.execute("""
                    SELECT COUNT(*) as count 
                    FROM conversation_messages 
                    WHERE channel_id = ANY(%s) OR LOWER(channel_id) = ANY(%s)
                       OR LOWER(channel_id) IN (
                           SELECT LOWER(channel_id) FROM channel_metadata WHERE folder_id = %s OR folder_id::text = %s
                       )
                       OR LOWER(channel_id) IN (
                           SELECT LOWER(channel_name) FROM channel_metadata WHERE folder_id = %s OR folder_id::text = %s
                       );
                """, (c_ids, c_ids, f_id_val, str(f_id_val), f_id_val, str(f_id_val)))
                r_conv = cur.fetchone()
                stats["conversations"] = r_conv["count"] if r_conv else 0

                try:
                    cur.execute("""
                        SELECT COUNT(*) as count 
                        FROM claude_context_snapshots 
                        WHERE channel_id = ANY(%s) OR LOWER(channel_id) = ANY(%s);
                    """, (c_ids, c_ids))
                    r_snap = cur.fetchone()
                    stats["snapshots"] = r_snap["count"] if r_snap else 0
                except Exception:
                    stats["snapshots"] = 0
            else:
                cur.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM job_queue 
                    GROUP BY status;
                """)
                for r in cur.fetchall():
                    st = r["status"]
                    cnt = r["count"]
                    if st in stats["jobs"]:
                        stats["jobs"][st] = cnt
                    stats["jobs"]["total"] += cnt

                cur.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM pending_approvals 
                    GROUP BY status;
                """)
                for r in cur.fetchall():
                    st = r["status"]
                    cnt = r["count"]
                    if st in stats["approvals"]:
                        stats["approvals"][st] = cnt
                    stats["approvals"]["total"] += cnt

                cur.execute("SELECT COUNT(*) as count FROM conversation_messages;")
                r_conv = cur.fetchone()
                stats["conversations"] = r_conv["count"] if r_conv else 0

                try:
                    cur.execute("SELECT COUNT(*) as count FROM claude_context_snapshots;")
                    r_snap = cur.fetchone()
                    stats["snapshots"] = r_snap["count"] if r_snap else 0
                except Exception:
                    stats["snapshots"] = 0

        return stats
    except Exception as e:
        logger.error(f"Error fetching system stats: {e}")
        return stats
    finally:
        if conn:
            conn.close()


def _ensure_slack_workspaces_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS slack_workspaces (
            team_id VARCHAR(64) PRIMARY KEY,
            team_name VARCHAR(255),
            bot_token TEXT NOT NULL DEFAULT '',
            bot_user_id VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
    """)
    # Ensure standard workspace team records exist
    cur.execute("""
        INSERT INTO slack_workspaces (team_id, team_name, bot_token, created_at, updated_at)
        VALUES 
            ('T5ZMF56H5', 'Axcel World', '', NOW(), NOW()),
            ('T02HKMBE09K', 'JTS Team', '', NOW(), NOW())
        ON CONFLICT (team_id) DO UPDATE SET team_name = EXCLUDED.team_name;
    """)


def save_slack_workspace(team_id: str, team_name: str, bot_token: str, bot_user_id: str = "") -> None:
    """Stores or updates a Slack workspace token in PostgreSQL safely preserving non-empty values."""
    if not team_id:
        return
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_slack_workspaces_table(cur)
            cur.execute("""
                INSERT INTO slack_workspaces (team_id, team_name, bot_token, bot_user_id, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (team_id) DO UPDATE SET
                    team_name = COALESCE(NULLIF(EXCLUDED.team_name, ''), slack_workspaces.team_name),
                    bot_token = CASE WHEN EXCLUDED.bot_token IS NOT NULL AND LENGTH(TRIM(EXCLUDED.bot_token)) > 10 THEN EXCLUDED.bot_token ELSE slack_workspaces.bot_token END,
                    bot_user_id = COALESCE(NULLIF(EXCLUDED.bot_user_id, ''), slack_workspaces.bot_user_id),
                    updated_at = NOW();
            """, (team_id, team_name, bot_token, bot_user_id))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save slack workspace {team_id}: {e}")
    finally:
        if conn:
            conn.close()


def get_slack_workspace_token(team_id: str) -> Optional[str]:
    """Retrieves the bot token for a specific Slack workspace team_id or team_name alias."""
    if not team_id:
        return None
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_slack_workspaces_table(cur)
            # 1. Exact team_id match
            cur.execute("SELECT bot_token FROM slack_workspaces WHERE team_id = %s AND bot_token IS NOT NULL AND LENGTH(TRIM(bot_token)) > 10;", (team_id,))
            row = cur.fetchone()
            if row:
                tok = row.get("bot_token") if isinstance(row, dict) else row[0]
                if tok and str(tok).strip():
                    return str(tok).strip()

            clean_id = team_id.strip().upper()
            # 2. Axcel World aliases
            if "AXCEL" in clean_id or clean_id in ("T5ZMF56H5", "T01AXCELWORLD"):
                cur.execute(
                    "SELECT bot_token FROM slack_workspaces WHERE (team_id IN ('T5ZMF56H5', 'T01AXCELWORLD') OR team_name ILIKE %s) AND bot_token IS NOT NULL AND LENGTH(TRIM(bot_token)) > 10 ORDER BY updated_at DESC LIMIT 1;",
                    ("%Axcel%",)
                )
                r = cur.fetchone()
                if r:
                    tok = r.get("bot_token") if isinstance(r, dict) else r[0]
                    if tok and str(tok).strip():
                        return str(tok).strip()
            # 3. JTS Team aliases
            elif "JTS" in clean_id or clean_id in ("T02HKMBE09K", "T02JTSTEAM"):
                cur.execute(
                    "SELECT bot_token FROM slack_workspaces WHERE (team_id IN ('T02HKMBE09K', 'T02JTSTEAM') OR team_name ILIKE %s) AND bot_token IS NOT NULL AND LENGTH(TRIM(bot_token)) > 10 ORDER BY updated_at DESC LIMIT 1;",
                    ("%JTS%",)
                )
                r = cur.fetchone()
                if r:
                    tok = r.get("bot_token") if isinstance(r, dict) else r[0]
                    if tok and str(tok).strip():
                        return str(tok).strip()

            # 4. General fallback by case-insensitive team_id or team_name
            cur.execute(
                "SELECT bot_token FROM slack_workspaces WHERE (UPPER(team_id) = %s OR team_name ILIKE %s) AND bot_token IS NOT NULL AND LENGTH(TRIM(bot_token)) > 10 LIMIT 1;",
                (clean_id, f"%{team_id}%")
            )
            r2 = cur.fetchone()
            if r2:
                tok = r2.get("bot_token") if isinstance(r2, dict) else r2[0]
                if tok and str(tok).strip():
                    return str(tok).strip()

            return None
    except Exception as e:
        logger.debug(f"Could not retrieve token for slack workspace {team_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


def list_slack_workspaces() -> List[Dict[str, Any]]:
    """Returns all registered Slack workspaces."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_slack_workspaces_table(cur)
            cur.execute("SELECT team_id, team_name, bot_token, bot_user_id, created_at, updated_at FROM slack_workspaces ORDER BY created_at ASC;")
            return list(cur.fetchall())
    except Exception as e:
        logger.debug(f"Failed to list slack workspaces: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_slack_workspace_bot_user_id(team_id: str) -> Optional[str]:
    """Retrieves the stored bot user ID for a workspace."""
    if not team_id:
        return None
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_slack_workspaces_table(cur)
            cur.execute("SELECT bot_user_id FROM slack_workspaces WHERE team_id = %s AND bot_user_id IS NOT NULL AND bot_user_id != '' LIMIT 1;", (team_id,))
            row = cur.fetchone()
            if row:
                uid = row.get("bot_user_id") if isinstance(row, dict) else row[0]
                if uid and str(uid).strip():
                    return str(uid).strip()
            return None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def get_all_slack_bot_user_ids() -> List[str]:
    """Returns all known bot user IDs across all registered Slack workspaces."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_slack_workspaces_table(cur)
            cur.execute("SELECT DISTINCT bot_user_id FROM slack_workspaces WHERE bot_user_id IS NOT NULL AND bot_user_id != '';")
            rows = cur.fetchall()
            return [str(r.get("bot_user_id") if isinstance(r, dict) else r[0]).strip() for r in rows if r]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _ensure_global_system_settings_table(cur):
    """Ensures the global_system_settings table exists and is initialized."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS global_system_settings (
            id INT PRIMARY KEY DEFAULT 1,
            timezone VARCHAR(100) DEFAULT 'UTC',
            date_format VARCHAR(50) DEFAULT 'YYYY-MM-DD',
            time_format VARCHAR(10) DEFAULT '12h',
            show_seconds BOOLEAN DEFAULT TRUE,
            auto_dst BOOLEAN DEFAULT TRUE,
            sync_alerts BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_by VARCHAR(255) DEFAULT 'admin'
        );
        INSERT INTO global_system_settings (id, timezone, date_format, time_format, show_seconds, auto_dst, sync_alerts, updated_by)
        VALUES (1, 'UTC', 'YYYY-MM-DD', '12h', TRUE, TRUE, TRUE, 'admin')
        ON CONFLICT (id) DO NOTHING;
    """)


def get_global_system_settings() -> Dict[str, Any]:
    """Retrieve global system timezone, date, and time formatting configuration."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_global_system_settings_table(cur)
            conn.commit()
            cur.execute("""
                SELECT timezone, date_format, time_format, show_seconds, auto_dst, sync_alerts, updated_at, updated_by
                FROM global_system_settings
                WHERE id = 1;
            """)
            row = cur.fetchone()
            if row:
                if isinstance(row, dict):
                    tz = row.get("timezone")
                    df = row.get("date_format")
                    tf = row.get("time_format")
                    sec = row.get("show_seconds")
                    dst = row.get("auto_dst")
                    sync = row.get("sync_alerts")
                    upd_at = row.get("updated_at")
                    upd_by = row.get("updated_by")
                else:
                    tz, df, tf, sec, dst, sync, upd_at, upd_by = row
                return {
                    "timezone": tz or "UTC",
                    "date_format": df or "YYYY-MM-DD",
                    "time_format": tf or "12h",
                    "show_seconds": bool(sec) if sec is not None else True,
                    "auto_dst": bool(dst) if dst is not None else True,
                    "sync_alerts": bool(sync) if sync is not None else True,
                    "updated_at": upd_at.isoformat() if hasattr(upd_at, "isoformat") else str(upd_at) if upd_at else None,
                    "updated_by": upd_by or "admin",
                }
            return {
                "timezone": "UTC",
                "date_format": "YYYY-MM-DD",
                "time_format": "12h",
                "show_seconds": True,
                "auto_dst": True,
                "sync_alerts": True,
                "updated_at": None,
                "updated_by": "admin",
            }
    except Exception as e:
        logger.error(f"Error fetching global system settings: {e}", exc_info=True)
        return {
            "timezone": "UTC",
            "date_format": "YYYY-MM-DD",
            "time_format": "12h",
            "show_seconds": True,
            "auto_dst": True,
            "sync_alerts": True,
            "updated_at": None,
            "updated_by": "admin",
        }
    finally:
        if conn:
            conn.close()


def update_global_system_settings(
    timezone: Optional[str] = None,
    date_format: Optional[str] = None,
    time_format: Optional[str] = None,
    show_seconds: Optional[bool] = None,
    auto_dst: Optional[bool] = None,
    sync_alerts: Optional[bool] = None,
    updated_by: str = "admin",
) -> Dict[str, Any]:
    """Updates the global system timezone and date formatting configuration."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_global_system_settings_table(cur)
            
            cur.execute("SELECT timezone, date_format, time_format, show_seconds, auto_dst, sync_alerts FROM global_system_settings WHERE id = 1;")
            curr_row = cur.fetchone()
            if curr_row and isinstance(curr_row, dict):
                curr = (
                    curr_row.get("timezone", "UTC"),
                    curr_row.get("date_format", "YYYY-MM-DD"),
                    curr_row.get("time_format", "12h"),
                    curr_row.get("show_seconds", True),
                    curr_row.get("auto_dst", True),
                    curr_row.get("sync_alerts", True),
                )
            elif curr_row:
                curr = curr_row
            else:
                curr = ("UTC", "YYYY-MM-DD", "12h", True, True, True)

            new_tz = timezone if timezone is not None else curr[0]
            new_df = date_format if date_format is not None else curr[1]
            new_tf = time_format if time_format is not None else curr[2]
            new_sec = show_seconds if show_seconds is not None else curr[3]
            new_dst = auto_dst if auto_dst is not None else curr[4]
            new_sync = sync_alerts if sync_alerts is not None else curr[5]

            cur.execute("""
                UPDATE global_system_settings
                SET 
                    timezone = %s,
                    date_format = %s,
                    time_format = %s,
                    show_seconds = %s,
                    auto_dst = %s,
                    sync_alerts = %s,
                    updated_at = CURRENT_TIMESTAMP,
                    updated_by = %s
                WHERE id = 1
                RETURNING timezone, date_format, time_format, show_seconds, auto_dst, sync_alerts, updated_at, updated_by;
            """, (new_tz, new_df, new_tf, new_sec, new_dst, new_sync, updated_by))

            row = cur.fetchone()
            conn.commit()

            if row:
                if isinstance(row, dict):
                    tz = row.get("timezone")
                    df = row.get("date_format")
                    tf = row.get("time_format")
                    sec = row.get("show_seconds")
                    dst = row.get("auto_dst")
                    sync = row.get("sync_alerts")
                    upd_at = row.get("updated_at")
                    upd_by = row.get("updated_by")
                else:
                    tz, df, tf, sec, dst, sync, upd_at, upd_by = row
                return {
                    "timezone": tz,
                    "date_format": df,
                    "time_format": tf,
                    "show_seconds": bool(sec) if sec is not None else True,
                    "auto_dst": bool(dst) if dst is not None else True,
                    "sync_alerts": bool(sync) if sync is not None else True,
                    "updated_at": upd_at.isoformat() if hasattr(upd_at, "isoformat") else str(upd_at) if upd_at else None,
                    "updated_by": upd_by,
                }
            return get_global_system_settings()
    except Exception as e:
        logger.error(f"Error updating global system settings: {e}", exc_info=True)
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()



