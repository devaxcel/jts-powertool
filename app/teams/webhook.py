import logging
import os
from typing import Any, Optional
from fastapi import APIRouter, BackgroundTasks, Header
from pydantic import BaseModel

from app.claude import stream
from app.db.repositories import record_processed_activity, get_or_create_session
from app.db.session import get_db_connection
from app.teams.client import send_activity

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
SYSTEM_PROMPT = "You are a helpful assistant talking to a user in Microsoft Teams."

class Account(BaseModel):
    id: str = ""
    name: str = ""

class ConversationRef(BaseModel):
    id: str
    tenantId: Optional[str] = "local-dev-tenant"

class Activity(BaseModel):
    type: str
    id: Optional[str] = None
    text: Optional[str] = None
    serviceUrl: str = "http://localhost:8000"
    conversation: ConversationRef
    from_: Account = Account()
    recipient: Account = Account()

    model_config = {"populate_by_name": True, "extra": "ignore"}

    def __init__(self, **data: Any) -> None:
        if "from" in data:
            data["from_"] = data.pop("from")
        super().__init__(**data)

def update_session_claude_id(session_db_id: str, new_claude_session_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE conversation_sessions SET claude_session_id = %s, updated_at = now() WHERE id = %s;",
                (new_claude_session_id, session_db_id)
            )
            conn.commit()
    finally:
        conn.close()

@router.post("/messages")
async def messages(
    activity: Activity,
    background_tasks: BackgroundTasks,
    authorization: str = Header(default=""),
):
    text = (activity.text or "").strip()
    if activity.type != "message" or not text:
        return {"status": "ignored", "reason": "not_a_text_message"}

    tenant_id = activity.conversation.tenantId or "local-dev-tenant"
    activity_id = activity.id or "synthetic-act-default"

    # 1. Deduplication check via PostgreSQL
    is_new = record_processed_activity(tenant_id, activity_id)
    if not is_new:
        logger.info(f"Duplicate activity ignored: {activity_id}")
        return {"status": "ignored", "reason": "duplicate_activity"}

    # 2. Add task to background
    background_tasks.add_task(run_agent, activity, text, tenant_id)
    return {"status": "received", "activity_id": activity_id}

async def run_agent(activity: Activity, text: str, tenant_id: str) -> None:
    conversation_id = activity.conversation.id
    user_id = activity.from_.id

    # 3. Fetch or initialize session mapping
    session_data = get_or_create_session(
        teams_tenant_id=tenant_id,
        teams_conversation_id=conversation_id,
        teams_thread_id="",
        teams_user_id=user_id,
        default_claude_session_id=""  # Empty string indicates uninitialized session
    )

    db_session_id = session_data["id"]
    existing_claude_id = session_data.get("claude_session_id") or None
    if existing_claude_id == "":
        existing_claude_id = None

    logger.info(f"Invoking Claude with resume session_id: {existing_claude_id}")

    try:
        async for message in stream(
            user_message=text,
            system_prompt=SYSTEM_PROMPT,
            session_id=existing_claude_id,
            model=MODEL,
        ):
            if message.role == "system" and isinstance(message.content, dict):
                # Capture the real Claude session UUID from the SDK system message
                real_session_id = message.content.get("data", {}).get("session_id")
                if real_session_id and real_session_id != existing_claude_id:
                    update_session_claude_id(db_session_id, real_session_id)
                    logger.info(f"Persisted Claude Session UUID: {real_session_id}")

            elif message.role == "assistant" and isinstance(message.content, str):
                logger.info(f"Agent Response: {message.content}")
                if activity.serviceUrl and not activity.serviceUrl.startswith("http://localhost"):
                    await send_activity(
                        activity.serviceUrl,
                        conversation_id,
                        {"type": "message", "text": message.content},
                    )
    except Exception as e:
        logger.error(f"Error during agent execution: {e}")
