import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.tools.slack_approval_ui import (
    build_approval_card_blocks,
    build_approved_card_blocks,
    build_rejected_card_blocks,
    build_expired_card_blocks,
    build_diff_modal_view,
)
from app.db.repositories import (
    create_pending_approval,
    get_pending_approval,
    claim_approval_for_execution,
    finalize_approval_execution,
    reject_approval,
)


class TestSlackApprovals(unittest.TestCase):
    def test_build_approval_card_blocks(self):
        """Verify that Block Kit approval card contains action buttons and metadata."""
        blocks = build_approval_card_blocks(
            approval_id="appr_12345",
            tool_name="create_or_update_file",
            tool_args={
                "owner": "AbdulAleemDev",
                "repo": "jts-powertool",
                "path": "app/main.py",
                "content": "print('hello')",
                "message": "feat: add greeting",
            },
            default_repo="AbdulAleemDev/jts-powertool",
        )

        block_types = [b["type"] for b in blocks]
        self.assertIn("header", block_types)
        self.assertIn("actions", block_types)
        self.assertIn("context", block_types)

        actions_block = next(b for b in blocks if b["type"] == "actions")
        action_ids = [el["action_id"] for el in actions_block["elements"]]
        self.assertIn("approve_github_action", action_ids)
        self.assertIn("inspect_github_diff", action_ids)
        self.assertIn("reject_github_action", action_ids)

        for el in actions_block["elements"]:
            self.assertEqual(el["value"], "appr_12345")

    def test_build_approved_card_blocks(self):
        """Verify in-place approved card removes buttons and displays audit stamp."""
        blocks = build_approved_card_blocks(
            approval_id="appr_12345",
            tool_name="create_or_update_file",
            tool_args={"owner": "AbdulAleemDev", "repo": "jts-powertool", "path": "app/main.py"},
            approved_by="U12345",
            execution_result="Commit #a1b2c3d created successfully.",
            default_repo="AbdulAleemDev/jts-powertool",
        )

        block_types = [b["type"] for b in blocks]
        self.assertNotIn("actions", block_types)

        all_text = json.dumps(blocks)
        self.assertIn("APPROVED & APPLIED", all_text)
        self.assertIn("<@U12345>", all_text)
        self.assertIn("Commit #a1b2c3d", all_text)

    def test_build_rejected_card_blocks(self):
        """Verify in-place rejected card removes buttons and displays cancelled stamp."""
        blocks = build_rejected_card_blocks(
            approval_id="appr_12345",
            tool_name="create_issue",
            tool_args={"title": "Test Issue"},
            rejected_by="U99999",
            default_repo="AbdulAleemDev/jts-powertool",
        )

        block_types = [b["type"] for b in blocks]
        self.assertNotIn("actions", block_types)

        all_text = json.dumps(blocks)
        self.assertIn("REJECTED", all_text)
        self.assertIn("<@U99999>", all_text)

    def test_build_expired_card_blocks(self):
        """Verify in-place expired card removes buttons and displays expired stamp."""
        blocks = build_expired_card_blocks(
            approval_id="appr_12345",
            tool_name="create_or_update_file",
            tool_args={"owner": "AbdulAleemDev", "repo": "jts-powertool", "path": "index.html"},
            default_repo="AbdulAleemDev/jts-powertool",
        )

        block_types = [b["type"] for b in blocks]
        self.assertNotIn("actions", block_types)

        all_text = json.dumps(blocks)
        self.assertIn("EXPIRED", all_text)
        self.assertIn("This approval request has expired", all_text)

    def test_build_diff_modal_view(self):
        """Verify modal structure for views.open."""
        modal = build_diff_modal_view(
            approval_id="appr_12345",
            tool_name="create_or_update_file",
            tool_args={
                "path": "app/config.py",
                "content": "REDIS_URL = 'redis://localhost:6379'",
            },
        )

        self.assertEqual(modal["type"], "modal")
        self.assertEqual(modal["title"]["text"], "Proposed Changes")
        self.assertGreater(len(modal["blocks"]), 0)


