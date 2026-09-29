import asyncio
import base64
import json
import logging
import os
import re
import signal
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple
import httpx
from dotenv import load_dotenv

from app.claude import stream, build_claude_messages_payload
from app.db.repositories import (
    claim_next_job,
    mark_job_completed,
    mark_job_failed,
    save_context_snapshot,
)
from app.db.session import get_db_connection
from app.log_stream import emit_telemetry
from app.memory_manager import (
    save_conversation_message,
    get_thread_context_since_last_reply,
)
from app.slack_router import (
    get_existing_claude_session,
    save_claude_session,
    get_slack_user_profile,
    is_valid_uuid,
    SYSTEM_PROMPT,
    get_system_prompt,
    MODEL,
    TENANT_ID,
)
from app.file_extractor import extract_file_content
from app.file_generator import (
    extract_file_generation_requests,
    strip_file_generation_blocks,
    format_text_for_slack,
    generate_file_bytes,
    upload_file_to_slack,
)
from app.tools.tool_adapter import ControlledToolAdapter
from app.tools.mcp_client import GitHubMCPClient
from app.tools.secrets_manager import get_secret, get_slack_bot_token
from app.services.channel_secrets_service import (
    get_channel_secret_value,
    resolve_anthropic_key,
    set_folder_key_health,
)

load_dotenv()
logger = logging.getLogger("jts_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def process_slack_files(files: list, token: str) -> tuple[list[dict], list[str]]:
    """
    Downloads Slack files using the bot token and converts them into Anthropic content blocks:
    - PDF documents (Anthropic native document block)
    - Images: PNG, JPEG, GIF, WebP (Anthropic native image block)
    - Word documents (.docx, .doc), Spreadsheets (.xlsx, .xls, .csv), Presentations (.pptx, .ppt),
      Archives (.zip, .tar), Code, and Text files: parsed into text content blocks
    - Any other file format: universal text extraction with fallback informative context
    """
    content_blocks = []
    notices = []
    
    if not files:
        return content_blocks, notices

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        for f in files:
            name = f.get("name", "file")
            download_url = f.get("url_private_download")
            mimetype = (f.get("mimetype") or "").lower()
            filetype = (f.get("filetype") or "").lower()
            size = f.get("size", 0)

            if not download_url:
                continue

            # Limit file size to 25MB
            if size > 25 * 1024 * 1024:
                notices.append(f"[File '{name}' exceeds 25MB size limit and could not be loaded]")
                continue

            try:
                resp = await client.get(download_url, headers={"Authorization": f"Bearer {token}"})
                if resp.status_code != 200:
                    logger.error(f"Failed to download Slack file '{name}': HTTP {resp.status_code}")
                    notices.append(f"[Could not download file '{name}' (HTTP {resp.status_code}). Please verify Slack bot has 'files:read' scope.]")
                    continue
                
                content_bytes = resp.content

                # 1. PDF Document (Anthropic native document block)
                if mimetype == "application/pdf" or filetype == "pdf" or name.lower().endswith(".pdf"):
                    b64_data = base64.b64encode(content_bytes).decode("utf-8")
                    content_blocks.append({
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64_data,
                        }
                    })
                    logger.info(f"Loaded PDF document '{name}' ({len(content_bytes)} bytes) for Claude.")

                # 2. Image: PNG, JPEG, GIF, WEBP (Anthropic native image block)
                elif mimetype in ["image/png", "image/jpeg", "image/gif", "image/webp"] or filetype in ["png", "jpg", "jpeg", "gif", "webp"]:
                    media_type = mimetype if mimetype in ["image/png", "image/jpeg", "image/gif", "image/webp"] else ("image/jpeg" if filetype in ["jpg", "jpeg"] else f"image/{filetype}")
                    b64_data = base64.b64encode(content_bytes).decode("utf-8")
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        }
                    })
                    logger.info(f"Loaded image '{name}' ({len(content_bytes)} bytes) for Claude.")

                # 3. Universal Extractor for ALL other files:
                # Word (.docx/.doc), Excel (.xlsx/.xls/.csv), PowerPoint (.pptx/.ppt),
                # Archives (.zip/.tar), Code, Text, Configuration, and generic files
                else:
                    extracted_text, label = extract_file_content(
                        name=name,
                        file_bytes=content_bytes,
                        mimetype=mimetype,
                        filetype=filetype,
                    )

                    if extracted_text and extracted_text.strip():
                        # Cap text to 80,000 characters to fit within context comfortably
                        if len(extracted_text) > 80000:
                            extracted_text = extracted_text[:80000] + "\n...[Content truncated for length]"
                        content_blocks.append({
                            "type": "text",
                            "text": f"--- Attached {label}: {name} ---\n{extracted_text}\n--- End of {name} ---"
                        })
                        logger.info(f"Loaded {label} '{name}' ({len(extracted_text)} chars) for Claude.")
                    else:
                        # Fallback for purely binary/media files without readable text
                        content_blocks.append({
                            "type": "text",
                            "text": f"[Attachment: '{name}' (Type: {mimetype or filetype or 'binary'}, Size: {len(content_bytes)} bytes). "
                                    f"This file contains media or binary data without readable text. "
                                    f"Please acknowledge this attachment and answer the user's question to the best of your ability.]"
                        })
                        logger.info(f"Recorded binary/media attachment info for '{name}'.")

            except Exception as e:
                logger.error(f"Error downloading/processing Slack file '{name}': {e}", exc_info=True)
                notices.append(f"[Error processing file '{name}': {str(e)}]")

    return content_blocks, notices


