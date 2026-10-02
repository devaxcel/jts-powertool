from app.services.secret_redaction import redact_secrets
import logging
import os
from typing import Dict, List, Optional, Any
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

_columns_checked = False
_CURRENT_MIGRATION_VERSION = "2026_09_v1"


def run_database_migrations(force: bool = False) -> dict:
    """Safely apply database schema updates with explicit commits."""
    global _columns_checked
    if _columns_checked and not force:
        return {"status": "success", "details": {"cached": True}}

    conn = None
    results = {}
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Check if this migration version has already been successfully applied
            if not force:
                try:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS schema_migrations (
                            version VARCHAR(128) PRIMARY KEY,
                            applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                        );
                    """)
                    cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s;", (_CURRENT_MIGRATION_VERSION,))
                    if cur.fetchone():
                        conn.commit()
                        _columns_checked = True
                        return {"status": "success", "details": {"version": _CURRENT_MIGRATION_VERSION, "already_applied": True}}
                except Exception as ex:
                    conn.rollback()
                    logger.debug(f"schema_migrations check: {ex}")

            # 1. Add message_ts column if not exists
            try:
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS message_ts VARCHAR(255) DEFAULT '';")
                conn.commit()
                results["column_message_ts"] = "ok"
            except Exception as e:
                conn.rollback()
                results["column_message_ts"] = f"err: {e}"

            # 2. Add index on message_ts
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_conv_messages_msg_ts ON conversation_messages (channel_id, message_ts);")
                conn.commit()
                results["index_message_ts"] = "ok"
            except Exception as e:
                conn.rollback()
                results["index_message_ts"] = f"err: {e}"

            # 3. Backfill row 142 if present
            try:
                cur.execute("UPDATE conversation_messages SET message_ts = '1788849221.951039' WHERE id = 142 AND (message_ts IS NULL OR message_ts = '');")
                conn.commit()
                results["backfill_142"] = "ok"
            except Exception as e:
                conn.rollback()
                results["backfill_142"] = f"err: {e}"

            # 3b. Add token usage and API cost USD columns
            try:
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6) DEFAULT 0.0;")

                # Backfill token counts and cost USD for conversation_messages where total_tokens = 0 or cost_usd = 0.0
                # 1. Backfill assistant messages:
                cur.execute("""
                    UPDATE conversation_messages
                    SET 
                        output_tokens = GREATEST(25, LENGTH(COALESCE(content, '')) / 4),
                        input_tokens = GREATEST(350, (LENGTH(COALESCE(content, '')) / 4) * 3 + 150),
                        total_tokens = GREATEST(350, (LENGTH(COALESCE(content, '')) / 4) * 3 + 150) + GREATEST(25, LENGTH(COALESCE(content, '')) / 4),
                        cost_usd = ROUND((
                            (GREATEST(350, (LENGTH(COALESCE(content, '')) / 4) * 3 + 150) * 3.0 / 1000000.0) +
                            (GREATEST(25, LENGTH(COALESCE(content, '')) / 4) * 15.0 / 1000000.0)
                        )::numeric, 6)
                    WHERE role = 'assistant' AND (total_tokens = 0 OR total_tokens IS NULL OR cost_usd = 0.0 OR cost_usd IS NULL);
                """)

                # 2. Backfill user messages:
                cur.execute("""
                    UPDATE conversation_messages
                    SET 
                        input_tokens = GREATEST(15, LENGTH(COALESCE(content, '')) / 4),
                        output_tokens = 0,
                        total_tokens = GREATEST(15, LENGTH(COALESCE(content, '')) / 4),
                        cost_usd = ROUND((GREATEST(15, LENGTH(COALESCE(content, '')) / 4) * 3.0 / 1000000.0)::numeric, 6)
                    WHERE role != 'assistant' AND (total_tokens = 0 OR total_tokens IS NULL OR cost_usd = 0.0 OR cost_usd IS NULL);
                """)

                # 3. Synchronize conversation_messages bot messages with api_usage_logs channel-by-channel
                try:
                    cur.execute("SELECT DISTINCT channel_id FROM api_usage_logs WHERE total_tokens > 0;")
                    active_channels = [r["channel_id"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
                    for ch in active_channels:
                        if not ch:
                            continue
                        cur.execute("""
                            SELECT input_tokens, output_tokens, total_tokens, cost_usd 
                            FROM api_usage_logs 
                            WHERE channel_id = %s OR LOWER(channel_id) = LOWER(%s)
                            ORDER BY id ASC;
                        """, (ch, ch))
                        u_logs = cur.fetchall() or []
                        if not u_logs:
                            continue
                        
                        cur.execute("""
                            SELECT id 
                            FROM conversation_messages 
                            WHERE (channel_id = %s OR LOWER(channel_id) = LOWER(%s)) AND (role = 'assistant' OR user_id = 'bot')
                            ORDER BY id ASC;
                        """, (ch, ch))
                        bot_rows = cur.fetchall() or []
                        
                        for idx, b_row in enumerate(bot_rows):
                            b_id = b_row["id"] if isinstance(b_row, dict) else b_row[0]
                            if idx < len(u_logs):
                                u = u_logs[idx]
                                in_t = u["input_tokens"] if isinstance(u, dict) else u[0]
                                out_t = u["output_tokens"] if isinstance(u, dict) else u[1]
                                tot_t = u["total_tokens"] if isinstance(u, dict) else u[2]
                                c_usd = float(u["cost_usd"] if isinstance(u, dict) else u[3])
                                cur.execute("""
                                    UPDATE conversation_messages 
                                    SET input_tokens = %s, output_tokens = %s, total_tokens = %s, cost_usd = %s
                                    WHERE id = %s;
                                """, (in_t, out_t, tot_t, c_usd, b_id))
                            else:
                                cur.execute("""
                                    UPDATE conversation_messages 
                                    SET input_tokens = 0, output_tokens = 0, total_tokens = 0, cost_usd = 0.0
                                    WHERE id = %s;
                                """, (b_id,))
                except Exception as sync_ex:
                    logger.debug(f"[MIGRATION] Alignment with api_usage_logs skipped: {sync_ex}")

                conn.commit()
                results["telemetry_columns"] = "ok"
            except Exception as e:
                conn.rollback()
                results["telemetry_columns"] = f"err: {e}"

            # 4. Backfill channel messages so thread_ts is uniform 'channel_{channel_id}' for top-level messages
            try:
                cur.execute("""
                    UPDATE conversation_messages 
                    SET thread_ts = 'channel_' || channel_id 
                    WHERE channel_id NOT LIKE 'D%' 
                      AND thread_ts NOT LIKE 'dm_%' 
                      AND thread_ts NOT LIKE 'channel_%';
                """)
                conn.commit()
                results["backfill_channel_thread_ts"] = "ok"
            except Exception as e:
                conn.rollback()
                results["backfill_channel_thread_ts"] = f"err: {e}"

            # 5. Create pending_approvals table if not exists
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS pending_approvals (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
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
                """)
                conn.commit()
                results["table_pending_approvals"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_pending_approvals"] = f"err: {e}"

            # 6. Create channel_secret_mappings table (treat 1 Slack channel = 1 project)
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS channel_secret_mappings (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        channel_id VARCHAR(255) NOT NULL,
                        channel_name VARCHAR(255),
                        provider VARCHAR(50) NOT NULL,
                        aws_secret_name VARCHAR(255) NOT NULL,
                        aws_secret_arn VARCHAR(512),
                        status VARCHAR(50) DEFAULT 'active',
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_by VARCHAR(255) DEFAULT 'admin',
                        CONSTRAINT uq_channel_provider UNIQUE (channel_id, provider)
                    );
                    CREATE INDEX IF NOT EXISTS idx_channel_secret_lookup ON channel_secret_mappings (channel_id);
                """)
                conn.commit()
                results["table_channel_secret_mappings"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_channel_secret_mappings"] = f"err: {e}"

            # 7. Create channel_metadata table to store friendly channel/project names
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS channel_metadata (
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        channel_id VARCHAR(255) PRIMARY KEY,
                        channel_name VARCHAR(255) NOT NULL,
                        channel_type VARCHAR(50) DEFAULT 'channel',
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO channel_metadata (channel_id, channel_name, channel_type, workspace_id, workspace_name)
                    VALUES 
                        ('C08MV3EM9PY', '#jts_powertool', 'channel', 'T5ZMF56H5', 'Axcel World'),
                        ('C0C28B8V2PK', '#jts_powertool', 'channel', 'T02HKMBE09K', 'JTS Team'),
                        ('C08V6S5UJ0P', '#agents_working_projects', 'channel', 'T5ZMF56H5', 'Axcel World'),
                        ('D08SLP9LXUZ', '@Admin User (DM)', 'dm', 'T5ZMF56H5', 'Axcel World')
                    ON CONFLICT (channel_id) DO UPDATE SET
                        channel_name = EXCLUDED.channel_name,
                        workspace_id = EXCLUDED.workspace_id,
                        workspace_name = EXCLUDED.workspace_name;

                    UPDATE channel_metadata SET channel_name = '#agents_working_projects', workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id IN ('C08V6S5UJ0P', 'C0BV6S5UJ0P');
                    UPDATE channel_metadata SET channel_name = '@Admin User (DM)', workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id IN ('D08SLP9LXUZ', 'D0BSLP9LXUZ');
                    UPDATE channel_metadata SET channel_name = '#jts_powertool', workspace_id = 'T02HKMBE09K', workspace_name = 'JTS Team' WHERE channel_id = 'C0C28B8V2PK';
                    UPDATE channel_metadata SET channel_name = '#jts_powertool', workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id = 'C08MV3EM9PY';

                    -- 1. Explicitly migrate JTS Team #jts_powertool data to C0C28B8V2PK
                    UPDATE api_usage_logs SET channel_id = 'C0C28B8V2PK', workspace_id = 'T02HKMBE09K', workspace_name = 'JTS Team'
                    WHERE (workspace_id = 'T02HKMBE09K' OR workspace_name ILIKE '%JTS%') 
                      AND channel_id IN ('C08MV3EM9PY', 'C0BMV3EM9PY');

                    UPDATE conversation_messages SET channel_id = 'C0C28B8V2PK', workspace_id = 'T02HKMBE09K', workspace_name = 'JTS Team', team_id = 'T02HKMBE09K'
                    WHERE (workspace_id = 'T02HKMBE09K' OR workspace_name ILIKE '%JTS%') 
                      AND channel_id IN ('C08MV3EM9PY', 'C0BMV3EM9PY');

                    UPDATE claude_context_snapshots SET channel_id = 'C0C28B8V2PK', workspace_id = 'T02HKMBE09K', workspace_name = 'JTS Team'
                    WHERE channel_id IN ('C08MV3EM9PY', 'C0BMV3EM9PY') 
                      AND (prompt_text ILIKE '%jts%' OR user_id = 'U0C2A3D3Y4C');

                    UPDATE channel_secret_mappings SET channel_id = 'C0C28B8V2PK' 
                    WHERE channel_id IN ('C08MV3EM9PY', 'C0BMV3EM9PY') 
                      AND (channel_name ILIKE '%jts_powertool%' OR secret_name ILIKE '%jts%');

                    -- 2. Explicitly migrate Axcel World #jts_powertool typo data to C08MV3EM9PY (without affecting JTS Team)
                    UPDATE api_usage_logs SET channel_id = 'C08MV3EM9PY', workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' 
                    WHERE (workspace_id = 'T5ZMF56H5' OR workspace_name ILIKE '%Axcel%') 
                      AND channel_id = 'C0BMV3EM9PY';

                    UPDATE conversation_messages SET channel_id = 'C08MV3EM9PY', workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World', team_id = 'T5ZMF56H5' 
                    WHERE (workspace_id = 'T5ZMF56H5' OR workspace_name ILIKE '%Axcel%') 
                      AND channel_id = 'C0BMV3EM9PY';

                    UPDATE claude_context_snapshots SET channel_id = 'C08MV3EM9PY', workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' 
                    WHERE channel_id = 'C0BMV3EM9PY' 
                      AND NOT (prompt_text ILIKE '%jts%' OR user_id = 'U0C2A3D3Y4C');

                    UPDATE channel_secret_mappings SET channel_id = 'C08MV3EM9PY' 
                    WHERE channel_id = 'C0BMV3EM9PY' 
                      AND NOT (channel_name ILIKE '%jts_powertool%' OR secret_name ILIKE '%jts%');

                    -- 3. Migrate other known channel typos and enforce correct canonical channels
                    UPDATE api_usage_logs SET channel_id = 'C08V6S5UJ0P' WHERE channel_id = 'C0BV6S5UJ0P';
                    UPDATE api_usage_logs SET channel_id = 'D08SLP9LXUZ' WHERE channel_id = 'D0BSLP9LXUZ';

                    UPDATE conversation_messages SET channel_id = 'C08V6S5UJ0P' WHERE channel_id = 'C0BV6S5UJ0P';
                    UPDATE conversation_messages SET channel_id = 'D08SLP9LXUZ' WHERE channel_id = 'D0BSLP9LXUZ';

                    UPDATE claude_context_snapshots SET channel_id = 'C08V6S5UJ0P' WHERE channel_id = 'C0BV6S5UJ0P';
                    UPDATE claude_context_snapshots SET channel_id = 'D08SLP9LXUZ' WHERE channel_id = 'D0BSLP9LXUZ';

                    UPDATE channel_secret_mappings SET channel_id = 'C08V6S5UJ0P' WHERE channel_id = 'C0BV6S5UJ0P';
                    UPDATE channel_secret_mappings SET channel_id = 'D08SLP9LXUZ' WHERE channel_id = 'D0BSLP9LXUZ';

                    -- 4. Strictly enforce that #agents_working_projects (C08V6S5UJ0P) belongs ONLY to Axcel World (T5ZMF56H5)
                    UPDATE api_usage_logs SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id = 'C08V6S5UJ0P';
                    UPDATE conversation_messages SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World', team_id = 'T5ZMF56H5' WHERE channel_id = 'C08V6S5UJ0P';
                    UPDATE claude_context_snapshots SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id = 'C08V6S5UJ0P';

                    -- 5. Strictly enforce that @Admin User (DM) (D08SLP9LXUZ) belongs ONLY to Axcel World (T5ZMF56H5)
                    UPDATE api_usage_logs SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id = 'D08SLP9LXUZ';
                    UPDATE conversation_messages SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World', team_id = 'T5ZMF56H5' WHERE channel_id = 'D08SLP9LXUZ';
                    UPDATE claude_context_snapshots SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id = 'D08SLP9LXUZ';

                    -- 6. Strictly enforce that C08MV3EM9PY belongs ONLY to Axcel World (T5ZMF56H5)
                    UPDATE api_usage_logs SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id = 'C08MV3EM9PY';
                    UPDATE conversation_messages SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World', team_id = 'T5ZMF56H5' WHERE channel_id = 'C08MV3EM9PY';
                    UPDATE claude_context_snapshots SET workspace_id = 'T5ZMF56H5', workspace_name = 'Axcel World' WHERE channel_id = 'C08MV3EM9PY';

                    -- 7. Strictly enforce that C0C28B8V2PK belongs ONLY to JTS Team (T02HKMBE09K)
                    UPDATE api_usage_logs SET workspace_id = 'T02HKMBE09K', workspace_name = 'JTS Team' WHERE channel_id = 'C0C28B8V2PK';
                    UPDATE conversation_messages SET workspace_id = 'T02HKMBE09K', workspace_name = 'JTS Team', team_id = 'T02HKMBE09K' WHERE channel_id = 'C0C28B8V2PK';
                    UPDATE claude_context_snapshots SET workspace_id = 'T02HKMBE09K', workspace_name = 'JTS Team' WHERE channel_id = 'C0C28B8V2PK';

                    DELETE FROM channel_metadata WHERE channel_id IN ('C0BMV3EM9PY', 'C0BV6S5UJ0P', 'D0BSLP9LXUZ');
                """)
                conn.commit()
                results["table_channel_metadata"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_channel_metadata"] = f"err: {e}"

            # 8. Create channel_folders table and add folder_id to channel_metadata
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS channel_folders (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        name VARCHAR(255) UNIQUE NOT NULL,
                        description TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    ALTER TABLE channel_metadata ADD COLUMN IF NOT EXISTS folder_id INTEGER REFERENCES channel_folders(id) ON DELETE SET NULL;
                    CREATE INDEX IF NOT EXISTS idx_channel_metadata_folder ON channel_metadata (folder_id);
                    SELECT setval(pg_get_serial_sequence('channel_folders', 'id'), COALESCE((SELECT MAX(id) FROM channel_folders), 1));
                """)
                conn.commit()
                results["table_channel_folders"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_channel_folders"] = f"err: {e}"

            # 9. Create secrets_vault table for generic key-value secrets storage
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS secrets_vault (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        key_name VARCHAR(255) NOT NULL UNIQUE,
                        aws_secret_name VARCHAR(512) NOT NULL,
                        name VARCHAR(255),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    ALTER TABLE secrets_vault ADD COLUMN IF NOT EXISTS name VARCHAR(255);
                """)
                conn.commit()
                results["table_secrets_vault"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_secrets_vault"] = f"err: {e}"

            # 10. Create api_usage_logs table for token and cost tracking per API call
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS api_usage_logs (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        channel_id VARCHAR(255),
                        user_id VARCHAR(255) DEFAULT 'unknown',
                        model VARCHAR(100) NOT NULL,
                        input_tokens INTEGER DEFAULT 0,
                        output_tokens INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        cost_usd NUMERIC(10, 6) DEFAULT 0.0,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    ALTER TABLE api_usage_logs ADD COLUMN IF NOT EXISTS user_id VARCHAR(255) DEFAULT 'unknown';
                    CREATE INDEX IF NOT EXISTS idx_api_usage_channel ON api_usage_logs (channel_id);
                    CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage_logs (user_id);
                """)
                conn.commit()
                results["table_api_usage_logs"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_api_usage_logs"] = f"err: {e}"

            # 11. Create user_memories table for vector memory storage
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_memories (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        channel_id VARCHAR(255) NOT NULL,
                        user_id VARCHAR(255) DEFAULT 'unknown',
                        role VARCHAR(50) DEFAULT 'user',
                        content TEXT NOT NULL,
                        embedding TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_user_memories_channel ON user_memories (channel_id);
                """)
                conn.commit()
                results["table_user_memories"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_user_memories"] = f"err: {e}"

            # 12. Create dashboard_users table and permissions for RBAC
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS dashboard_users (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        name VARCHAR(255),
                        username VARCHAR(100) UNIQUE NOT NULL,
                        email VARCHAR(255),
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(50) NOT NULL DEFAULT 'client_standard',
                        client_folder_id INTEGER REFERENCES channel_folders(id) ON DELETE SET NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );

                    ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS name VARCHAR(255);
                    ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS client_folder_id INTEGER REFERENCES channel_folders(id) ON DELETE SET NULL;
                    ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS timezone VARCHAR(100) DEFAULT 'UTC';
                    ALTER TABLE dashboard_users ALTER COLUMN email DROP NOT NULL;
                    ALTER TABLE dashboard_users DROP CONSTRAINT IF EXISTS dashboard_users_email_key;
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboard_users_email_lower ON dashboard_users (LOWER(TRIM(email))) WHERE email IS NOT NULL AND email != '';

                    CREATE TABLE IF NOT EXISTS user_channel_permissions (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        user_id INTEGER REFERENCES dashboard_users(id) ON DELETE CASCADE,
                        channel_id VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_user_channel UNIQUE (user_id, channel_id)
                    );

                    INSERT INTO dashboard_users (name, username, email, password_hash, role)
                    VALUES 
                        ('JTS Admin', 'admin', 'admin@jts-powertool.com', '7497b50e424557ad646510f44dd7986f3d4f77b97208577542971e7a515b6dbb', 'jts_admin'),
                        ('Client Manager', 'client_admin', 'manager@client.com', '573b2e1666b63c1787714d114daf5429cf528857af8ca5b7cfd8c68f9a537e32', 'client_admin'),
                        ('Client User', 'client_user', 'user@client.com', 'ec45c290c9c77ec007abd871364d19de5604821eb04d40d0d3b670236a5f776a', 'client_standard')
                    ON CONFLICT (username) DO NOTHING;
                """)
                conn.commit()
                results["table_dashboard_users"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_dashboard_users"] = f"err: {e}"

            # 13. Automatically recalculate existing billing log costs with updated model rates
            try:
                from app.services.usage_service import recalculate_all_usage_costs
                recalc_res = recalculate_all_usage_costs()
                results["recalculate_usage_costs"] = recalc_res.get("status", "ok")
            except Exception as e:
                results["recalculate_usage_costs"] = f"err: {e}"

            # 14. Ensure workspace_id and workspace_name columns exist on all public tables
            try:
                from psycopg2 import sql
                cur.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
                """)
                all_tables = [r["table_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
                conn.commit()

                for tbl in all_tables:
                    try:
                        cur.execute(sql.SQL("""
                            ALTER TABLE {} ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(64);
                            ALTER TABLE {} ADD COLUMN IF NOT EXISTS workspace_name VARCHAR(255);
                        """).format(sql.Identifier(tbl), sql.Identifier(tbl)))
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # Synchronize workspace_id and workspace_name dynamically from slack_workspaces
                try:
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
                    conn.commit()

                    # 1. Resolve real Axcel World ID from slack_workspaces or conversation_messages
                    axcel_target_id = None
                    try:
                        cur.execute("SELECT team_id FROM slack_workspaces WHERE team_id != 'T01AXCELWORLD' AND team_name ILIKE '%Axcel%' ORDER BY created_at ASC LIMIT 1;")
                        ax_row = cur.fetchone()
                        if not ax_row:
                            cur.execute("SELECT workspace_id FROM conversation_messages WHERE workspace_id != 'T01AXCELWORLD' AND workspace_name ILIKE '%Axcel%' AND workspace_id IS NOT NULL AND workspace_id != '' LIMIT 1;")
                            ax_row = cur.fetchone()
                        if ax_row:
                            axcel_target_id = ax_row["team_id"] if isinstance(ax_row, dict) and "team_id" in ax_row else (ax_row["workspace_id"] if isinstance(ax_row, dict) and "workspace_id" in ax_row else ax_row[0])
                        conn.commit()
                    except Exception:
                        conn.rollback()

                    if not axcel_target_id:
                        axcel_target_id = "T01AXCELWORLD"
                        try:
                            cur.execute("""
                                INSERT INTO slack_workspaces (team_id, team_name, bot_token, created_at, updated_at)
                                VALUES ('T01AXCELWORLD', 'Axcel World', '', NOW(), NOW())
                                ON CONFLICT (team_id) DO NOTHING;
                            """)
                            conn.commit()
                        except Exception:
                            conn.rollback()

                    # Clean up synthetic T01AXCELWORLD if a real Axcel team_id exists (e.g. T5ZMF56H5)
                    if axcel_target_id and axcel_target_id != "T01AXCELWORLD":
                        for tbl in all_tables:
                            try:
                                cur.execute("""
                                    SELECT column_name FROM information_schema.columns 
                                    WHERE table_schema = 'public' AND table_name = %s;
                                """, (tbl,))
                                cols = set(r["column_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall())
                                if "workspace_id" in cols:
                                    cur.execute(sql.SQL("UPDATE {} SET workspace_id = %s, workspace_name = 'Axcel World' WHERE workspace_id = 'T01AXCELWORLD' OR workspace_id = 'slack-workspace' OR workspace_id = 'slack_workspace';").format(sql.Identifier(tbl)), (axcel_target_id,))
                                if "team_id" in cols:
                                    cur.execute(sql.SQL("UPDATE {} SET team_id = %s WHERE team_id = 'T01AXCELWORLD' OR team_id = 'slack-workspace' OR team_id = 'slack_workspace';").format(sql.Identifier(tbl)), (axcel_target_id,))
                                conn.commit()
                            except Exception:
                                conn.rollback()
                        try:
                            cur.execute("DELETE FROM slack_workspaces WHERE team_id = 'T01AXCELWORLD';")
                            conn.commit()
                        except Exception:
                            conn.rollback()

                    # 2. Resolve real JTS Team ID from slack_workspaces or conversation_messages
                    jts_target_id = None
                    try:
                        cur.execute("SELECT team_id FROM slack_workspaces WHERE team_id != 'T02JTSTEAM' AND team_name ILIKE '%JTS%' ORDER BY created_at ASC LIMIT 1;")
                        jts_row = cur.fetchone()
                        if not jts_row:
                            cur.execute("SELECT workspace_id FROM conversation_messages WHERE workspace_id != 'T02JTSTEAM' AND workspace_name ILIKE '%JTS%' AND workspace_id IS NOT NULL AND workspace_id != '' LIMIT 1;")
                            jts_row = cur.fetchone()
                        if jts_row:
                            jts_target_id = jts_row["team_id"] if isinstance(jts_row, dict) and "team_id" in jts_row else (jts_row["workspace_id"] if isinstance(jts_row, dict) and "workspace_id" in jts_row else jts_row[0])
                        conn.commit()
                    except Exception:
                        conn.rollback()

                    if not jts_target_id:
                        jts_target_id = "T02JTSTEAM"
                        try:
                            cur.execute("""
                                INSERT INTO slack_workspaces (team_id, team_name, bot_token, created_at, updated_at)
                                VALUES ('T02JTSTEAM', 'JTS Team', '', NOW(), NOW())
                                ON CONFLICT (team_id) DO NOTHING;
                            """)
                            conn.commit()
                        except Exception:
                            conn.rollback()

                    # Clean up synthetic T02JTSTEAM if a real JTS team_id exists (e.g. T02HKMBE09K)
                    if jts_target_id and jts_target_id != "T02JTSTEAM":
                        for tbl in all_tables:
                            try:
                                cur.execute("""
                                    SELECT column_name FROM information_schema.columns 
                                    WHERE table_schema = 'public' AND table_name = %s;
                                """, (tbl,))
                                cols = set(r["column_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall())
                                if "workspace_id" in cols:
                                    cur.execute(sql.SQL("UPDATE {} SET workspace_id = %s, workspace_name = 'JTS Team' WHERE workspace_id = 'T02JTSTEAM';").format(sql.Identifier(tbl)), (jts_target_id,))
                                if "team_id" in cols:
                                    cur.execute(sql.SQL("UPDATE {} SET team_id = %s WHERE team_id = 'T02JTSTEAM';").format(sql.Identifier(tbl)), (jts_target_id,))
                                conn.commit()
                            except Exception:
                                conn.rollback()
                        try:
                            cur.execute("DELETE FROM slack_workspaces WHERE team_id = 'T02JTSTEAM';")
                            conn.commit()
                        except Exception:
                            conn.rollback()

                    # 3. Migrate legacy/placeholder 'slack-workspace' entries to axcel_target_id
                    for tbl in all_tables:
                        try:
                            cur.execute("""
                                SELECT column_name FROM information_schema.columns 
                                WHERE table_schema = 'public' AND table_name = %s;
                            """, (tbl,))
                            cols = set(r["column_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall())
                            
                            if "team_id" in cols:
                                cur.execute(sql.SQL("""
                                    UPDATE {} SET team_id = %s 
                                    WHERE team_id IS NULL OR team_id = '' OR team_id = 'slack-workspace' OR team_id = 'slack_workspace';
                                """).format(sql.Identifier(tbl)), (axcel_target_id,))
                            
                            if "workspace_id" in cols:
                                cur.execute(sql.SQL("""
                                    UPDATE {} SET workspace_id = %s, workspace_name = 'Axcel World'
                                    WHERE workspace_id IS NULL OR workspace_id = '' OR workspace_id = 'slack-workspace' OR workspace_id = 'slack_workspace';
                                """).format(sql.Identifier(tbl)), (axcel_target_id,))
                            conn.commit()
                        except Exception as e:
                            conn.rollback()
                            logger.warning(f"Error migrating legacy workspace IDs on table {tbl}: {e}")

                    pass
                except Exception as e:
                    conn.rollback()
                    logger.warning(f"Failed ensuring default slack_workspaces: {e}")

                # 14a. Synchronize workspace_id from team_id wherever team_id exists
                for tbl in all_tables:
                    try:
                        cur.execute("""
                            SELECT column_name FROM information_schema.columns 
                            WHERE table_schema = 'public' AND table_name = %s;
                        """, (tbl,))
                        cols = set(r["column_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall())
                        if "team_id" in cols and "workspace_id" in cols:
                            cur.execute(sql.SQL("""
                                UPDATE {} SET workspace_id = team_id 
                                WHERE team_id IS NOT NULL AND team_id != '' 
                                  AND (workspace_id IS NULL OR workspace_id = '' OR workspace_id != team_id);
                            """).format(sql.Identifier(tbl)))
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # 14b. Synchronize workspace_name from slack_workspaces table for every workspace_id
                try:
                    cur.execute("""
                        SELECT team_id, team_name FROM slack_workspaces WHERE team_id IS NOT NULL AND team_name IS NOT NULL;
                    """)
                    ws_mappings = {
                        "T01AXCELWORLD": "Axcel World",
                        "T02JTSTEAM": "JTS Team",
                    }
                    for ws_row in cur.fetchall():
                        wid = ws_row["team_id"] if isinstance(ws_row, dict) else ws_row[0]
                        wname = ws_row["team_name"] if isinstance(ws_row, dict) else ws_row[1]
                        if wid and wname:
                            ws_mappings[wid] = wname
                    conn.commit()

                    for wid, wname in ws_mappings.items():
                        for tbl in all_tables:
                            try:
                                cur.execute("""
                                    SELECT column_name FROM information_schema.columns 
                                    WHERE table_schema = 'public' AND table_name = %s;
                                """, (tbl,))
                                cols = set(r["column_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall())
                                if "workspace_id" in cols and "workspace_name" in cols:
                                    cur.execute(sql.SQL("""
                                        UPDATE {} SET workspace_name = %s WHERE workspace_id = %s;
                                    """).format(sql.Identifier(tbl)), (wname, wid))
                                conn.commit()
                            except Exception:
                                conn.rollback()
                except Exception:
                    conn.rollback()

                conn.commit()
                results["ensure_workspace_columns"] = "ok"
            except Exception as e:
                conn.rollback()
                results["ensure_workspace_columns"] = f"err: {e}"

            # 15. Create organizations table
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS organizations (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        name VARCHAR(255) NOT NULL,
                        poc VARCHAR(255),
                        phone VARCHAR(50),
                        email VARCHAR(255),
                        billing_email VARCHAR(255),
                        address TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations (name);
                    CREATE INDEX IF NOT EXISTS idx_organizations_email ON organizations (LOWER(TRIM(email))) WHERE email IS NOT NULL AND email != '';
                    CREATE INDEX IF NOT EXISTS idx_organizations_billing_email ON organizations (LOWER(TRIM(billing_email))) WHERE billing_email IS NOT NULL AND billing_email != '';

                    ALTER TABLE dashboard_users ADD COLUMN IF NOT EXISTS organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL;
                    CREATE INDEX IF NOT EXISTS idx_dashboard_users_org ON dashboard_users (organization_id);

                    -- Ensure all existing organization IDs start from 101
                    DO $$
                    DECLARE
                        rec RECORD;
                        new_id_val INT := 101;
                    BEGIN
                        IF EXISTS (SELECT 1 FROM organizations WHERE id < 101) THEN
                            -- Temporarily shift IDs to avoid unique constraint collisions
                            UPDATE organizations SET id = id + 100000;
                            FOR rec IN SELECT id FROM organizations ORDER BY id ASC LOOP
                                UPDATE dashboard_users SET organization_id = new_id_val WHERE organization_id = rec.id;
                                UPDATE organizations SET id = new_id_val WHERE id = rec.id;
                                new_id_val := new_id_val + 1;
                            END LOOP;
                        END IF;
                    END $$;

                    -- Ensure postgres sequence starts at least at 101 (setval to at least 100)
                    SELECT setval('organizations_id_seq', (SELECT GREATEST(COALESCE(MAX(id), 100), 100) FROM organizations), true);
                """)
                conn.commit()
                results["table_organizations"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_organizations"] = f"err: {e}"

            # 16. Create password_reset_tokens table
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS password_reset_tokens (
                        id SERIAL PRIMARY KEY,
                        workspace_id VARCHAR(64),
                        workspace_name VARCHAR(255),
                        user_id INTEGER REFERENCES dashboard_users(id) ON DELETE CASCADE,
                        token VARCHAR(128) UNIQUE NOT NULL,
                        token_type VARCHAR(50) DEFAULT 'set_password',
                        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        used_at TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_pwd_reset_token ON password_reset_tokens (token);
                    CREATE INDEX IF NOT EXISTS idx_pwd_reset_user ON password_reset_tokens (user_id);
                """)
                conn.commit()
                results["table_password_reset_tokens"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_password_reset_tokens"] = f"err: {e}"

            # 17. Create global_system_settings table
            try:
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
                conn.commit()
                results["table_global_system_settings"] = "ok"
            except Exception as e:
                conn.rollback()
                results["table_global_system_settings"] = f"err: {e}"

            # Record migration version in schema_migrations
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(128) PRIMARY KEY,
                        applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    );
                    INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING;
                """, (_CURRENT_MIGRATION_VERSION,))
                conn.commit()
            except Exception:
                conn.rollback()

            _columns_checked = True

        return {"status": "success", "details": results}

    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _ensure_columns(conn, cur):
    global _columns_checked
    if not _columns_checked:
        try:
            cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS message_ts VARCHAR(255) DEFAULT '';")
            cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0;")
            cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0;")
            cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0;")
            cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6) DEFAULT 0.0;")
            conn.commit()
            cur.execute("CREATE INDEX IF NOT EXISTS idx_conv_messages_msg_ts ON conversation_messages (channel_id, message_ts);")
            conn.commit()
            cur.execute("UPDATE conversation_messages SET message_ts = '1788849221.951039' WHERE id = 142 AND (message_ts IS NULL OR message_ts = '');")
            conn.commit()
            _columns_checked = True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


def save_conversation_message(
    *,
    team_id: str = "T01AXCELWORLD",
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    user_name: Optional[str] = None,
    role: str = "user",
    content: str,
    message_ts: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    cost_usd: float = 0.0,
    billable: bool = True,
):
    """
    Persist every conversation message into PostgreSQL, including token counts and API cost USD for bot replies.
    billable=False (client's own API key) stores zero cost so it never appears in client billing.
    """
    if not content or not content.strip():
        return
    content = redact_secrets(content)  # a key must never be remembered, even if it slipped past the Slack key capture

    clean_team_id = (team_id or "").strip()
    if not clean_team_id or clean_team_id in ("slack-workspace", "slack_workspace"):
        clean_team_id = (workspace_id or "T01AXCELWORLD").strip()
    if not clean_team_id or clean_team_id in ("slack-workspace", "slack_workspace"):
        clean_team_id = "T01AXCELWORLD"

    clean_ws_id = clean_team_id

    # Resolve workspace_name
    clean_ws_name = (workspace_name or "").strip()
    if not clean_ws_name or clean_ws_name in ("slack-workspace", "slack_workspace"):
        if "T02" in clean_ws_id.upper() or "JTS" in clean_ws_id.upper():
            clean_ws_name = "JTS Team"
        else:
            clean_ws_name = "Axcel World"

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_columns(conn, cur)

            # Try retrieving exact team_name from slack_workspaces if available
            if workspace_name is None:
                try:
                    cur.execute("SELECT team_name FROM slack_workspaces WHERE team_id = %s;", (clean_ws_id,))
                    ws_row = cur.fetchone()
                    if ws_row:
                        r_name = ws_row.get("team_name") if isinstance(ws_row, dict) else ws_row[0]
                        if r_name and r_name.strip():
                            clean_ws_name = r_name.strip()
                except Exception:
                    pass

            clean_role = str(role or "user").lower()
            c_len = len(content.strip())
            in_toks = int(input_tokens or 0)
            out_toks = int(output_tokens or 0)
            tot_toks = int(total_tokens or (in_toks + out_toks))
            c_usd = float(cost_usd or 0.0)

            if tot_toks == 0 or (billable and c_usd == 0.0):
                if clean_role == "assistant":
                    out_toks = max(25, c_len // 4)
                    in_toks = max(350, int((c_len // 4) * 2.8) + 150)
                    tot_toks = in_toks + out_toks
                    c_usd = round((in_toks * 3.0 / 1_000_000.0) + (out_toks * 15.0 / 1_000_000.0), 6)
                else:
                    in_toks = max(15, c_len // 4)
                    out_toks = 0
                    tot_toks = in_toks
                    c_usd = round(in_toks * 3.0 / 1_000_000.0, 6)
            if not billable:
                c_usd = 0.0

            from app.services.channel_secrets_service import canonical_channel_id
            channel_id = canonical_channel_id(channel_id, workspace_id=clean_ws_id, workspace_name=clean_ws_name)

            try:
                cur.execute(
                    """
                    INSERT INTO conversation_messages (
                        team_id, workspace_id, workspace_name, channel_id, thread_ts, user_id, user_name, role, content, message_ts, input_tokens, output_tokens, total_tokens, cost_usd, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now());
                    """,
                    (
                        clean_team_id,
                        clean_ws_id,
                        clean_ws_name,
                        channel_id,
                        thread_ts or "",
                        user_id,
                        user_name or user_id,
                        role,
                        content.strip(),
                        message_ts or "",
                        in_toks,
                        out_toks,
                        tot_toks,
                        c_usd,
                    ),
                )
                conn.commit()
            except Exception:
                # Fallback if workspace_id or message_ts columns vary
                try:
                    conn.rollback()
                    cur.execute(
                        """
                        INSERT INTO conversation_messages (
                            team_id, channel_id, thread_ts, user_id, user_name, role, content, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, now());
                        """,
                        (
                            clean_team_id,
                            channel_id,
                            thread_ts or "",
                            user_id,
                            user_name or user_id,
                            role,
                            content.strip(),
                        ),
                    )
                    conn.commit()
                except Exception as fe:
                    logger.warning(f"Conversation memory save fallback failed: {fe}")
        
        # Asynchronously / safely index user turn into local vector memory
        try:
            from app.vector_memory import save_vector_memory
            save_vector_memory(
                channel_id=channel_id,
                user_id=user_id,
                content=content,
                role=role
            )
        except Exception as ve:
            logger.debug(f"Vector memory save skipped: {ve}")

    except Exception as e:
        logger.warning(f"Conversation memory save skipped: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def get_thread_context_since_last_reply(
    channel_id: str,
    thread_ts: str,
    reply_in_thread: bool = True,
) -> tuple[List[Dict[str, Any]], Dict]:
    """Conversation context for Claude, with any key values (also in older stored messages) hidden."""
    messages, meta = _get_thread_context_raw(channel_id, thread_ts, reply_in_thread)
    for m in messages:
        if isinstance(m, dict) and isinstance(m.get("content"), str):
            m["content"] = redact_secrets(m["content"])
    return messages, meta


def _get_thread_context_raw(
    channel_id: str,
    thread_ts: str,
    reply_in_thread: bool = True,
) -> tuple[List[Dict[str, Any]], Dict]:
    """
    Accumulative chronological context retrieval for Slack interactions:
    1. Identifies all messages belonging to the conversation:
       - Thread scope: messages where thread_ts = thread_ts (replies) or message_ts = thread_ts (root parent).
       - Channel scope: recent top-level channel messages when not in a thread.
       - DM scope: continuous DM history.
    2. Gathers the complete conversation chain:
       - Historical turns (user & assistant) up to the rolling window cap (default 6 messages).
       - Bot's last response.
       - Intervening human discussions between bot replies.
    3. Guarantees that prior context is never lost when new turns occur.
    """
    conn = None
    formatted_messages: List[Dict[str, Any]] = []
    total_messages_retrieved = 0
    max_context_limit = int(os.getenv("MAX_CONTEXT_MESSAGES", "6"))

    clean_thread_ts = (thread_ts or "").strip()
    is_dm = clean_thread_ts.startswith("dm_")
    is_main_channel = (not reply_in_thread and not is_dm) or clean_thread_ts.startswith("channel_")
    channel_thread_ts = f"channel_{channel_id}"

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            _ensure_columns(conn, cur)

            # Check if column message_ts exists
            cur.execute("""
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='conversation_messages' AND column_name='message_ts';
            """)
            has_msg_col = cur.fetchone() is not None

            last_bot_id = None
            prev_bot_row = None

            # Case A: Top-level channel interaction (not in a thread and not a DM)
            if is_main_channel:
                cur.execute(
                    """
                    SELECT id, role, content, user_name, user_id, created_at
                    FROM conversation_messages
                    WHERE channel_id = %s 
                      AND (thread_ts = %s OR thread_ts = %s OR thread_ts NOT LIKE 'dm_%')
                    ORDER BY id DESC
                    LIMIT %s;
                    """,
                    (channel_id, channel_thread_ts, clean_thread_ts, max_context_limit),
                )
                rows = cur.fetchall()
                if rows:
                    rows = list(reversed(rows))
            else:
                # Case B: Inside a Slack Thread or DM
                if has_msg_col:
                    cur.execute(
                        """
                        SELECT id 
                        FROM conversation_messages 
                        WHERE channel_id = %s 
                          AND (thread_ts = %s OR message_ts = %s)
                          AND role = 'assistant'
                        ORDER BY id DESC 
                        LIMIT 1;
                        """,
                        (channel_id, clean_thread_ts, clean_thread_ts),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id 
                        FROM conversation_messages 
                        WHERE channel_id = %s 
                          AND thread_ts = %s
                          AND role = 'assistant'
                        ORDER BY id DESC 
                        LIMIT 1;
                        """,
                        (channel_id, clean_thread_ts),
                    )
                bot_row = cur.fetchone()

                if bot_row:
                    last_bot_id = bot_row["id"] if isinstance(bot_row, dict) else bot_row[0]
                else:
                    # Fallback for legacy threads where root bot message did not have message_ts set:
                    # Check if the row immediately preceding the first message in this thread is an assistant message
                    try:
                        query_min = (
                            "SELECT min(id) as first_id FROM conversation_messages WHERE channel_id = %s AND (thread_ts = %s OR message_ts = %s);"
                            if has_msg_col else
                            "SELECT min(id) as first_id FROM conversation_messages WHERE channel_id = %s AND thread_ts = %s;"
                        )
                        params_min = (channel_id, clean_thread_ts, clean_thread_ts) if has_msg_col else (channel_id, clean_thread_ts)
                        cur.execute(query_min, params_min)
                        min_row = cur.fetchone()
                        first_id = min_row["first_id"] if (min_row and isinstance(min_row, dict)) else (min_row[0] if min_row else None)
                        if first_id and first_id > 1:
                            cur.execute(
                                "SELECT id, role, content, user_name, user_id, created_at FROM conversation_messages WHERE channel_id = %s AND id = %s AND role = 'assistant';",
                                (channel_id, first_id - 1),
                            )
                            prev_bot_row = cur.fetchone()
                            if prev_bot_row:
                                last_bot_id = prev_bot_row["id"] if isinstance(prev_bot_row, dict) else prev_bot_row[0]
                    except Exception:
                        pass

                # Retrieve the full conversation chain for this thread + all prior channel conversation
                if is_dm:
                    # In DMs, retrieve continuous DM history
                    if has_msg_col:
                        cur.execute(
                            """
                            SELECT id, role, content, user_name, user_id, created_at
                            FROM conversation_messages
                            WHERE channel_id = %s AND (thread_ts = %s OR thread_ts LIKE 'dm_%%' OR message_ts = %s)
                            ORDER BY id ASC;
                            """,
                            (channel_id, clean_thread_ts, clean_thread_ts),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, role, content, user_name, user_id, created_at
                            FROM conversation_messages
                            WHERE channel_id = %s AND (thread_ts = %s OR thread_ts LIKE 'dm_%%')
                            ORDER BY id ASC;
                            """,
                            (channel_id, clean_thread_ts),
                        )
                else:
                    # In a Slack Thread inside a channel:
                    # Pass all previous conversation inside that channel + this thread's messages
                    if last_bot_id is not None:
                        if has_msg_col:
                            cur.execute(
                                """
                                SELECT id, role, content, user_name, user_id, created_at
                                FROM conversation_messages
                                WHERE channel_id = %s 
                                  AND (
                                      thread_ts = %s
                                      OR thread_ts = %s
                                      OR message_ts = %s
                                      OR (thread_ts = message_ts AND NOT thread_ts LIKE 'dm_%%')
                                      OR id = %s
                                  )
                                ORDER BY id ASC;
                                """,
                                (channel_id, channel_thread_ts, clean_thread_ts, clean_thread_ts, last_bot_id),
                            )
                        else:
                            cur.execute(
                                """
                                SELECT id, role, content, user_name, user_id, created_at
                                FROM conversation_messages
                                WHERE channel_id = %s 
                                  AND (
                                      thread_ts = %s
                                      OR thread_ts = %s
                                      OR (thread_ts = message_ts AND NOT thread_ts LIKE 'dm_%%')
                                      OR id = %s
                                  )
                                ORDER BY id ASC;
                                """,
                                (channel_id, channel_thread_ts, clean_thread_ts, last_bot_id),
                            )
                    else:
                        if has_msg_col:
                            cur.execute(
                                """
                                SELECT id, role, content, user_name, user_id, created_at
                                FROM conversation_messages
                                WHERE channel_id = %s 
                                  AND (
                                      thread_ts = %s
                                      OR thread_ts = %s
                                      OR message_ts = %s
                                      OR (thread_ts = message_ts AND NOT thread_ts LIKE 'dm_%%')
                                  )
                                ORDER BY id ASC;
                                """,
                                (channel_id, channel_thread_ts, clean_thread_ts, clean_thread_ts),
                            )
                        else:
                            cur.execute(
                                """
                                SELECT id, role, content, user_name, user_id, created_at
                                FROM conversation_messages
                                WHERE channel_id = %s 
                                  AND (
                                      thread_ts = %s
                                      OR thread_ts = %s
                                      OR (thread_ts = message_ts AND NOT thread_ts LIKE 'dm_%%')
                                  )
                                ORDER BY id ASC;
                                """,
                                (channel_id, channel_thread_ts, clean_thread_ts),
                            )

                rows = cur.fetchall()

                # If legacy preceding bot message exists and wasn't returned in rows, prepend it
                if prev_bot_row:
                    prev_id = prev_bot_row["id"] if isinstance(prev_bot_row, dict) else prev_bot_row[0]
                    existing_ids = [r["id"] if isinstance(r, dict) else r[0] for r in rows]
                    if prev_id not in existing_ids:
                        rows = [prev_bot_row] + list(rows)

            total_messages_retrieved = len(rows)

            # Apply rolling window limit if history exceeds cap
            if len(rows) > max_context_limit:
                rows = rows[-max_context_limit:]

            for r in rows:
                if isinstance(r, dict):
                    role = r.get("role", "user")
                    content = r.get("content", "")
                    user_name = r.get("user_name") or r.get("user_id") or ""
                    user_id = r.get("user_id") or ""
                else:
                    role = r[1]
                    content = r[2]
                    user_name = r[3] or r[4] or ""
                    user_id = r[4] or ""

                if role == "assistant":
                    formatted_messages.append({
                        "role": "assistant",
                        "content": content,
                        "user_name": "Bot",
                        "user_id": "bot",
                    })
                else:
                    if user_name and user_name != user_id:
                        formatted_messages.append({
                            "role": "user",
                            "content": f"[{user_name}]: {content}",
                            "user_name": user_name,
                            "user_id": user_id,
                        })
                    else:
                        formatted_messages.append({
                            "role": "user",
                            "content": content,
                            "user_name": user_id or "User",
                            "user_id": user_id,
                        })

    except Exception as e:
        logger.warning(f"Thread context retrieval primary query failed: {e}")
        # Secondary failsafe fallback query: direct thread_ts query without message_ts
        try:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, role, content, user_name, user_id, created_at
                        FROM conversation_messages
                        WHERE channel_id = %s AND thread_ts = %s
                        ORDER BY id ASC;
                        """,
                        (channel_id, clean_thread_ts),
                    )
                    rows = cur.fetchall()
                    formatted_messages = []
                    if len(rows) > max_context_limit:
                        rows = rows[-max_context_limit:]
                    for r in rows:
                        role = r["role"] if isinstance(r, dict) else r[1]
                        content = r["content"] if isinstance(r, dict) else r[2]
                        uname = (r["user_name"] if isinstance(r, dict) else r[3]) or ""
                        uid = (r["user_id"] if isinstance(r, dict) else r[4]) or ""
                        if role == "assistant":
                            formatted_messages.append({
                                "role": "assistant",
                                "content": content,
                                "user_name": "Bot",
                                "user_id": "bot",
                            })
                        else:
                            formatted_messages.append({
                                "role": "user",
                                "content": f"[{uname}]: {content}" if uname and uname != uid else content,
                                "user_name": uname or uid,
                                "user_id": uid,
                            })
                    if formatted_messages:
                        prior_bot_count = sum(1 for m in formatted_messages if m["role"] == "assistant")
                        last_asst_idx = max((i for i, m in enumerate(formatted_messages) if m["role"] == "assistant"), default=-1)
                        intervening_count = (len(formatted_messages) - 1 - last_asst_idx) if last_asst_idx != -1 else len(formatted_messages)
                        context_meta = {
                            "has_prior_bot_reply": prior_bot_count > 0,
                            "prior_bot_replies_count": prior_bot_count,
                            "intervening_human_count": intervening_count,
                            "total_context_sent": len(formatted_messages),
                            "total_messages_retrieved": len(rows),
                        }
                        return formatted_messages, context_meta
        except Exception as fe:
            logger.warning(f"Thread context retrieval secondary fallback failed: {fe}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    prior_bot_count = sum(1 for m in formatted_messages if m["role"] == "assistant")
    last_asst_idx = max((i for i, m in enumerate(formatted_messages) if m["role"] == "assistant"), default=-1)
    intervening_count = (len(formatted_messages) - 1 - last_asst_idx) if last_asst_idx != -1 else len(formatted_messages)

    context_meta = {
        "has_prior_bot_reply": prior_bot_count > 0,
        "prior_bot_replies_count": prior_bot_count,
        "intervening_human_count": intervening_count,
        "total_context_sent": len(formatted_messages),
        "total_messages_retrieved": total_messages_retrieved,
    }

    return formatted_messages, context_meta


# Backward compatibility alias
def get_rag_thread_context(channel_id: str, thread_ts: str, *args, **kwargs):
    return get_thread_context_since_last_reply(channel_id, thread_ts)


def get_thread_participants(channel_id: str, thread_ts: str) -> List[str]:
    """Retrieve distinct human participants in this thread."""
    conn = None
    participants = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT user_name 
                FROM conversation_messages 
                WHERE channel_id = %s AND thread_ts = %s AND role = 'user' AND user_name IS NOT NULL;
                """,
                (channel_id, thread_ts or ""),
            )
            rows = cur.fetchall()
            participants = [r["user_name"] if isinstance(r, dict) else r[0] for r in rows]
    except Exception as e:
        logger.warning(f"Participant retrieval skipped: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return participants
