import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.claude import stream, Message
from app.tools.tool_adapter import ControlledToolAdapter


class TestClaudeToolLoop(unittest.IsolatedAsyncioTestCase):
    @patch("app.claude.httpx.AsyncClient")
    async def test_claude_agentic_tool_call_flow(self, mock_client_cls):
        """
        Simulates:
        Turn 1: Claude responds with tool_use for list_issues.
        Turn 2: Claude receives tool_result and outputs final natural text.
        """
        # Mock responses from Anthropic API
        turn1_response = MagicMock()
        turn1_response.status_code = 200
        turn1_response.json.return_value = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": "Let me check the open issues for you."},
                {
                    "type": "tool_use",
                    "id": "toolu_123",
                    "name": "list_issues",
                    "input": {"state": "open"},
                },
            ],
        }

        turn2_response = MagicMock()
        turn2_response.status_code = 200
        turn2_response.json.return_value = {
            "stop_reason": "end_turn",
            "content": [
                {
                    "type": "text",
                    "text": "There is currently 1 open issue: #42 Memory bug.",
                }
            ],
        }

        mock_http_client = AsyncMock()
        mock_http_client.post.side_effect = [turn1_response, turn2_response]
        mock_client_cls.return_value.__aenter__.return_value = mock_http_client

        # Mock tool adapter
        mock_adapter = MagicMock(spec=ControlledToolAdapter)
        mock_adapter.get_allowed_tools.return_value = [
            {"name": "list_issues", "description": "List open issues", "input_schema": {}}
        ]
        mock_adapter.execute_tool = AsyncMock(
            return_value=("[Issue #42]: Memory bug (Status: open)", False)
        )

        # Telemetry tracker
        telemetry_events = []

        async def mock_callback(action, tool_name, tool_args, output, is_error):
            telemetry_events.append((action, tool_name, is_error))

        collected_messages = []
        async for msg in stream(
            user_message="What issues are open?",
            system_prompt="You are a helpful assistant.",
            session_id="test-session-uuid",
            model="claude-haiku-4-5-20251001",
            tool_adapter=mock_adapter,
            tool_callback=mock_callback,
        ):
            collected_messages.append(msg)

        # Verify tool adapter executed
        mock_adapter.execute_tool.assert_called_once_with("list_issues", {"state": "open"})

        # Verify telemetry callback events
        self.assertEqual(len(telemetry_events), 2)
        self.assertEqual(telemetry_events[0][0], "TOOL_INVOKED")
        self.assertEqual(telemetry_events[1][0], "TOOL_RESULT_RECEIVED")

        # Verify final message yielded to caller
        assistant_replies = [
            m.content for m in collected_messages
            if m.role == "assistant" and isinstance(m.content, str)
        ]
        self.assertTrue(any("There is currently 1 open issue" in r for r in assistant_replies))

        # Verify full messages chain was yielded for snapshot persistence
        chain_msgs = [
            m.content.get("data", {}).get("full_messages_chain")
            for m in collected_messages
            if m.role == "system" and isinstance(m.content, dict) and "full_messages_chain" in m.content.get("data", {})
        ]
        self.assertTrue(len(chain_msgs) > 0)
        self.assertGreater(len(chain_msgs[0]), 2)  # user prompt, assistant tool_use, user tool_result

    @patch("app.claude.httpx.AsyncClient")
    async def test_claude_no_tool_needed(self, mock_client_cls):
        """
        Simulates Claude answering immediately without needing any tools.
        """
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Hello there! How can I assist you today?"}],
        }

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = response
        mock_client_cls.return_value.__aenter__.return_value = mock_http_client

        mock_adapter = MagicMock(spec=ControlledToolAdapter)
        mock_adapter.get_allowed_tools.return_value = [
            {"name": "list_issues", "description": "...", "input_schema": {}}
        ]
        mock_adapter.execute_tool = AsyncMock()

        collected_messages = []
        async for msg in stream(
            user_message="Hello",
            system_prompt="You are a helpful assistant.",
            session_id="test-session-uuid",
            model="claude-haiku-4-5-20251001",
            tool_adapter=mock_adapter,
        ):
            collected_messages.append(msg)

        # Tool adapter should NOT have been executed
        mock_adapter.execute_tool.assert_not_called()

        assistant_replies = [
            m.content for m in collected_messages
            if m.role == "assistant" and isinstance(m.content, str)
        ]
        self.assertIn("Hello there! How can I assist you today?", assistant_replies)

    @patch("app.claude.httpx.AsyncClient")
    async def test_claude_short_circuits_on_approval_card(self, mock_client_cls):
        """
        Verifies that when a write tool posts an approval card to Slack,
        Claude short-circuits at Turn 1 and does NOT execute a redundant Turn 2.
        """
        turn1_response = MagicMock()
        turn1_response.status_code = 200
        turn1_response.json.return_value = {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_write_999",
                    "name": "create_or_update_file",
                    "input": {
                        "path": "index.html",
                        "content": "<h1>Login</h1>",
                        "message": "feat: add login",
                        "branch": "main",
                    },
                },
            ],
        }

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = turn1_response
        mock_client_cls.return_value.__aenter__.return_value = mock_http_client

        mock_adapter = MagicMock(spec=ControlledToolAdapter)
        mock_adapter.get_allowed_tools.return_value = [
            {"name": "create_or_update_file", "description": "...", "input_schema": {}}
        ]
        mock_adapter.approval_card_posted = False

        async def fake_execute_tool(tool_name, tool_args):
            # Simulates tool adapter posting card and setting approval_card_posted to True
            mock_adapter.approval_card_posted = True
            return ("Action proposal prepared and submitted for approval.", False)

        mock_adapter.execute_tool.side_effect = fake_execute_tool

        collected_messages = []
        async for msg in stream(
            user_message="Create index.html with login page",
            system_prompt="You are a helpful assistant.",
            session_id="test-session-uuid",
            model="claude-haiku-4-5-20251001",
            tool_adapter=mock_adapter,
        ):
            collected_messages.append(msg)

        # Assert tool was invoked
        mock_adapter.execute_tool.assert_called_once()
        # Assert Anthropic API was called ONLY ONCE (Turn 1 only, Turn 2 was short-circuited!)
        self.assertEqual(mock_http_client.post.call_count, 1)

        # Assert full messages chain was still yielded
        chain_msgs = [
            m.content.get("data", {}).get("full_messages_chain")
            for m in collected_messages
            if m.role == "system" and isinstance(m.content, dict) and "full_messages_chain" in m.content.get("data", {})
        ]
        self.assertTrue(len(chain_msgs) > 0)


if __name__ == "__main__":
    unittest.main()