async def _send_slack_post_message_with_fallback(
    token: str,
    payload: dict,
    team_id: str,
    channel_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Attempts to post message to Slack. If channel_not_found, not_in_channel, or invalid_auth occurs,
    automatically tries other workspace tokens from the database/secrets to guarantee delivery across workspaces.
    Returns (sent_ts, error_msg).
    """
    candidate_tokens: List[str] = [token] if (token and len(token) > 10) else []

    # 1. Collect tokens from database slack_workspaces
    try:
        from app.db.repositories import list_slack_workspaces
        for ws in list_slack_workspaces():
            t = ws.get("bot_token")
            if t and t not in candidate_tokens and len(t) > 10:
                candidate_tokens.append(t)
    except Exception:
        pass

    # 2. Collect tokens for standard known workspaces
    for specific_id in ("T5ZMF56H5", "T02HKMBE09K", "Axcel World", "JTS Team"):
        try:
            t = get_slack_bot_token(specific_id)
            if t and t not in candidate_tokens and len(t) > 10:
                candidate_tokens.append(t)
        except Exception:
            pass

    # 3. Default fallback token
    fallback_t = get_secret("SLACK_BOT_TOKEN", "").strip() or os.getenv("SLACK_BOT_TOKEN", "").strip()
    if fallback_t and fallback_t not in candidate_tokens and len(fallback_t) > 10:
        candidate_tokens.append(fallback_t)

    last_error = "No valid Slack bot token available"
    async with httpx.AsyncClient(timeout=20.0) as http_client:
        for cand_token in candidate_tokens:
            try:
                resp = await http_client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {cand_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                )
                resp_data = resp.json()
                if resp_data.get("ok"):
                    return resp_data.get("ts"), None
                
                last_error = resp_data.get("error", "Unknown Slack error")
                # If error is not a token/channel mismatch, don't try other tokens
                if last_error not in ("channel_not_found", "not_in_channel", "invalid_auth", "token_revoked", "account_inactive", "missing_scope"):
                    return None, last_error
            except Exception as ex:
                last_error = str(ex)

    return None, last_error


async def process_job(job: dict):
    """Processes a single claimed job from the job_queue."""
    job_id = job["id"]
    event_id = job.get("event_id") or ""
    payload = job.get("payload") or {}
    team_id = payload.get("workspace_id") or job.get("team_id") or TENANT_ID
    workspace_name = payload.get("workspace_name") or ""
    channel_id = payload.get("channel_id") or job.get("channel_id") or ""
    channel_name = payload.get("channel_name") or ""
    thread_ts = job.get("thread_ts") or ""
    user_id = job.get("user_id") or "unknown-user"

    raw_text = payload.get("raw_text", "")
    reply_in_thread = payload.get("reply_in_thread", True)
    message_id = payload.get("message_id") or event_id

    from app.services.usage_service import normalize_workspace_info
    from app.services.channel_secrets_service import canonical_channel_id, resolve_slack_channel_name

    norm_wid, norm_wname = normalize_workspace_info(team_id, workspace_name=workspace_name, channel_id=channel_id)
    if norm_wid and norm_wid != "UNKNOWN":
        team_id = norm_wid
    if norm_wname:
        workspace_name = norm_wname

    token = get_slack_bot_token(team_id)
    if not channel_name and channel_id:
        try:
            channel_name = resolve_slack_channel_name(channel_id, token=token, team_id=team_id)
        except Exception:
            pass

    channel_id = canonical_channel_id(
        channel_id,
        workspace_id=team_id,
        workspace_name=workspace_name,
        channel_name=channel_name,
    )

    logger.info(f"[PIPELINE_STEP_1_WORKER] Processing Job #{job_id}: workspace='{workspace_name}' ({team_id}), channel='{channel_name}' ({channel_id}), user='{user_id}'")

    emit_telemetry(
        action="JOB_CLAIMED",
        category="WORKER",
        level="INFO",
        thread_id=thread_ts,
        event_id=event_id,
        user_id=user_id,
        message=f"Worker claimed job #{job_id} (event_id={event_id}) for processing.",
    )

    thinking_ts = None
    token = get_slack_bot_token(team_id)

    try:
        # 1. Clean prompt and resolve user identity
        cleaned_prompt = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()
        raw_files = payload.get("files", [])

        if not cleaned_prompt and not raw_files:
            logger.info(f"Job #{job_id} has empty prompt and no files. Completing cleanly without invoking Claude.")
            mark_job_completed(job_id)
            return

        if not cleaned_prompt and raw_files:
            cleaned_prompt = "Please analyze the attached file(s)."

        token = get_slack_bot_token(team_id)
        profile = await get_slack_user_profile(user_id, token)
        user_display = profile.get("real_name") or profile.get("display_name") or user_id
        if user_display and user_display != user_id:
            user_annotated_prompt = f"[{user_display}]: {cleaned_prompt}"
        else:
            user_annotated_prompt = cleaned_prompt

        # Process any attached files
        file_blocks, file_notices = await process_slack_files(raw_files, token)

        prompt_str = user_annotated_prompt
        if file_notices:
            notice_text = "\n".join(file_notices)
            prompt_str = f"{notice_text}\n\n{prompt_str}" if prompt_str else notice_text

        if file_blocks:
            file_blocks.append({
                "type": "text",
                "text": prompt_str if prompt_str else "Please analyze the attached file(s)."
            })
            user_message_to_send = file_blocks
        else:
            user_message_to_send = prompt_str

        # 1b. Post immediate "Thinking / Generating code..." feedback to Slack
        thinking_ts = None
        target_thread = (
            thread_ts
            if (reply_in_thread and thread_ts and not thread_ts.startswith("dm_") and not thread_ts.startswith("channel_"))
            else None
        )
        if channel_id:
            try:
                thinking_payload = {
                    "channel": channel_id,
                    "text": "_Thinking..._",
                }
                if target_thread:
                    thinking_payload["thread_ts"] = target_thread

                thinking_ts, _ = await _send_slack_post_message_with_fallback(
                    token=token,
                    payload=thinking_payload,
                    team_id=team_id,
                    channel_id=channel_id,
                )
                if thinking_ts:
                    logger.info(f"[DEBUG_WORKER] Posted thinking status message: ts='{thinking_ts}', channel='{channel_id}'")
            except Exception as te:
                logger.warning(f"[DEBUG_WORKER] Failed to post thinking status message: {te}")

        # 2. Lookup existing Claude session
        conv_id = f"slack-{channel_id}"
        thread_id = thread_ts or ""
        existing_session_id = get_existing_claude_session(team_id, conv_id, thread_id)

        if existing_session_id:
            emit_telemetry(
                action="SESSION_RESUMED",
                category="CLAUDE",
                level="INFO",
                thread_id=thread_id,
                session_id=existing_session_id,
                event_id=message_id,
                user_id=user_id,
                message=f"Resuming existing Claude session for thread {thread_id} (Speaker: {user_display} / {user_id})",
            )
        else:
            emit_telemetry(
                action="SESSION_INITIALIZING",
                category="CLAUDE",
                level="INFO",
                thread_id=thread_id,
                event_id=message_id,
                user_id=user_id,
                message=f"Initializing new Claude session for thread {thread_id} (Speaker: {user_display} / {user_id})",
            )

        # 3. Retrieve thread context accumulatively (last 6 messages window)
        history_messages, context_meta = get_thread_context_since_last_reply(
            channel_id=channel_id,
            thread_ts=thread_id,
            reply_in_thread=reply_in_thread,
        )

        # 3b. Retrieve semantically relevant memories via local bge-small-en-v1.5 vector DB
        vector_memories = []
        try:
            from app.vector_memory import retrieve_relevant_memories
            vector_memories = retrieve_relevant_memories(
                channel_id=channel_id,
                query_text=cleaned_prompt
            )
        except Exception as ve:
            logger.warning(f"Vector memory retrieval skipped: {ve}")

        active_system_prompt = get_system_prompt(cleaned_prompt, user_profile=profile)
        if vector_memories:
            memory_block = "\n".join([f"- {m}" for m in vector_memories])
            active_system_prompt = (
                f"{active_system_prompt}\n\n"
                f"[RELEVANT VECTOR MEMORIES FROM PAST CONVERSATIONS]\n"
                f"{memory_block}"
            )
            logger.info(f"Retrieved {len(vector_memories)} relevant vector memories for prompt.")

        context_breakdown_msg = (
            f"[ACCUMULATIVE_CONTEXT]\n"
            f"Prior bot replies preserved: {context_meta.get('prior_bot_replies_count', 0)}\n"
            f"Intervening human messages: {context_meta.get('intervening_human_count', 0)}\n"
            f"Total context messages chain: {context_meta.get('total_context_sent', 0)}\n"
            f"Vector memories retrieved: {len(vector_memories)}\n"
            f"Scope: {'Thread (' + thread_id + ')' if reply_in_thread else 'Channel (' + channel_id + ')'}"
        )

        emit_telemetry(
            action="THREAD_CONTEXT_RETRIEVED",
            category="CONTEXT",
            level="INFO",
            thread_id=thread_id,
            session_id=existing_session_id,
            event_id=message_id,
            user_id=user_id,
            message=context_breakdown_msg,
            extra={**context_meta, "vector_memories_count": len(vector_memories)},
        )

        # Build snapshot of exact messages sent to Claude for permanent storage
        payload_turns = build_claude_messages_payload(history_messages, user_message_to_send)
        snapshot_messages = []
        for pt in payload_turns:
            r = pt.get("role", "user")
            content_val = pt.get("content", "")
            if isinstance(content_val, list):
                text_parts = []
                for b in content_val:
                    if b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                    elif b.get("type") == "document":
                        text_parts.append("[Attached Document: PDF]")
                    elif b.get("type") == "image":
                        text_parts.append(f"[Attached Image: {b.get('source', {}).get('media_type', 'image')}]")
                content_display = "\n\n".join(text_parts)
            else:
                content_display = str(content_val)

            speaker = "Bot" if r == "assistant" else user_display
            snapshot_messages.append({
                "role": r,
                "speaker": speaker,
                "content": content_display,
            })

        # Permanently save Claude context snapshot in PostgreSQL
        try:
            snapshot_id = save_context_snapshot(
                channel_id=channel_id,
                thread_ts=thread_id,
                user_id=user_id,
                user_name=user_display,
                session_id=existing_session_id or "",
                model=MODEL,
                prompt_text=cleaned_prompt,
                system_prompt=active_system_prompt,
                messages_sent=snapshot_messages,
                files_included=[f.get("name") for f in raw_files if f.get("name")],
                workspace_id=team_id,
                workspace_name=workspace_name,
            )
            if snapshot_id:
                emit_telemetry(
                    action="CONTEXT_SNAPSHOT_SAVED",
                    category="CONTEXT",
                    level="INFO",
                    thread_id=thread_id,
                    session_id=existing_session_id,
                    event_id=message_id,
                    user_id=user_id,
                    message=f"Permanently stored Claude context snapshot #{snapshot_id} ({len(snapshot_messages)} turns, {len(raw_files)} files).",
                )
        except Exception as se:
            logger.warning(f"Failed to record context snapshot: {se}")

        # 4. Stream / call Claude with Agentic Tool Adapter
        # Resolve channel-scoped API keys (treat 1 Slack channel = 1 project)
        # Client's own key (channel or client folder) -> not billed; otherwise JTS key -> billed to client
        channel_anthropic_key, key_source, key_folder_id = resolve_anthropic_key(
            channel_id,
            workspace_id=team_id, workspace_name=workspace_name, channel_name=channel_name,
        )
        is_billable = key_source == "jts"
        client_key_fallback_reason = None
        channel_github_token = get_channel_secret_value(
            channel_id, "github",
            workspace_id=team_id, workspace_name=workspace_name, channel_name=channel_name,
        )

        accumulated_text = ""
        accumulated_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        real_session_id = existing_session_id
        mcp_client = GitHubMCPClient(token=channel_github_token) if channel_github_token else None
        tool_adapter = ControlledToolAdapter(
            mcp_client=mcp_client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            slack_token=token,
            workspace_id=team_id,
            workspace_name=workspace_name,
            channel_name=channel_name,
        )

        async def on_tool_event(action: str, tool_name: str, tool_args: dict, output: str, is_error: bool):
            emit_telemetry(
                action=action,
                category="TOOL",
                level="WARN" if is_error else "INFO",
                thread_id=thread_id,
                session_id=real_session_id or existing_session_id,
                event_id=message_id,
                user_id=user_id,
                message=(
                    f"Tool '{tool_name}' invoked: {tool_args}"
                    if action == "TOOL_INVOKED"
                    else f"Tool '{tool_name}' result: {output[:300]}"
                ),
                extra={"tool_name": tool_name, "args": tool_args, "is_error": is_error},
            )

        full_agent_messages = []
        async for message in stream(
            user_message=user_message_to_send,
            system_prompt=active_system_prompt,
            session_id=existing_session_id,
            model=MODEL,
            context_messages=history_messages,
            tool_adapter=tool_adapter,
            tool_callback=on_tool_event,
            api_key=channel_anthropic_key,
            key_source=key_source,
            channel_id=channel_id,
            channel_name=channel_name,
            user_id=user_id,
            workspace_id=team_id,
            workspace_name=workspace_name,
        ):
            if hasattr(message, "role"):
                if message.role == "system" and isinstance(message.content, dict):
                    extracted_session = message.content.get("data", {}).get("session_id")
                    if extracted_session and is_valid_uuid(extracted_session):
                        real_session_id = str(extracted_session)
                        save_claude_session(team_id, conv_id, thread_id, user_id, real_session_id)
                    fallback = message.content.get("data", {}).get("key_fallback")
                    if fallback:
                        # Client's key failed; Claude answered with the JTS key, so this reply is billed
                        is_billable = True
                        client_key_fallback_reason = f"Anthropic {fallback.get('status')}: {fallback.get('reason')}"
                        emit_telemetry(
                            action="CLIENT_KEY_FALLBACK",
                            category="CLAUDE",
                            level="WARN",
                            thread_id=thread_id,
                            event_id=message_id,
                            user_id=user_id,
                            channel_id=channel_id,
                            channel_name=channel_name,
                            message=f"Client API key failed ({client_key_fallback_reason}). Answered with JTS key; usage billed.",
                        )
                    chain = message.content.get("data", {}).get("full_messages_chain")
                    if chain:
                        full_agent_messages = chain
                    u_data = message.content.get("data", {}).get("usage")
                    if isinstance(u_data, dict):
                        accumulated_usage["input_tokens"] += int(u_data.get("input_tokens") or 0)
                        accumulated_usage["output_tokens"] += int(u_data.get("output_tokens") or 0)
                        accumulated_usage["total_tokens"] += int(u_data.get("total_tokens") or 0)
                        accumulated_usage["cost_usd"] += float(u_data.get("cost_usd") or 0.0)
                elif message.role == "assistant" and isinstance(message.content, str):
                    accumulated_text += message.content
            elif isinstance(message, str):
                accumulated_text += message

        # Show the client on the dashboard whether their key is working
        if key_folder_id:
            set_folder_key_health(key_folder_id, client_key_fallback_reason)

        logger.info(
            f"[DEBUG_WORKER] Claude stream finished for Job #{job_id}. "
            f"accumulated_text_len={len(accumulated_text)}, "
            f"accumulated_text_preview={repr(accumulated_text[:120]) if accumulated_text else 'EMPTY'}, "
            f"approval_card_posted={tool_adapter.approval_card_posted}"
        )

        # Save snapshot of agent interaction including tool calls if multi-turn tool calling occurred
        if full_agent_messages:
            try:
                formatted_chain = []
                for pt in full_agent_messages:
                    r = pt.get("role", "user")
                    c = pt.get("content", "")
                    if isinstance(c, list):
                        block_strs = []
                        for b in c:
                            if isinstance(b, dict):
                                btype = b.get("type")
                                if btype == "tool_use":
                                    block_strs.append(f"[Tool Call: {b.get('name')}({json.dumps(b.get('input', {}))})]")
                                elif btype == "tool_result":
                                    block_strs.append(f"[Tool Result: {str(b.get('content', ''))[:300]}]")
                                elif btype == "text":
                                    block_strs.append(b.get("text", ""))
                                else:
                                    block_strs.append(str(b))
                            else:
                                block_strs.append(str(b))
                        c_str = "\n".join(block_strs)
                    else:
                        c_str = str(c)
                    formatted_chain.append({
                        "role": r,
                        "speaker": "Bot" if r == "assistant" else user_display,
                        "content": c_str,
                    })
                save_context_snapshot(
                    channel_id=channel_id,
                    thread_ts=thread_id,
                    user_id=user_id,
                    user_name=user_display,
                    session_id=real_session_id or existing_session_id or "",
                    model=MODEL,
                    prompt_text=cleaned_prompt,
                    system_prompt=SYSTEM_PROMPT,
                    messages_sent=formatted_chain,
                    files_included=[f.get("name") for f in raw_files if f.get("name")],
                    workspace_id=team_id,
                    workspace_name=workspace_name,
                )
            except Exception as snap_err:
                logger.warning(f"Failed to record updated tool context snapshot: {snap_err}")

        # 5. Process any requested file generation and upload to Slack
        files_to_generate = extract_file_generation_requests(accumulated_text)

        # Fallback intent detection: if user explicitly asked for a file but Claude didn't output a generate_file block
        if not files_to_generate and accumulated_text:
            prompt_lower = cleaned_prompt.lower()
            if any(k in prompt_lower for k in ["generate pdf", "generate a pdf", "make a pdf", "create a pdf", "save as pdf", "pdf of this", "download pdf", "as pdf"]):
                files_to_generate.append(("summary.pdf", accumulated_text))
            elif any(k in prompt_lower for k in ["generate word", "generate docx", "generate a docx", "make a docx", "create a word", "save as word", "as docx", "as word"]):
                files_to_generate.append(("summary.docx", accumulated_text))
            elif any(k in prompt_lower for k in ["generate excel", "generate xlsx", "create spreadsheet", "export to excel", "export to csv", "generate csv"]):
                ext = "csv" if "csv" in prompt_lower else "xlsx"
                files_to_generate.append((f"data.{ext}", accumulated_text))
            elif any(k in prompt_lower for k in ["generate pptx", "generate powerpoint", "create slides", "generate presentation"]):
                files_to_generate.append(("presentation.pptx", accumulated_text))

        uploaded_files = []
        upload_errors = []
        target_thread = thread_ts if (reply_in_thread and thread_ts and not thread_ts.startswith("dm_") and not thread_ts.startswith("channel_")) else None

        for filename, file_content in files_to_generate:
            try:
                file_bytes, mime = generate_file_bytes(filename, file_content)
                emit_telemetry(
                    action="FILE_GENERATED",
                    category="FILE",
                    level="INFO",
                    thread_id=thread_id,
                    session_id=real_session_id,
                    event_id=message_id,
                    user_id=user_id,
                    message=f"Generated file '{filename}' ({len(file_bytes)} bytes, {mime})",
                )
                success = await upload_file_to_slack(
                    token=token,
                    channel_id=channel_id,
                    thread_ts=target_thread,
                    filename=filename,
                    file_bytes=file_bytes,
                    title=filename,
                    initial_comment=f"📎 Attached: *{filename}*",
                )
                if success:
                    uploaded_files.append(filename)
                    emit_telemetry(
                        action="FILE_UPLOADED_TO_SLACK",
                        category="SLACK",
                        level="INFO",
                        thread_id=thread_id,
                        session_id=real_session_id,
                        event_id=message_id,
                        user_id=user_id,
                        message=f"Successfully uploaded generated file '{filename}' to Slack.",
                    )
                else:
                    upload_errors.append(filename)
            except Exception as fe:
                logger.error(f"Error generating/uploading file '{filename}': {fe}", exc_info=True)
                upload_errors.append(filename)

        # Clean display text for Slack presentation
        display_text = strip_file_generation_blocks(accumulated_text) if accumulated_text else ""
        display_text = format_text_for_slack(display_text)
        if uploaded_files:
            file_badge = ", ".join(f"`{f}`" for f in uploaded_files)
            if not any(f"`{f}`" in display_text for f in uploaded_files):
                display_text = f"{display_text}\n\n📎 *Generated & attached:* {file_badge}".strip()

        if upload_errors:
            error_note = f"\n\n> ⚠️ *Note:* Generated {', '.join(upload_errors)} but Slack file upload failed. Please verify that the `files:write` scope is granted to the Slack bot token."
            display_text += error_note

        if not display_text:
            logger.warning(
                f"[DEBUG_WORKER] Fallback TRIGGERED for Job #{job_id}: display_text is empty. "
                f"accumulated_text_len={len(accumulated_text)}, approval_card_posted={tool_adapter.approval_card_posted}. "
                f"Will post 'I received your message, but no response was generated.' if approval_card_posted is False."
            )
            display_text = "I received your message, but no response was generated."
        else:
            display_text = format_text_for_slack(display_text)
            logger.info(
                f"[DEBUG_WORKER] display_text prepared for Job #{job_id}: "
                f"display_text_len={len(display_text)}, approval_card_posted={tool_adapter.approval_card_posted}"
            )

        # 6. Post final response to Slack
        if not token:
            raise ValueError("SLACK_BOT_TOKEN is missing from environment variables.")

        sent_ts = None
        if tool_adapter.approval_card_posted:
            logger.info(
                f"[DEBUG_WORKER] Approval card posted for Job #{job_id}. Checking for assistant explanation text."
            )
            # If Claude generated an explanation of steps, update the thinking status message with the text
            if display_text and display_text != "I received your message, but no response was generated.":
                if thinking_ts and channel_id and token:
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as http_client:
                            await http_client.post(
                                "https://slack.com/api/chat.update",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"channel": channel_id, "ts": thinking_ts, "text": display_text},
                            )
                            logger.info(f"[DEBUG_WORKER] Updated thinking status message with explanation text for Job #{job_id}.")
                    except Exception as te:
                        logger.warning(f"[DEBUG_WORKER] Failed to update explanation message: {te}")
            else:
                # Clean up the thinking status message if no extra text explanation
                if thinking_ts and channel_id and token:
                    try:
                        async with httpx.AsyncClient(timeout=10.0) as http_client:
                            del_resp = await http_client.post(
                                "https://slack.com/api/chat.delete",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"channel": channel_id, "ts": thinking_ts},
                            )
                            del_data = del_resp.json()
                            if del_data.get("ok"):
                                logger.info(
                                    f"[DEBUG_WORKER] Deleted thinking status message ts='{thinking_ts}' after approval card posted."
                                )
                    except Exception as de:
                        logger.warning(f"[DEBUG_WORKER] Failed to delete thinking message: {de}")

            emit_telemetry(
                action="APPROVAL_CARD_DELIVERED",
                category="SLACK",
                level="INFO",
                thread_id=thread_id,
                session_id=real_session_id,
                event_id=message_id,
                user_id=user_id,
                message="Interactive approval card was posted to Slack. Suppressed redundant assistant text.",
            )
        else:
            logger.info(
                f"[DEBUG_WORKER] Delivering text message to Slack for Job #{job_id}: "
                f"channel='{channel_id}', text_len={len(display_text)}, text_preview={repr(display_text[:100])}"
            )
            updated_in_place = False
            if thinking_ts and token:
                try:
                    async with httpx.AsyncClient(timeout=20.0) as http_client:
                        update_resp = await http_client.post(
                            "https://slack.com/api/chat.update",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json; charset=utf-8",
                            },
                            json={
                                "channel": channel_id,
                                "ts": thinking_ts,
                                "text": display_text,
                            },
                        )
                        update_data = update_resp.json()
                        if update_data.get("ok"):
                            sent_ts = thinking_ts
                            updated_in_place = True
                            logger.info(
                                f"[DEBUG_WORKER] Updated thinking message ts='{thinking_ts}' in-place with response."
                            )
                            emit_telemetry(
                                action="RESPONSE_DISPATCHED",
                                category="SLACK",
                                level="INFO",
                                thread_id=thread_id,
                                session_id=real_session_id,
                                event_id=message_id,
                                user_id=user_id,
                                message=f"Successfully updated thinking message in Slack channel {channel_id} (ts={sent_ts})",
                            )
                        else:
                            logger.warning(
                                f"[DEBUG_WORKER] chat.update failed for thinking message: {update_data.get('error')}, falling back to postMessage"
                            )
                except Exception as ue:
                    logger.warning(f"[DEBUG_WORKER] Exception updating thinking message: {ue}, falling back to postMessage")

            if not updated_in_place:
                post_kwargs = {
                    "channel": channel_id,
                    "text": display_text,
                }
                if reply_in_thread and thread_ts and not thread_ts.startswith("dm_") and not thread_ts.startswith("channel_"):
                    post_kwargs["thread_ts"] = thread_ts

                sent_ts, err_msg = await _send_slack_post_message_with_fallback(
                    token=token,
                    payload=post_kwargs,
                    team_id=team_id,
                    channel_id=channel_id,
                )
                if not sent_ts:
                    emit_telemetry(
                        action="SLACK_API_ERROR",
                        category="SLACK",
                        level="ERROR",
                        thread_id=thread_id,
                        session_id=real_session_id,
                        event_id=message_id,
                        message=f"Slack postMessage failed: {err_msg}",
                    )
                    raise RuntimeError(f"Slack postMessage failed: {err_msg}")
                else:
                    emit_telemetry(
                        action="RESPONSE_DISPATCHED",
                        category="SLACK",
                        level="INFO",
                        thread_id=thread_id,
                        session_id=real_session_id,
                        event_id=message_id,
                        user_id=user_id,
                        message=f"Successfully delivered response to Slack channel {channel_id} (ts={sent_ts})",
                    )

        # 7. Persist assistant reply to conversation memory with consistent thread_ts
        bot_content = "Action proposal posted for human approval." if tool_adapter.approval_card_posted else display_text
        if bot_content:
            bot_thread_ts = (
                thread_ts if (reply_in_thread and thread_ts and not thread_ts.startswith("dm_") and not thread_ts.startswith("channel_"))
                else (thread_id or sent_ts)
            )
            if accumulated_usage.get("total_tokens", 0) == 0:
                c_len = len(bot_content)
                out_toks = max(25, c_len // 4)
                in_toks = max(350, int((c_len // 4) * 2.8) + 150)
                accumulated_usage["input_tokens"] = in_toks
                accumulated_usage["output_tokens"] = out_toks
                accumulated_usage["total_tokens"] = in_toks + out_toks
                accumulated_usage["cost_usd"] = (
                    round((in_toks * 3.0 / 1_000_000.0) + (out_toks * 15.0 / 1_000_000.0), 6) if is_billable else 0.0
                )

            save_conversation_message(
                team_id=team_id,
                workspace_id=team_id,
                workspace_name=workspace_name,
                channel_id=channel_id,
                thread_ts=bot_thread_ts,
                user_id="bot",
                user_name="JTS Powertool Agent",
                role="assistant",
                content=bot_content,
                message_ts=sent_ts or "",
                input_tokens=accumulated_usage.get("input_tokens", 0),
                output_tokens=accumulated_usage.get("output_tokens", 0),
                total_tokens=accumulated_usage.get("total_tokens", 0),
                cost_usd=accumulated_usage.get("cost_usd", 0.0),
                billable=is_billable,
            )

        # 8. Mark Job Completed
        mark_job_completed(job_id)
        emit_telemetry(
            action="JOB_COMPLETED",
            category="WORKER",
            level="INFO",
            thread_id=thread_id,
            session_id=real_session_id,
            event_id=message_id,
            user_id=user_id,
            message=f"Job #{job_id} successfully completed.",
        )

    except Exception as e:
        logger.error(f"Error processing job #{job_id}: {e}", exc_info=True)
        if thinking_ts and channel_id and token:
            try:
                async with httpx.AsyncClient(timeout=10.0) as http_client:
                    await http_client.post(
                        "https://slack.com/api/chat.update",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json; charset=utf-8",
                        },
                        json={
                            "channel": channel_id,
                            "ts": thinking_ts,
                            "text": f"⚠️ An error occurred while processing your request: {str(e)}",
                        },
                    )
            except Exception:
                pass
        mark_job_failed(job_id, str(e))
        emit_telemetry(
            action="JOB_FAILED",
            category="WORKER",
            level="ERROR",
            thread_id=thread_ts,
            event_id=event_id,
            user_id=user_id,
            message=f"Job #{job_id} failed: {str(e)}",
        )


async def run_worker_loop(poll_interval: float = 1.0, stop_event: asyncio.Event = None):
    """Continuous worker loop claiming and processing jobs."""
    try:
        from app.memory_manager import run_database_migrations
        run_database_migrations()
    except Exception as me:
        logger.warning(f"Worker migration check failed: {me}")

    logger.info("Background worker started. Polling job_queue...")
    emit_telemetry(
        action="WORKER_STARTED",
        category="WORKER",
        level="INFO",
        message="Background worker daemon initialized and listening for jobs.",
    )

    while True:
        if stop_event and stop_event.is_set():
            logger.info("Stop event received. Shutting down worker loop.")
            break

        try:
            job = claim_next_job()
            if job:
                logger.info(f"Claimed job #{job['id']} (event_id={job['event_id']})")
                await process_job(job)
            else:
                await asyncio.sleep(poll_interval)
        except Exception as e:
            logger.error(f"Unexpected error in worker loop: {e}", exc_info=True)
            await asyncio.sleep(poll_interval)


def main():
    stop_event = asyncio.Event()

    def handle_sigterm(*args):
        logger.info("Received termination signal, stopping worker...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_sigterm)
    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        asyncio.run(run_worker_loop(stop_event=stop_event))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
