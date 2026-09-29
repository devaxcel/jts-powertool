import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.tools.tool_adapter import (
    ControlledToolAdapter,
    ALLOWED_READONLY_TOOLS,
    BLOCKED_WRITE_TOOLS,
)
from app.tools.mcp_client import GitHubMCPClient


class TestControlledToolAdapter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_client = AsyncMock(spec=GitHubMCPClient)
        self.mock_client.is_configured.return_value = True
        self.adapter = ControlledToolAdapter(mcp_client=self.mock_client)

    def test_allowed_read_and_write_tools(self):
        """Verify that read and write tools are allowed."""
        for tool in ["list_issues", "get_file_contents", "create_issue", "push_files", "create_or_update_file", "create_branch"]:
            self.assertTrue(
                self.adapter.is_tool_allowed(tool),
                f"Tool '{tool}' should be allowed under full read/write policy.",
            )

    def test_blocked_destructive_tools(self):
        """Verify that destructive tools like delete_repository are blocked."""
        for tool in ["delete_repository"]:
            self.assertFalse(
                self.adapter.is_tool_allowed(tool),
                f"Destructive tool '{tool}' must be blocked by Controlled Tool Adapter.",
            )

    def test_anthropic_tools_schema_filtering(self):
        """Verify that approved read and write tools appear in Anthropic tool schemas."""
        allowed_schemas = self.adapter.get_allowed_tools()
        self.assertGreater(len(allowed_schemas), 5)
        schema_names = [s["name"] for s in allowed_schemas]
        self.assertIn("list_issues", schema_names)
        self.assertIn("create_issue", schema_names)
        self.assertIn("create_or_update_file", schema_names)
        self.assertNotIn("delete_repository", schema_names)

    def test_inject_default_repo(self):
        """Verify that default repo is injected when omitted from tool arguments."""
        self.adapter.default_repo = "AbdulAleemDev/jts-powertool"
        
        args = {"state": "open"}
        updated = self.adapter._inject_default_repo(args)
        self.assertEqual(updated["owner"], "AbdulAleemDev")
        self.assertEqual(updated["repo"], "jts-powertool")
        self.assertEqual(updated["state"], "open")

        # Do not overwrite if already provided
        args_with_custom = {"owner": "custom_owner", "repo": "custom_repo"}
        updated_custom = self.adapter._inject_default_repo(args_with_custom)
        self.assertEqual(updated_custom["owner"], "custom_owner")
        self.assertEqual(updated_custom["repo"], "custom_repo")

    async def test_execute_blocked_destructive_tool_intercepted(self):
        """Verify that attempting to execute a destructive tool returns a safety block message."""
        result, is_error = await self.adapter.execute_tool("delete_repository", {"repo": "test"})
        self.assertTrue(is_error)
        self.assertIn("Action blocked by Controlled Tool Adapter", result)
        self.assertIn("Destructive operations", result)
        self.mock_client.execute_tool.assert_not_called()

    async def test_execute_write_tool_direct_execution_when_approval_disabled(self):
        """Verify that a write tool executes directly through MCP when require_approval_for_writes is False."""
        adapter = ControlledToolAdapter(mcp_client=self.mock_client, require_approval_for_writes=False)
        self.mock_client.execute_tool.return_value = "Issue #100 created successfully!"
        result, is_error = await adapter.execute_tool("create_issue", {"title": "New Bug"})
        
        self.assertFalse(is_error)
        self.assertIn("Issue #100", result)
        self.mock_client.execute_tool.assert_called_once()

    @patch("app.tools.tool_adapter.update_pending_approval_message_ts")
    @patch("app.tools.tool_adapter.create_pending_approval")
    @patch("app.tools.tool_adapter.httpx.AsyncClient")
    async def test_execute_write_tool_triggers_slack_approval(
        self, mock_client_cls, mock_create_pending, mock_update_ts
    ):
        """Verify that write tools trigger pending approval card posting when require_approval_for_writes is True."""
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "ts": "1710000000.000100"}
        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_http
        mock_create_pending.return_value = 1

        adapter = ControlledToolAdapter(
            mcp_client=self.mock_client,
            channel_id="C123",
            thread_ts="1710000000.000001",
            user_id="U123",
            slack_token="xoxb-test",
            require_approval_for_writes=True,
        )

        result, is_error = await adapter.execute_tool("create_issue", {"title": "Test Bug", "body": "Details"})
        self.assertFalse(is_error)
        self.assertIn("submitted for human approval", result)
        mock_create_pending.assert_called_once()
        mock_update_ts.assert_called_once()
        self.mock_client.execute_tool.assert_not_called()

    @patch("app.tools.tool_adapter.update_pending_approval_message_ts")
    @patch("app.tools.tool_adapter.create_pending_approval")
    @patch("app.tools.tool_adapter.httpx.AsyncClient")
    async def test_valid_file_creation_creates_pending_approval(
        self, mock_client_cls, mock_create_pending, mock_update_ts
    ):
        """Test Case 1: Valid file creation creates pending approval with full content and posts card."""
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "ts": "1710000000.000100"}
        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_http
        mock_create_pending.return_value = 1

        adapter = ControlledToolAdapter(
            mcp_client=self.mock_client,
            channel_id="C123",
            thread_ts="1710000000.000001",
            user_id="U123",
            slack_token="xoxb-test",
            require_approval_for_writes=True,
        )

        args = {
            "path": "index.html",
            "content": "<!DOCTYPE html><html><body><h1>Login</h1></body></html>",
            "message": "feat: add login page",
        }
        result, is_error = await adapter.execute_tool("create_or_update_file", args)
        self.assertFalse(is_error)
        self.assertIn("submitted for human approval", result)
        mock_create_pending.assert_called_once()
        call_kwargs = mock_create_pending.call_args.kwargs
        self.assertEqual(call_kwargs["tool_name"], "create_or_update_file")
        self.assertEqual(call_kwargs["tool_arguments"]["content"], args["content"])
        self.assertEqual(call_kwargs["tool_arguments"]["path"], "index.html")
        self.assertEqual(call_kwargs["tool_arguments"]["branch"], "main")
        mock_update_ts.assert_called_once()
        self.mock_client.execute_tool.assert_not_called()

    @patch("app.tools.tool_adapter.create_pending_approval")
    @patch("app.tools.tool_adapter.httpx.AsyncClient")
    async def test_missing_or_empty_content_returns_error_to_agent(
        self, mock_client_cls, mock_create_pending
    ):
        """Test Case 2: Missing or whitespace-only content returns error to agent and creates no approval."""
        adapter = ControlledToolAdapter(
            mcp_client=self.mock_client,
            channel_id="C123",
            slack_token="xoxb-test",
            require_approval_for_writes=True,
        )

        # 1. Missing content
        res1, err1 = await adapter.execute_tool("create_or_update_file", {"path": "index.html"})
        self.assertTrue(err1)
        self.assertIn("'content' parameter is required", res1)

        # 2. Empty string content
        res2, err2 = await adapter.execute_tool("create_or_update_file", {"path": "index.html", "content": ""})
        self.assertTrue(err2)
        self.assertIn("'content' parameter is required", res2)

        # 3. Whitespace only content
        res3, err3 = await adapter.execute_tool("create_or_update_file", {"path": "index.html", "content": "   \n  \t "})
        self.assertTrue(err3)
        self.assertIn("'content' parameter is required", res3)

        # 4. Missing path
        res4, err4 = await adapter.execute_tool("create_or_update_file", {"content": "print(1)"})
        self.assertTrue(err4)
        self.assertIn("'path' parameter is required", res4)

        # Ensure no pending approvals or HTTP posts were made for invalid requests
        mock_create_pending.assert_not_called()
        mock_client_cls.assert_not_called()
        self.mock_client.execute_tool.assert_not_called()

    @patch("app.tools.tool_adapter.update_pending_approval_message_ts")
    @patch("app.tools.tool_adapter.create_pending_approval")
    @patch("app.tools.tool_adapter.httpx.AsyncClient")
    async def test_full_missing_content_retry_to_hitl_approval_flow(
        self, mock_client_cls, mock_create_pending, mock_update_ts
    ):
        """
        Tests the exact flow:
        Turn 1: Claude calls create_or_update_file with missing content -> returns tool error to Claude, NO approval.
        Turn 2: Claude generates COMPLETE index.html content -> valid tool call -> creates HITL approval & Slack card.
        """
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "ts": "1710000000.000200"}
        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_http
        mock_create_pending.return_value = 1

        adapter = ControlledToolAdapter(
            mcp_client=self.mock_client,
            channel_id="C_LOGIN_FLOW",
            thread_ts="1710000000.000001",
            user_id="U_ALEEM",
            slack_token="xoxb-test-token",
            require_approval_for_writes=True,
        )

        # 1. Turn 1: Missing content
        turn1_args = {
            "path": "index.html",
            "repo": "AbdulAleemDev/test-repository",
        }
        res1, err1 = await adapter.execute_tool("create_or_update_file", turn1_args)
        self.assertTrue(err1, "Turn 1 must fail with is_error=True")
        self.assertIn("'content' parameter is required", res1)
        self.assertFalse(adapter.approval_card_posted)
        mock_create_pending.assert_not_called()

        # 2. Turn 2: Claude generates complete index.html and provides commit message
        full_html = (
            "<!DOCTYPE html>\n<html lang=\"en\">\n<head><title>Login</title></head>\n"
            "<body><form><input type=\"email\"/><button type=\"submit\">Sign In</button></form></body></html>"
        )
        turn2_args = {
            "path": "index.html",
            "content": full_html,
            "message": "feat: add modern responsive login page with HTML, CSS, and JS",
            "repo": "AbdulAleemDev/test-repository",
        }
        res2, err2 = await adapter.execute_tool("create_or_update_file", turn2_args)
        self.assertFalse(err2, "Turn 2 must succeed with is_error=False")
        self.assertIn("Action proposal for 'create_or_update_file' has been prepared and submitted for human approval", res2)
        self.assertTrue(adapter.approval_card_posted)

        # Verify pending approval was created with complete content and normalized repo
        mock_create_pending.assert_called_once()
        call_kwargs = mock_create_pending.call_args.kwargs
        self.assertEqual(call_kwargs["tool_name"], "create_or_update_file")
        self.assertEqual(call_kwargs["tool_arguments"]["content"], full_html)
        self.assertEqual(call_kwargs["tool_arguments"]["path"], "index.html")
        self.assertEqual(call_kwargs["tool_arguments"]["owner"], "AbdulAleemDev")
        self.assertEqual(call_kwargs["tool_arguments"]["repo"], "test-repository")
        self.assertEqual(call_kwargs["channel_id"], "C_LOGIN_FLOW")
        self.assertEqual(call_kwargs["user_id"], "U_ALEEM")
        mock_update_ts.assert_called_once()


if __name__ == "__main__":
    unittest.main()


