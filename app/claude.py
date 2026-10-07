import asyncio
import json
import logging
import os
import uuid
import re
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Literal,
    Optional,
    Union,
)
import httpx
from dotenv import load_dotenv
from pydantic import BaseModel
from app.tools.secrets_manager import get_secret
from app.services.usage_service import record_api_usage, calculate_token_cost

load_dotenv()
logger = logging.getLogger(__name__)

# Maintain in-memory turn history for sessions
_SESSION_HISTORIES: Dict[str, List[Dict[str, str]]] = {}
_INVALID_ANTHROPIC_WORKSPACE_IDS = set()


class Message(BaseModel):
    role: str
    content: Union[str, Dict[str, Any]]


def build_claude_messages_payload(
    context_messages: Optional[List[Dict[str, Any]]],
    user_message: Any,
    max_turns: int = 30,
) -> List[Dict[str, Any]]:
    """
    Builds a strictly alternating [user, assistant, user, assistant, ... user]
    messages payload for Anthropic Claude API:
    - Merges consecutive human messages between bot replies into single 'user' turns.
    - Preserves all historical conversation turns up to max_turns.
    - Incorporates intervening human team discussion into the final user turn along with user_message & files.
    - Guarantees strict alternation: user -> assistant -> user -> ...
    - Guarantees start and end with role 'user'.
    """
    if not context_messages:
        return [{"role": "user", "content": user_message}]

    valid_msgs = []
    for m in context_messages:
        c = m.get("content", "")
        if (isinstance(c, str) and c.strip()) or isinstance(c, list):
            valid_msgs.append({
                "role": m.get("role", "user"),
                "content": c,
                "user_name": m.get("user_name") or "",
            })

    if not valid_msgs:
        return [{"role": "user", "content": user_message}]

    last_asst_idx = -1
    for i in range(len(valid_msgs) - 1, -1, -1):
        if valid_msgs[i]["role"] == "assistant":
            last_asst_idx = i
            break

    if last_asst_idx != -1:
        prior_msgs = valid_msgs[: last_asst_idx + 1]
        intervening_msgs = valid_msgs[last_asst_idx + 1 :]
    else:
        prior_msgs = []
        intervening_msgs = valid_msgs

    turns: List[Dict[str, Any]] = []
    for m in prior_msgs:
        role = m["role"]
        c = m["content"]
        text_content = c if isinstance(c, str) else str(c)
        if role == "assistant" and "interactive approval card has been posted" in text_content:
            text_content = "Action proposal was prepared and submitted for human approval."

        if turns and turns[-1]["role"] == role:
            existing_content = turns[-1]["content"]
            if isinstance(existing_content, str):
                turns[-1]["content"] = f"{existing_content}\n\n{text_content}"
            else:
                turns[-1]["content"] = f"{str(existing_content)}\n\n{text_content}"
        else:
            turns.append({
                "role": role,
                "content": text_content,
            })

    if turns and turns[0]["role"] == "assistant":
        turns.insert(0, {
            "role": "user",
            "content": "[Conversation started on the assistant message below]",
        })

    intervening_text_parts = []
    curr_text_only = ""
    if isinstance(user_message, str):
        curr_text_only = user_message.strip()
    elif isinstance(user_message, list):
        for b in user_message:
            if b.get("type") == "text":
                curr_text_only = b.get("text", "").strip()
                break

    for i, im in enumerate(intervening_msgs):
        raw_c = im.get("content", "")
        im_text = raw_c if isinstance(raw_c, str) else str(raw_c)
        if i == len(intervening_msgs) - 1 and curr_text_only:
            norm_im = re.sub(r"^\[.*?\]:\s*", "", im_text).strip()
            norm_curr = re.sub(r"^\[.*?\]:\s*", "", curr_text_only).strip()
            if norm_im == norm_curr:
                continue
        intervening_text_parts.append(im_text)

    if intervening_text_parts:
        intervening_block = "\n\n".join(intervening_text_parts)
        if isinstance(user_message, str):
            final_user_content = f"{intervening_block}\n\n{user_message}"
        elif isinstance(user_message, list):
            new_blocks = []
            text_merged = False
            for b in user_message:
                if b.get("type") == "text" and not text_merged:
                    t = b.get("text", "")
                    new_blocks.append({
                        "type": "text",
                        "text": f"{intervening_block}\n\n{t}",
                    })
                    text_merged = True
                else:
                    new_blocks.append(b)
            if not text_merged:
                new_blocks.insert(0, {"type": "text", "text": intervening_block})
            final_user_content = new_blocks
        else:
            final_user_content = f"{intervening_block}\n\n{str(user_message)}"
    else:
        final_user_content = user_message

    turns.append({
        "role": "user",
        "content": final_user_content,
    })

    if len(turns) > max_turns:
        turns = turns[-max_turns:]
        if turns[0]["role"] != "user":
            turns = turns[1:]

    return turns