class TestSlackInteractiveEndpoint(unittest.IsolatedAsyncioTestCase):
    @patch("app.slack_router.claim_approval_for_execution")
    @patch("app.slack_router.finalize_approval_execution")
    @patch("app.slack_router.GitHubMCPClient")
    @patch("app.slack_router.httpx.AsyncClient")
    async def test_approve_action_flow(
        self, mock_http_cls, mock_mcp_cls, mock_finalize, mock_claim
    ):
        """Test Case 3: Approve transitions pending -> applying -> applied, executes MCP with stored DB payload."""
        from app.slack_router import slack_interactive
        from fastapi import Request

        stored_payload = {"path": "index.html", "content": "<h1>Production Code</h1>", "message": "feat: login"}
        mock_claim.return_value = (
            "claimed",
            {
                "approval_id": "appr_test_123",
                "channel_id": "C12345",
                "thread_ts": "1788855136.953639",
                "tool_name": "create_or_update_file",
                "tool_arguments": stored_payload,
                "status": "applying",
            }
        )
        mock_finalize.return_value = True

        mock_mcp = AsyncMock()
        mock_mcp.execute_tool.return_value = "Commit #sha123 created successfully!"
        mock_mcp_cls.return_value = mock_mcp

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})
        mock_http_cls.return_value.__aenter__.return_value = mock_client

        payload_data = {
            "type": "block_actions",
            "actions": [{"action_id": "approve_github_action", "value": "appr_test_123"}],
            "user": {"id": "U12345", "name": "Abdul"},
            "channel": {"id": "C12345"},
            "message": {"ts": "1788855200.000100"},
        }
        form_body = f"payload={json.dumps(payload_data)}".encode("utf-8")

        mock_request = AsyncMock(spec=Request)
        mock_request.body.return_value = form_body
        mock_request.headers = {"x-slack-signature": "dummy", "x-slack-request-timestamp": "12345"}

        with patch("app.slack_router.verify_slack_signature", return_value=True):
            response = await slack_interactive(mock_request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("approved", response.body.decode())

        # Verify tool executed using exact stored DB payload
        mock_mcp.execute_tool.assert_called_once_with("create_or_update_file", stored_payload)

        # Verify atomic claim and finalize called
        mock_claim.assert_called_once_with("appr_test_123", "U12345")
        mock_finalize.assert_called_once_with(
            approval_id="appr_test_123",
            success=True,
            execution_result="Commit #sha123 created successfully!",
        )

    @patch("app.slack_router.reject_approval")
    @patch("app.slack_router.GitHubMCPClient")
    @patch("app.slack_router.httpx.AsyncClient")
    async def test_reject_action_flow(
        self, mock_http_cls, mock_mcp_cls, mock_reject
    ):
        """Test Case 4: Reject transitions pending -> rejected, MCP is not called."""
        from app.slack_router import slack_interactive
        from fastapi import Request

        mock_reject.return_value = (
            "rejected",
            {
                "approval_id": "appr_test_reject",
                "channel_id": "C12345",
                "thread_ts": "1788855136.953639",
                "tool_name": "create_issue",
                "tool_arguments": {"title": "Test Issue"},
                "status": "rejected",
            }
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})
        mock_http_cls.return_value.__aenter__.return_value = mock_client

        payload_data = {
            "type": "block_actions",
            "actions": [{"action_id": "reject_github_action", "value": "appr_test_reject"}],
            "user": {"id": "U12345", "name": "Abdul"},
            "channel": {"id": "C12345"},
            "message": {"ts": "1788855200.000100"},
        }
        form_body = f"payload={json.dumps(payload_data)}".encode("utf-8")

        mock_request = AsyncMock(spec=Request)
        mock_request.body.return_value = form_body
        mock_request.headers = {"x-slack-signature": "dummy", "x-slack-request-timestamp": "12345"}

        with patch("app.slack_router.verify_slack_signature", return_value=True):
            response = await slack_interactive(mock_request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("rejected", response.body.decode())

        mock_reject.assert_called_once_with("appr_test_reject", "U12345")
        mock_mcp_cls.assert_not_called()

    @patch("app.slack_router.claim_approval_for_execution")
    @patch("app.slack_router.GitHubMCPClient")
    async def test_double_approval_safely_rejected_idempotent(
        self, mock_mcp_cls, mock_claim
    ):
        """Test Case 5: Double approval second click is safely rejected as already_processed, MCP not called."""
        from app.slack_router import slack_interactive
        from fastapi import Request

        # First user/click is already applying or applied
        mock_claim.return_value = (
            "already_applying",
            {"approval_id": "appr_double_click", "status": "applying"}
        )

        payload_data = {
            "type": "block_actions",
            "actions": [{"action_id": "approve_github_action", "value": "appr_double_click"}],
            "user": {"id": "U99999", "name": "SecondUser"},
            "channel": {"id": "C12345"},
            "message": {"ts": "1788855200.000100"},
        }
        form_body = f"payload={json.dumps(payload_data)}".encode("utf-8")

        mock_request = AsyncMock(spec=Request)
        mock_request.body.return_value = form_body
        mock_request.headers = {"x-slack-signature": "dummy", "x-slack-request-timestamp": "12345"}

        with patch("app.slack_router.verify_slack_signature", return_value=True):
            response = await slack_interactive(mock_request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("already_processed", response.body.decode())
        mock_mcp_cls.assert_not_called()

    @patch("app.slack_router.claim_approval_for_execution")
    @patch("app.slack_router.GitHubMCPClient")
    @patch("app.slack_router.httpx.AsyncClient")
    async def test_expired_approval_rejected_as_expired(
        self, mock_http_cls, mock_mcp_cls, mock_claim
    ):
        """Test Case 8: Expired approval transitions to expired, updates card, MCP is not called."""
        from app.slack_router import slack_interactive
        from fastapi import Request

        mock_claim.return_value = (
            "expired",
            {
                "approval_id": "appr_expired_1",
                "channel_id": "C12345",
                "tool_name": "create_or_update_file",
                "tool_arguments": {"path": "index.html"},
                "status": "expired",
            }
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})
        mock_http_cls.return_value.__aenter__.return_value = mock_client

        payload_data = {
            "type": "block_actions",
            "actions": [{"action_id": "approve_github_action", "value": "appr_expired_1"}],
            "user": {"id": "U12345", "name": "Abdul"},
            "channel": {"id": "C12345"},
            "message": {"ts": "1788855200.000100"},
        }
        form_body = f"payload={json.dumps(payload_data)}".encode("utf-8")

        mock_request = AsyncMock(spec=Request)
        mock_request.body.return_value = form_body
        mock_request.headers = {"x-slack-signature": "dummy", "x-slack-request-timestamp": "12345"}

        with patch("app.slack_router.verify_slack_signature", return_value=True):
            response = await slack_interactive(mock_request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("expired", response.body.decode())
        mock_mcp_cls.assert_not_called()


class TestSlackEventHandling(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.slack_router.record_processed_activity")
    @patch("app.slack_router.enqueue_job")
    @patch("app.slack_router.get_bot_user_id", return_value="B_BOT_ID")
    @patch("app.slack_router.save_conversation_message")
    def test_duplicate_slack_event_deduplicated_by_event_id(
        self, mock_save, mock_get_bot, mock_enqueue, mock_record
    ):
        """Test Case 6: Duplicate Slack event with the same event ID is rejected by deduplication."""
        # First delivery: new activity
        mock_record.return_value = True
        mock_enqueue.return_value = 101

        payload = {
            "event": {
                "type": "app_mention",
                "user": "U12345",
                "text": "<@B_BOT_ID> create a login page",
                "channel": "C12345",
                "ts": "1710000000.000100",
                "client_msg_id": "unique-event-id-123",
            }
        }

        with patch("app.slack_router.verify_slack_signature", return_value=True):
            resp1 = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp1.status_code, 200)
            self.assertIn("enqueued", resp1.text)
            self.assertEqual(mock_enqueue.call_count, 1)

            # Second delivery: record_processed_activity returns False (already processed)
            mock_record.return_value = False
            resp2 = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp2.status_code, 200)
            self.assertIn("duplicate_ignored", resp2.text)
            self.assertEqual(mock_enqueue.call_count, 1)  # No second enqueue

    @patch("app.slack_router.enqueue_job")
    @patch("app.slack_router.record_processed_activity")
    def test_message_changed_event_ignored_no_queue_no_claude(
        self, mock_record, mock_enqueue
    ):
        """Test Case 7: message_changed event (from chat.update) is discarded without enqueueing or invoking Claude."""
        payload = {
            "event": {
                "type": "message",
                "subtype": "message_changed",
                "hidden": True,
                "channel": "D12345",
                "ts": "1710000000.000200",
                "message": {
                    "type": "message",
                    "subtype": "bot_message",
                    "text": "🛡️ GitHub Write Permission — Applied",
                    "ts": "1710000000.000100",
                    "bot_id": "B012345",
                },
            }
        }

        with patch("app.slack_router.verify_slack_signature", return_value=True):
            resp = self.client.post("/api/slack/events", json=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("ignored_subtype_message_changed", resp.text)
        mock_enqueue.assert_not_called()
        mock_record.assert_not_called()


class TestApprovalRepositoryStateMachine(unittest.TestCase):
    @patch("app.db.repositories.get_db_connection")
    def test_claim_approval_for_execution_success(self, mock_get_conn):
        """Verify atomic transition pending -> applying claims successfully."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # First query (expired check) returns None, second query (atomic claim) returns row
        mock_cur.fetchone.side_effect = [
            None,
            {
                "approval_id": "appr_100",
                "tool_name": "create_or_update_file",
                "tool_arguments": '{"path": "index.html", "content": "<h1>Test</h1>"}',
                "status": "applying",
            }
        ]

        status, rec = claim_approval_for_execution("appr_100", "U123")
        self.assertEqual(status, "claimed")
        self.assertEqual(rec["tool_arguments"]["path"], "index.html")
        mock_conn.commit.assert_called()

    @patch("app.db.repositories.get_db_connection")
    def test_claim_approval_for_execution_already_claimed(self, mock_get_conn):
        """Verify second claim attempt detects already_applying or already_applied."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # 1. expired check returns None
        # 2. atomic claim WHERE status='pending' returns None (already claimed)
        # 3. SELECT current status returns status='applying'
        mock_cur.fetchone.side_effect = [
            None,
            None,
            {
                "approval_id": "appr_100",
                "status": "applying",
            }
        ]

        status, rec = claim_approval_for_execution("appr_100", "U999")
        self.assertEqual(status, "already_applying")

    @patch("app.db.repositories.get_db_connection")
    def test_claim_approval_for_execution_detects_expiration(self, mock_get_conn):
        """Verify expired check marks status as expired when expires_at has passed."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # First query (expired update) returns expired row
        mock_cur.fetchone.return_value = {
            "approval_id": "appr_expired",
            "status": "expired",
            "tool_arguments": "{}",
        }

        status, rec = claim_approval_for_execution("appr_expired", "U123")
        self.assertEqual(status, "expired")
        self.assertEqual(rec["status"], "expired")

    @patch("app.db.repositories.get_db_connection")
    def test_finalize_approval_execution(self, mock_get_conn):
        """Verify finalizing transitions applying -> applied."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = {"id": 1}

        success = finalize_approval_execution("appr_100", True, "Done")
        self.assertTrue(success)
        mock_conn.commit.assert_called()

    @patch("app.db.repositories.get_db_connection")
    def test_reject_approval_success(self, mock_get_conn):
        """Verify rejecting transitions pending -> rejected."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # First query (expired check) returns None, second query (atomic reject) returns row
        mock_cur.fetchone.side_effect = [
            None,
            {
                "approval_id": "appr_100",
                "status": "rejected",
                "tool_arguments": "{}",
            }
        ]

        status, rec = reject_approval("appr_100", "U123")
        self.assertEqual(status, "rejected")
        self.assertEqual(rec["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
