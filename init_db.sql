-- 1. Conversation Sessions Table (Maps Slack/Teams conversation to Claude Session UUID)
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id SERIAL PRIMARY KEY,
    team_id VARCHAR(255) NOT NULL DEFAULT 'slack-workspace',
    channel_id VARCHAR(255) NOT NULL DEFAULT '',
    thread_ts VARCHAR(255) NOT NULL DEFAULT '',
    user_id VARCHAR(255),
    claude_session_id VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    -- Backward compatibility columns
    teams_tenant_id VARCHAR(255),
    teams_conversation_id VARCHAR(255),
    teams_thread_id VARCHAR(255),
    teams_user_id VARCHAR(255),
    CONSTRAINT unique_team_channel_thread UNIQUE (team_id, channel_id, thread_ts)
);

-- Ensure columns exist if table was already created
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='team_id') THEN
        ALTER TABLE conversation_sessions ADD COLUMN team_id VARCHAR(255) DEFAULT 'slack-workspace';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='channel_id') THEN
        ALTER TABLE conversation_sessions ADD COLUMN channel_id VARCHAR(255) DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='thread_ts') THEN
        ALTER TABLE conversation_sessions ADD COLUMN thread_ts VARCHAR(255) DEFAULT '';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='user_id') THEN
        ALTER TABLE conversation_sessions ADD COLUMN user_id VARCHAR(255);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='teams_tenant_id') THEN
        ALTER TABLE conversation_sessions ADD COLUMN teams_tenant_id VARCHAR(255);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='teams_conversation_id') THEN
        ALTER TABLE conversation_sessions ADD COLUMN teams_conversation_id VARCHAR(255);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='teams_thread_id') THEN
        ALTER TABLE conversation_sessions ADD COLUMN teams_thread_id VARCHAR(255);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_sessions' AND column_name='teams_user_id') THEN
        ALTER TABLE conversation_sessions ADD COLUMN teams_user_id VARCHAR(255);
    END IF;
END $$;

-- 2. Processed Events Table (Unique Slack event_id for deduplication)
CREATE TABLE IF NOT EXISTS processed_events (
    id SERIAL PRIMARY KEY,
    team_id VARCHAR(255) NOT NULL DEFAULT 'slack-workspace',
    event_id VARCHAR(255) NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_team_event UNIQUE (team_id, event_id)
);

-- 3. Processed Activities Table (Backward compatibility)
CREATE TABLE IF NOT EXISTS processed_activities (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(255) NOT NULL DEFAULT 'slack-workspace',
    activity_id VARCHAR(255) NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_tenant_activity UNIQUE (tenant_id, activity_id)
);

-- 4. Telemetry Logs Table (Permanent Database Persistence for All Streamed Logs)
CREATE TABLE IF NOT EXISTS telemetry_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    level VARCHAR(50) DEFAULT 'INFO',
    category VARCHAR(100) DEFAULT 'SLACK',
    action VARCHAR(100) NOT NULL,
    thread_id VARCHAR(255) DEFAULT '-',
    session_id VARCHAR(255) DEFAULT '-',
    event_id VARCHAR(255) DEFAULT '-',
    user_id VARCHAR(255) DEFAULT '-',
    message TEXT NOT NULL,
    extra JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Conversation Messages Table (Message History & Context Storage)
CREATE TABLE IF NOT EXISTS conversation_messages (
    id SERIAL PRIMARY KEY,
    team_id VARCHAR(255) NOT NULL DEFAULT 'slack-workspace',
    channel_id VARCHAR(255) NOT NULL,
    thread_ts VARCHAR(255) NOT NULL DEFAULT '',
    user_id VARCHAR(255) NOT NULL,
    user_name VARCHAR(255),
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    content TEXT NOT NULL,
    message_ts VARCHAR(255) DEFAULT '',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='conversation_messages' AND column_name='message_ts') THEN
        ALTER TABLE conversation_messages ADD COLUMN message_ts VARCHAR(255) DEFAULT '';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_conv_messages_thread ON conversation_messages (channel_id, thread_ts);
CREATE INDEX IF NOT EXISTS idx_conv_messages_msg_ts ON conversation_messages (channel_id, message_ts);

-- 6. Job Queue Table (Durable Queue for Background Worker)
CREATE TABLE IF NOT EXISTS job_queue (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(255) NOT NULL UNIQUE,
    team_id VARCHAR(255) NOT NULL DEFAULT 'slack-workspace',
    channel_id VARCHAR(255) NOT NULL,
    thread_ts VARCHAR(255) DEFAULT '',
    user_id VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    error_message TEXT,
    locked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_queue_status_created ON job_queue (status, created_at);

-- 7. Claude Context Snapshots Table (Permanent Storage for Messages Passed to Claude)
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_context_snapshots_channel_thread ON claude_context_snapshots (channel_id, thread_ts);
CREATE INDEX IF NOT EXISTS idx_context_snapshots_created_at ON claude_context_snapshots (created_at DESC);

-- 8. Pending Approvals Table (Human-in-the-Loop Slack Approvals for Mutating Actions)
CREATE TABLE IF NOT EXISTS pending_approvals (
    id SERIAL PRIMARY KEY,
    approval_id VARCHAR(64) UNIQUE NOT NULL,
    team_id VARCHAR(255) DEFAULT 'slack-workspace',
    channel_id VARCHAR(255) NOT NULL,
    thread_ts VARCHAR(255) DEFAULT '',
    message_ts VARCHAR(255) DEFAULT '',
    user_id VARCHAR(255) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    tool_arguments JSONB NOT NULL,
    status VARCHAR(50) DEFAULT 'pending', -- 'pending', 'approved', 'applying', 'applied', 'rejected', 'expired', 'failed'
    approved_by VARCHAR(255),
    execution_result TEXT,
    expires_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours'),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pending_approvals' AND column_name='expires_at') THEN
        ALTER TABLE pending_approvals ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP + INTERVAL '24 hours');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON pending_approvals (status, created_at);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_lookup ON pending_approvals (approval_id);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_expires ON pending_approvals (status, expires_at);