def _anthropic_error_message(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        return resp.text


def _is_client_key_failure(status_code: int, err_msg: str) -> bool:
    """Key problems the client must fix (invalid, revoked, no permission, out of credit)."""
    if status_code in (401, 402, 403):
        return True
    msg = (err_msg or "").lower()
    return status_code == 400 and ("credit balance" in msg or "billing" in msg)


def should_force_tool_calling(user_message: Union[str, List[Dict[str, Any]]]) -> bool:
    """Detects if user intent is explicitly a tool operation (GitHub or Web)."""
    text = ""
    raw = ""
    if isinstance(user_message, str):
        raw = user_message
        text = user_message.lower()
    elif isinstance(user_message, list):
        for b in user_message:
            if isinstance(b, dict) and b.get("type") == "text":
                raw += " " + b.get("text", "")
                text += " " + b.get("text", "").lower()

    # Jira: a clear Jira request must end in a real tool call (never just a promise to "send it for approval").
    # An issue key like KAN-2 is only recognised in capitals so words like "covid-19" don't count.
    if re.search(r"\bjira\b", text) or re.search(
        r"\b(create|add|update|comment|move|transition|close|assign|change|show|get|open|list|search|find)\b.*\b(ticket|tickets)\b", text
    ) or re.search(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", raw):
        return True

    # Saving a key must go to the secure-form tool, not be answered in prose. Pasted key-like strings count too.
    if re.search(r"\b(list|show|display|which|what|check|see)\b.*\b(saved|stored|my|our|the|client|api)?\s*(api[ -]?keys|keys|secrets|credentials)\b", text) or re.search(
        r"\bkey\s+value\b|\bvalue\s+of\s+(the\s+)?(api\s+)?key\b", text
    ) or re.search(r"\b(save|add|store|set|update|replace|change)\b.*\b(api[ -]?keys?|secrets?|tokens?|credentials?)\b", text) or re.search(
        r"\b(sk-[a-z0-9_-]{16,}|ghp_[a-z0-9]{20,}|xox[bp]-[a-z0-9-]{10,})", text
    ):
        return True

    if "http://" in text or "https://" in text or "www." in text:
        return True

    patterns = [
        r"\b(search|google|look up|find|browse)\b.*\b(web|internet|online|site|link|website)\b",
        r"\b(create|open|file|new)\b.*\b(issue|issues|pr|pull request|branch)\b",
        r"\b(create|make|write|generate|add|update|modify|push|commit|save|build|setup|install)\b.*\b(file|code|page|script|readme|index|template|structure|project|react|\.html|\.css|\.js|\.py|\.json|\.md)\b",
        r"\b(list|show|get|check|search|view|read|find|what)\b.*\b(issue|issues|commit|commits|file|files|code|repo|repository)\b",
        r"\b(push|commit)\b.*\b(files|code|changes)\b",
    ]
    return any(re.search(p, text) for p in patterns)


def supports_forced_tool_choice(model: str) -> bool:
    """Newer models (Sonnet 5.x, Opus 5.x, Fable, Mythos) reject tool_choice "any"/"tool" with a 400."""
    m = (model or "").lower()
    return not any(k in m for k in ("sonnet-5", "opus-5", "fable", "mythos"))


async def stream(
    *,
    user_message: Union[str, List[Dict[str, Any]]],
    system_prompt: str,
    session_id: Optional[str],
    model: str,
    effort: Optional[Literal["low", "medium", "high", "max"]] = None,
    context_messages: Optional[List[Dict[str, str]]] = None,
    tool_adapter: Optional[Any] = None,
    tool_callback: Optional[Any] = None,
    api_key: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    user_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    key_source: str = "jts",
    max_agent_turns_override: Optional[int] = None,
    max_tokens_override: Optional[int] = None,
    cost_cap_usd: Optional[float] = None,
    request_timeout: float = 90.0,
) -> AsyncIterator[Message]:
    from app.services.channel_secrets_service import canonical_channel_id
    if channel_id:
        channel_id = canonical_channel_id(
            channel_id,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            channel_name=channel_name,
        )

    active_api_key = (api_key or get_secret("ANTHROPIC_API_KEY", "")).strip().strip('"').strip("'")
    if not active_api_key:
        raise ValueError("ANTHROPIC_API_KEY is missing from channel secrets and environment variables.")

    logger.info(f"[PIPELINE_STEP_2_CLAUDE] Stream starting: workspace='{workspace_name}' ({workspace_id}), channel='{channel_name}' ({channel_id}), user='{user_id}'")

    # Generate or reuse session UUID
    active_session_id = session_id if (session_id and len(session_id) > 10) else str(uuid.uuid4())
    
    # Emit session initialization event for database mapping
    yield Message(
        role="system",
        content={"data": {"session_id": active_session_id}}
    )

    # Ensure session history list exists
    if active_session_id not in _SESSION_HISTORIES:
        _SESSION_HISTORIES[active_session_id] = []
    history = _SESSION_HISTORIES[active_session_id]

    # Build conversation messages payload using intelligent multi-turn alternating builder
    max_turns = int(os.getenv("MAX_CONTEXT_TURNS", "30"))
    if context_messages and len(context_messages) > 0:
        trimmed_messages = build_claude_messages_payload(context_messages, user_message, max_turns=max_turns)
    else:
        # In-memory history fallback
        text_summary = user_message if isinstance(user_message, str) else "[User attached document/file]"
        history.append({"role": "user", "content": text_summary})
        trimmed_messages = [{"role": "user", "content": user_message}]

    headers = {
        "x-api-key": active_api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "pdfs-2024-09-25",
        "content-type": "application/json",
    }
    
    # JTS's Anthropic workspace only applies to the JTS key, never to a client's own key
    anthropic_ws_id = get_secret("ANTHROPIC_WORKSPACE_ID", "").strip().strip('"').strip("'")
    if key_source != "client" and anthropic_ws_id and anthropic_ws_id not in _INVALID_ANTHROPIC_WORKSPACE_IDS:
        headers["anthropic-workspace-id"] = anthropic_ws_id

    try:
        raw_max_tokens = int(os.getenv("MAX_TOKENS", "8192"))
        max_tokens = max(raw_max_tokens, 8192)
    except Exception:
        max_tokens = 8192
    if max_tokens_override:
        max_tokens = int(max_tokens_override)

    # Retrieve allowed tools from Controlled Tool Adapter if present
    allowed_tools = tool_adapter.get_allowed_tools() if tool_adapter else []

    # If tools are configured, run the Agentic Tool Calling Loop
    if allowed_tools:
        current_messages = list(trimmed_messages)
        max_agent_turns = int(max_agent_turns_override or os.getenv("MAX_AGENT_TURNS", "5"))
        turn_count = 0
        full_assistant_reply = ""
        spent_usd = 0.0  # cost of this request so far (for the optional cap)
        stopped_for_cost = False
        # Loop guards: stop the agent from burning tokens by repeating itself
        if not cost_cap_usd:
            try:
                cost_cap_usd = float(get_secret("REPLY_COST_CAP_USD", "1.0") or 1.0)
            except ValueError:
                cost_cap_usd = 1.0
        max_same_call = int(get_secret("MAX_IDENTICAL_TOOL_CALLS", "2") or 2)
        max_error_turns = int(get_secret("MAX_TOOL_ERROR_TURNS", "3") or 3)
        call_counts: Dict[str, int] = {}
        blocked_repeats = 0
        error_turns = 0
        stopped_for_loop = ""

        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                while turn_count < max_agent_turns:
                    turn_count += 1
                    logger.info(f"[DEBUG_CLAUDE] Starting agent turn {turn_count}/{max_agent_turns} (model={model})")
                    payload = {
                        "model": model,
                        "max_tokens": max_tokens,
                        "system": system_prompt,
                        "messages": current_messages,
                        "tools": allowed_tools,
                    }
                    if turn_count == 1 and supports_forced_tool_choice(model) and should_force_tool_calling(user_message):
                        payload["tool_choice"] = {"type": "any"}
                        logger.info(f"[DEBUG_CLAUDE] Turn 1 forced tool_choice: type=any")

                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers=headers,
                        json=payload
                    )

                    if resp.status_code != 200:
                        err_body = resp.text
                        try:
                            err_json = resp.json()
                            err_msg = err_json.get("error", {}).get("message", err_body)
                        except Exception:
                            err_msg = err_body

                        # Graceful fallback: If workspace ID is not found, retry automatically without workspace header
                        if resp.status_code == 404 and "anthropic-workspace-id" in headers and "workspace" in str(err_msg).lower():
                            bad_ws = headers.pop("anthropic-workspace-id", None)
                            if bad_ws:
                                _INVALID_ANTHROPIC_WORKSPACE_IDS.add(bad_ws)
                            logger.warning(
                                f"[DEBUG_CLAUDE] Anthropic Workspace ID '{bad_ws}' "
                                f"not found. Retrying without workspace-id header..."
                            )
                            resp = await client.post(
                                "https://api.anthropic.com/v1/messages",
                                headers=headers,
                                json=payload
                            )
                            err_msg = _anthropic_error_message(resp)

                        # Client's own key is invalid/revoked/out of credit: answer with the JTS key and bill it
                        if (
                            resp.status_code != 200
                            and key_source == "client"
                            and _is_client_key_failure(resp.status_code, err_msg)
                        ):
                            jts_key = get_secret("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'")
                            if jts_key and jts_key != headers.get("x-api-key"):
                                logger.warning(
                                    f"[DEBUG_CLAUDE] Client key failed ({resp.status_code}: {err_msg}). "
                                    f"Falling back to JTS key (billed) for channel={channel_id}."
                                )
                                headers["x-api-key"] = jts_key
                                key_source = "jts"
                                if anthropic_ws_id and anthropic_ws_id not in _INVALID_ANTHROPIC_WORKSPACE_IDS:
                                    headers["anthropic-workspace-id"] = anthropic_ws_id
                                yield Message(
                                    role="system",
                                    content={"data": {"key_fallback": {"status": resp.status_code, "reason": str(err_msg)[:300]}}},
                                )
                                resp = await client.post(
                                    "https://api.anthropic.com/v1/messages",
                                    headers=headers,
                                    json=payload
                                )
                                err_msg = _anthropic_error_message(resp)

                        if resp.status_code != 200:
                            logger.error(f"[DEBUG_CLAUDE] Anthropic API Error ({resp.status_code}): {err_msg}")
                            yield Message(
                                role="assistant",
                                content="Sorry, I couldn't process that request right now. Please try again in a moment.",
                            )
                            return

                    resp_data = resp.json()
                    stop_reason = resp_data.get("stop_reason")
                    content_blocks = resp_data.get("content", [])

                    # Record API usage and cost USD
                    usage_info = resp_data.get("usage", {})
                    in_toks = usage_info.get("input_tokens", 0)
                    out_toks = usage_info.get("output_tokens", 0)
                    if not in_toks and current_messages:
                        in_toks = max(1, len(str(current_messages) + (system_prompt or "")) // 4)
                    if not out_toks and content_blocks:
                        out_toks = max(1, len(str(content_blocks)) // 4)

                    try:
                        u_res = record_api_usage(
                            workspace_id=workspace_id,
                            workspace_name=workspace_name,
                            channel_id=channel_id,
                            channel_name=channel_name,
                            user_id=user_id,
                            model=model,
                            input_tokens=in_toks,
                            output_tokens=out_toks,
                            key_source=key_source,
                        )
                        if isinstance(u_res, dict) and u_res.get("status") != "error":
                            yield Message(role="system", content={"data": {"usage": u_res}})
                    except Exception as usage_err:
                        logger.warning(f"[USAGE] Failed to record usage log: {usage_err}")

                    spent_usd += calculate_token_cost(model, in_toks, out_toks)

                    tool_use_blocks = [b for b in content_blocks if b.get("type") == "tool_use"]
                    text_blocks = [b for b in content_blocks if b.get("type") == "text"]

                    logger.info(
                        f"[DEBUG_CLAUDE] Turn {turn_count} API response: stop_reason='{stop_reason}', "
                        f"total_blocks={len(content_blocks)}, tool_use_blocks={len(tool_use_blocks)}, text_blocks={len(text_blocks)}"
                    )

                    if stop_reason == "tool_use" or tool_use_blocks:
                        # Append assistant turn with tool_use block(s)
                        current_messages.append({"role": "assistant", "content": content_blocks})

                        tool_results = []
                        for tub in tool_use_blocks:
                            tool_id = tub.get("id")
                            tool_name = tub.get("name")
                            tool_args = tub.get("input", {})

                            has_content = "content" in tool_args
                            content_len = len(str(tool_args.get("content", ""))) if has_content else 0
                            clean_args_summary = {
                                k: (f"<{len(str(v))} chars>" if k == "content" else v)
                                for k, v in tool_args.items()
                            }
                            logger.info(
                                f"[DEBUG_CLAUDE] Turn {turn_count} tool call: tool='{tool_name}', id='{tool_id}', "
                                f"has_content={has_content}, content_len={content_len}, args={clean_args_summary}"
                            )

                            if tool_callback:
                                await tool_callback(
                                    action="TOOL_INVOKED",
                                    tool_name=tool_name,
                                    tool_args=tool_args,
                                    output="",
                                    is_error=False,
                                )

                            call_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
                            call_counts[call_key] = call_counts.get(call_key, 0) + 1
                            if call_counts[call_key] > max_same_call:
                                blocked_repeats += 1
                                tool_output = (
                                    f"LOOP GUARD: you already made this exact '{tool_name}' call {call_counts[call_key] - 1} times in this "
                                    "reply. It will not run again. Use the results you already have and answer the user now, or ask them "
                                    "for what is missing."
                                )
                                is_error = True
                                logger.warning(f"[LOOP_GUARD] Blocked repeated '{tool_name}' call (#{call_counts[call_key]}) in channel={channel_id}")
                                if tool_callback:
                                    await tool_callback(
                                        action="LOOP_GUARD_BLOCKED",
                                        tool_name=tool_name,
                                        tool_args=tool_args,
                                        output=tool_output,
                                        is_error=True,
                                    )
                            else:
                                tool_output, is_error = await tool_adapter.execute_tool(tool_name, tool_args)
                            output_preview = str(tool_output)[:200].replace("\n", " ")
                            logger.info(
                                f"[DEBUG_CLAUDE] Turn {turn_count} tool result: tool='{tool_name}', is_error={is_error}, "
                                f"output_len={len(str(tool_output))}, output_preview='{output_preview}'"
                            )

                            if tool_callback:
                                await tool_callback(
                                    action="TOOL_RESULT_RECEIVED" if not is_error else "TOOL_BLOCKED_OR_ERROR",
                                    tool_name=tool_name,
                                    tool_args=tool_args,
                                    output=tool_output,
                                    is_error=is_error,
                                )

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": tool_output,
                                "is_error": is_error,
                            })

                        # Append user turn with tool results
                        current_messages.append({"role": "user", "content": tool_results})

                        # Loop guards: repeated blocked calls, or turn after turn where every tool call failed
                        error_turns = error_turns + 1 if tool_results and all(r.get("is_error") for r in tool_results) else 0
                        if blocked_repeats >= 2:
                            stopped_for_loop = "repeat"
                        elif error_turns >= max_error_turns:
                            stopped_for_loop = "errors"
                        if stopped_for_loop:
                            logger.warning(f"[LOOP_GUARD] Stopping the agent loop ({stopped_for_loop}) at turn {turn_count} in channel={channel_id}")
                            break

                        # Short-circuit if an interactive approval card was already posted to Slack.
                        # Continuing to the next turn would redundantly call Claude to generate
                        # conversational text that is discarded by the worker anyway.
                        if getattr(tool_adapter, "approval_card_posted", False):
                            logger.info(
                                f"[DEBUG_CLAUDE] Approval card was posted to Slack. "
                                f"Short-circuiting agent loop at turn {turn_count}/{max_agent_turns} to avoid redundant turn."
                            )
                            break

                        # Stop before another (paid) turn once the request's cost cap is reached
                        if cost_cap_usd and spent_usd >= cost_cap_usd:
                            stopped_for_cost = True
                            logger.warning(
                                f"[DEBUG_CLAUDE] Cost cap reached (${spent_usd:.4f} >= ${cost_cap_usd:.2f}) at turn {turn_count}. Stopping."
                            )
                            break

                        # Continue agent loop to next turn
                    else:
                        # Final text output turn
                        for b in content_blocks:
                            if b.get("type") == "text":
                                chunk = b.get("text", "")
                                full_assistant_reply += chunk
                                yield Message(role="assistant", content=chunk)
                        logger.info(
                            f"[DEBUG_CLAUDE] Turn {turn_count} final text completed: reply_len={len(full_assistant_reply)}, "
                            f"preview='{repr(full_assistant_reply[:120])}'"
                        )
                        break

                if (stopped_for_loop or stopped_for_cost or turn_count >= max_agent_turns) and not full_assistant_reply and not getattr(tool_adapter, "approval_card_posted", False):
                    if stopped_for_loop == "repeat":
                        note = "I stopped because I was repeating the same step and getting nowhere. Tell me what you'd like me to do differently, or give me the missing detail."
                    elif stopped_for_loop == "errors":
                        note = "I stopped because my last few steps all failed. Nothing was changed. Please check the connection or details and try again."
                    else:
                        note = (
                            f"I paused because this request reached its cost limit (${cost_cap_usd:.2f}). "
                            if stopped_for_cost
                            else "I paused because this request reached its step limit. "
                        ) + "My work so far is saved. Reply \"continue\" and I'll pick up where I left off."
                    full_assistant_reply = note
                    yield Message(role="assistant", content=note)
                if turn_count >= max_agent_turns and not full_assistant_reply:
                    logger.warning(
                        f"[DEBUG_CLAUDE] Loop EXHAUSTED max_agent_turns ({max_agent_turns}) without producing text response! "
                        f"turn_count={turn_count}, full_assistant_reply_len={len(full_assistant_reply)}"
                    )
                else:
                    logger.info(
                        f"[DEBUG_CLAUDE] Agent loop concluded. turns_used={turn_count}/{max_agent_turns}, "
                        f"final_reply_len={len(full_assistant_reply)}"
                    )

            # Save full messages chain event for telemetry / snapshot recording
            yield Message(
                role="system",
                content={"data": {"full_messages_chain": current_messages}}
            )

            if full_assistant_reply:
                history.append({"role": "assistant", "content": full_assistant_reply})

        except Exception as e:
            logger.error(f"[DEBUG_CLAUDE] Claude agent loop error: {e}", exc_info=True)
            yield Message(
                role="assistant",
                content="Sorry, I couldn't process that request right now. Please try again in a moment.",
            )

        return

    # Fallback to direct streaming when no tools are configured
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": trimmed_messages,
        "stream": True,
    }

    full_assistant_reply = ""

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload
            ) as response:
                if response.status_code != 200:
                    err_body = await response.aread()
                    try:
                        err_json = json.loads(err_body)
                        err_msg = err_json.get("error", {}).get("message", err_body.decode())
                    except Exception:
                        err_msg = err_body.decode()

                    if response.status_code == 404 and "anthropic-workspace-id" in headers and "workspace" in str(err_msg).lower():
                        bad_ws = headers.pop("anthropic-workspace-id", None)
                        if bad_ws:
                            _INVALID_ANTHROPIC_WORKSPACE_IDS.add(bad_ws)
                        logger.warning(f"[DEBUG_CLAUDE] Retrying streaming without invalid workspace-id header '{bad_ws}'...")
                        async with client.stream(
                            "POST",
                            "https://api.anthropic.com/v1/messages",
                            headers=headers,
                            json=payload
                        ) as retry_resp:
                            if retry_resp.status_code == 200:
                                async for line in retry_resp.aiter_lines():
                                    if not line or not line.startswith("data: "):
                                        continue
                                    data_str = line[6:].strip()
                                    if data_str == "[DONE]":
                                        break
                                    try:
                                        event = json.loads(data_str)
                                        if event.get("type") == "content_block_delta":
                                            delta = event.get("delta", {})
                                            if delta.get("type") == "text_delta":
                                                text = delta.get("text", "")
                                                full_assistant_reply += text
                                                yield Message(role="assistant", content=text)
                                    except Exception:
                                        continue
                                return

                    logger.error(f"Anthropic API Error ({response.status_code}): {err_msg}")
                    yield Message(role="assistant", content=f"API Error ({response.status_code}): {err_msg}")
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                        evt_type = event.get("type")
                        if evt_type == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                chunk = delta.get("text", "")
                                full_assistant_reply += chunk
                                yield Message(role="assistant", content=chunk)
                    except Exception:
                        pass

        # Save assistant response to session history & record usage
        if full_assistant_reply:
            history.append({"role": "assistant", "content": full_assistant_reply})

        in_toks = max(1, len(str(trimmed_messages) + (system_prompt or "")) // 4)
        out_toks = max(1, len(full_assistant_reply) // 4)
        try:
            u_res = record_api_usage(
                workspace_id=workspace_id,
                workspace_name=workspace_name,
                channel_id=channel_id,
                channel_name=channel_name,
                user_id=user_id,
                model=model,
                input_tokens=in_toks,
                output_tokens=out_toks,
                key_source=key_source,
            )
            if isinstance(u_res, dict) and u_res.get("status") != "error":
                yield Message(role="system", content={"data": {"usage": u_res}})
        except Exception as usage_err:
            logger.warning(f"[USAGE] Failed to record usage log: {usage_err}")

    except Exception as e:
        logger.error(f"Claude streaming error: {e}")
        yield Message(role="assistant", content=f"Error communicating with Claude: {str(e)}")

