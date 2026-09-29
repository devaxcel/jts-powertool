import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG_BUFFER: deque = deque(maxlen=1000)
_subscribers: List[asyncio.Queue] = []

METRICS: Dict[str, Any] = {
    "total_events": 0,
    "active_sessions": 0,
    "resumed_sessions": 0,
    "deduplicated_events": 0,
    "ignored_bot_events": 0,
}
_seen_sessions = set()
_seen_threads = set()


def get_metrics_snapshot() -> Dict[str, Any]:
    return {
        **METRICS,
        "unique_threads": len(_seen_threads),
        "unique_sessions": len(_seen_sessions),
    }


def persist_log_to_db(entry: Dict[str, Any]):
    """Persist telemetry event permanently into the PostgreSQL telemetry_logs table."""
    try:
        from app.db.session import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_logs (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ,
                    level VARCHAR(50),
                    category VARCHAR(50),
                    action VARCHAR(100),
                    thread_id VARCHAR(255),
                    session_id VARCHAR(255),
                    event_id VARCHAR(255),
                    user_id VARCHAR(255),
                    channel_id VARCHAR(255),
                    channel_name VARCHAR(255),
                    message TEXT,
                    extra JSONB
                );
                ALTER TABLE telemetry_logs ADD COLUMN IF NOT EXISTS channel_id VARCHAR(255);
                ALTER TABLE telemetry_logs ADD COLUMN IF NOT EXISTS channel_name VARCHAR(255);
                INSERT INTO telemetry_logs (
                    timestamp, level, category, action, thread_id, session_id, event_id, user_id, channel_id, channel_name, message, extra
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                entry.get("timestamp"),
                entry.get("level", "INFO"),
                entry.get("category", "SLACK"),
                entry.get("action", "LOG"),
                entry.get("thread_id", "-"),
                entry.get("session_id", "-"),
                entry.get("event_id", "-"),
                entry.get("user_id", "-"),
                entry.get("channel_id", "-"),
                entry.get("channel_name", "-"),
                entry.get("message", ""),
                json.dumps(entry.get("extra", {})),
            ))
            conn.commit()
        conn.close()
    except Exception:
        # Failsafe if DB is temporarily unreachable or table being migrated
        pass


def clear_log_buffer():
    """Clear memory buffer and truncate PostgreSQL telemetry_logs table."""
    LOG_BUFFER.clear()
    _seen_sessions.clear()
    _seen_threads.clear()
    for k in METRICS:
        METRICS[k] = 0

    try:
        from app.db.session import get_db_connection
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE telemetry_logs RESTART IDENTITY CASCADE;")
            conn.commit()
        conn.close()
    except Exception:
        pass

    payload = json.dumps({"action": "FEED_CLEARED"})
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


def push_log_entry(entry: Dict[str, Any]):
    LOG_BUFFER.append(entry)
    METRICS["total_events"] += 1
    
    # Update metrics based on action
    action = entry.get("action")
    if action == "SESSION_CREATED":
        METRICS["active_sessions"] += 1
    elif action == "SESSION_RESUMED":
        METRICS["resumed_sessions"] += 1
    elif action == "DUPLICATE_IGNORED":
        METRICS["deduplicated_events"] += 1
    elif action == "BOT_IGNORED":
        METRICS["ignored_bot_events"] += 1
        
    thread_id = entry.get("thread_id")
    if thread_id and thread_id != "-":
        _seen_threads.add(thread_id)
        
    session_id = entry.get("session_id")
    if session_id and session_id != "-":
        _seen_sessions.add(session_id)

    # Persist in PostgreSQL
    persist_log_to_db(entry)

    # Broadcast to all SSE listeners
    payload = json.dumps(entry)
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass
        except Exception:
            if q in _subscribers:
                _subscribers.remove(q)


def emit_telemetry(
    action: str,
    message: str,
    category: str = "SLACK",
    level: str = "INFO",
    thread_id: Optional[str] = None,
    session_id: Optional[str] = None,
    event_id: Optional[str] = None,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
):
    """
    Emit a structured telemetry event for the log stream, database, and real-time dashboard.
    Note: Message content / body is never logged to protect privacy.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "category": category,
        "action": action,
        "thread_id": thread_id or "-",
        "session_id": session_id or "-",
        "event_id": event_id or "-",
        "user_id": user_id or "-",
        "channel_id": channel_id or "-",
        "channel_name": channel_name or "-",
        "message": message,
        "extra": extra or {},
    }
    push_log_entry(entry)


class StructuredLogHandler(logging.Handler):
    def emit(self, record):
        try:
            # Avoid duplicating telemetry logs that go through standard logging
            if record.name.startswith("uvicorn") or record.name.startswith("fastapi") or record.name.startswith("app"):
                msg = record.getMessage()
                entry = {
                    "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                    "level": record.levelname,
                    "category": "SYSTEM",
                    "action": "SYSTEM_LOG",
                    "thread_id": "-",
                    "session_id": "-",
                    "event_id": "-",
                    "user_id": "-",
                    "message": msg,
                    "extra": {"logger": record.name},
                }
                push_log_entry(entry)
        except Exception:
            self.handleError(record)


structured_handler = StructuredLogHandler()


async def subscribe_log_stream():
    """Register an SSE client queue and yield items as they arrive with heartbeat ping."""
    queue = asyncio.Queue(maxsize=200)
    _subscribers.append(queue)
    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                # Keep-alive ping every 15s to keep proxy/SSE connections alive
                yield ": ping\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        if queue in _subscribers:
            _subscribers.remove(queue)
