import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.db.repositories import (
    save_context_snapshot,
    get_latest_context_snapshot,
    get_context_snapshot_history,
)


class TestContextRouter(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_context_page_html(self):
        response = self.client.get("/context")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Claude Context Inspector", response.text)
        self.assertIn("messagesContainer", response.text)

    def test_api_latest_empty(self):
        with patch("app.context_router.get_latest_context_snapshot", return_value=None):
            response = self.client.get("/context/api/latest")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIsNone(data["snapshot"])

    def test_api_latest_with_data(self):
        mock_snapshot = {
            "id": 1,
            "channel_id": "C123",
            "thread_ts": "1700.1",
            "user_id": "U123",
            "user_name": "Abdul Aleem",
            "session_id": "sess-123",
            "model": "claude-haiku",
            "prompt_text": "now generate a pdf of this summary",
            "system_prompt": "You are a helpful assistant.",
            "messages_sent": [
                {"role": "assistant", "speaker": "Bot", "content": "Prior summary content"},
                {"role": "user", "speaker": "Abdul Aleem", "content": "now generate a pdf of this summary"},
            ],
            "files_included": ["sample.docx"],
            "context_count": 2,
            "created_at": "2026-09-08T10:00:00Z",
        }
        with patch("app.context_router.get_latest_context_snapshot", return_value=mock_snapshot):
            response = self.client.get("/context/api/latest")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIsNotNone(data["snapshot"])
            self.assertEqual(data["snapshot"]["user_name"], "Abdul Aleem")
            self.assertEqual(len(data["snapshot"]["messages_sent"]), 2)

    def test_api_history(self):
        mock_history = [
            {"id": 2, "channel_id": "C123", "user_name": "Abdul Aleem", "prompt_text": "generate pdf", "context_count": 2, "created_at": "2026-09-08T10:01:00Z"},
            {"id": 1, "channel_id": "C123", "user_name": "Abdul Aleem", "prompt_text": "hello", "context_count": 1, "created_at": "2026-09-08T10:00:00Z"},
        ]
        with patch("app.context_router.get_context_snapshot_history", return_value=mock_history):
            response = self.client.get("/context/api/history")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(len(data["history"]), 2)


if __name__ == "__main__":
    unittest.main()
