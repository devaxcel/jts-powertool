import json
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app


class TestApiApprovals(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.api_approvals.get_system_stats")
    def test_get_stats(self, mock_stats):
        mock_stats.return_value = {
            "jobs": {"total": 5, "pending": 1, "completed": 4},
            "approvals": {"total": 2, "pending": 1, "applied": 1},
            "conversations": 12,
            "snapshots": 8,
        }
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["jobs"]["total"], 5)
        self.assertEqual(data["approvals"]["pending"], 1)

    @patch("app.api_approvals.get_all_approvals")
    def test_list_approvals(self, mock_all):
        mock_all.return_value = [
            {
                "id": 1,
                "approval_id": "appr_test123",
                "tool_name": "create_or_update_file",
                "tool_arguments": {"path": "index.html", "content": "<h1>Hello</h1>"},
                "status": "pending",
                "created_at": "2026-09-10T12:00:00",
            }
        ]
        resp = self.client.get("/api/approvals")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("approvals", data)
        self.assertEqual(len(data["approvals"]), 1)
        self.assertIn("+<h1>Hello</h1>", data["approvals"][0]["diff_preview"])

    @patch("app.api_approvals.get_pending_approval")
    def test_get_approval_details(self, mock_get):
        mock_get.return_value = {
            "id": 1,
            "approval_id": "appr_test123",
            "tool_name": "create_or_update_file",
            "tool_arguments": {"path": "test.txt", "content": "line1\nline2"},
            "status": "pending",
        }
        resp = self.client.get("/api/approvals/appr_test123")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("+line1", data["approval"]["diff_preview"])

    @patch("app.api_approvals.finalize_approval_execution")
    @patch("app.api_approvals.claim_approval_for_execution")
    @patch("app.api_approvals.GitHubMCPClient")
    def test_approve_action_flow(self, mock_mcp_cls, mock_claim, mock_finalize):
        mock_claim.return_value = (
            "claimed",
            {
                "approval_id": "appr_test123",
                "tool_name": "create_or_update_file",
                "tool_arguments": {"path": "test.py", "content": "print('ok')"},
                "channel_id": None,
                "message_ts": None,
            },
        )
        mock_mcp = AsyncMock()
        mock_mcp.execute_tool.return_value = "File test.py committed successfully to main."
        mock_mcp_cls.return_value = mock_mcp

        resp = self.client.post("/api/approvals/appr_test123/action", json={"action": "approve"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "applied")
        mock_finalize.assert_called_once_with(
            approval_id="appr_test123",
            success=True,
            execution_result="File test.py committed successfully to main.",
        )

    @patch("app.api_approvals.reject_approval")
    def test_reject_action_flow(self, mock_reject):
        mock_reject.return_value = (
            "rejected",
            {
                "approval_id": "appr_test123",
                "channel_id": None,
                "message_ts": None,
            },
        )
        resp = self.client.post("/api/approvals/appr_test123/action", json={"action": "reject"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
