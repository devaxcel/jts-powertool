import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.slack_router import verify_slack_signature
import app.db.repositories as repo

class TestJobQueueAndWorker(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_signature_verification(self):
        # With empty signing secret, defaults to True for local testing
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": ""}):
            self.assertTrue(verify_slack_signature(b"{}", {}))

    def test_slack_event_channel_mention_does_not_reply_in_thread(self):
        with patch("app.slack_router.record_processed_activity", return_value=True), \
             patch("app.slack_router.enqueue_job", return_value=42) as mock_enqueue, \
             patch("app.slack_router.get_bot_user_id", return_value="BOT123"), \
             patch("app.slack_router.save_conversation_message"):
            
            payload = {
                "event": {
                    "type": "app_mention",
                    "user": "U12345",
                    "text": "<@BOT123> help me with python",
                    "channel": "C12345",
                    "ts": "1700000000.123456",
                    "client_msg_id": "msg-uuid-999"
                }
            }

            resp = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp.status_code, 200)
            mock_enqueue.assert_called_once()
            call_kwargs = mock_enqueue.call_args.kwargs
            self.assertFalse(call_kwargs["payload"]["reply_in_thread"], "Top-level channel mentions must NOT reply in thread")

    def test_slack_event_threaded_mention_replies_in_thread(self):
        with patch("app.slack_router.record_processed_activity", return_value=True), \
             patch("app.slack_router.enqueue_job", return_value=43) as mock_enqueue, \
             patch("app.slack_router.get_bot_user_id", return_value="BOT123"), \
             patch("app.slack_router.save_conversation_message"):
            
            payload = {
                "event": {
                    "type": "app_mention",
                    "user": "U12345",
                    "text": "<@BOT123> help me with python",
                    "channel": "C12345",
                    "thread_ts": "1700000000.000111",
                    "ts": "1700000000.123456",
                    "client_msg_id": "msg-uuid-999"
                }
            }

            resp = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp.status_code, 200)
            mock_enqueue.assert_called_once()
            call_kwargs = mock_enqueue.call_args.kwargs
            self.assertTrue(call_kwargs["payload"]["reply_in_thread"], "Mentions inside threads MUST reply in thread")

    def test_duplicate_event_rejection(self):
        with patch("app.slack_router.record_processed_activity", return_value=False):
            payload = {
                "event": {
                    "type": "app_mention",
                    "user": "U12345",
                    "text": "<@BOT123> repeat message",
                    "channel": "C12345",
                    "ts": "1700000000.123456",
                    "client_msg_id": "duplicate-msg-id"
                }
            }

            resp = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("status"), "duplicate_ignored")

    @patch("app.worker.get_slack_user_profile", return_value={"real_name": "Alice"})
    @patch("app.worker.save_conversation_message")
    @patch("app.worker.mark_job_completed")
    @patch("app.worker.get_thread_context_since_last_reply", return_value=([], {"has_prior_bot_reply": False}))
    @patch("app.worker.stream")
    @patch("app.worker.httpx.AsyncClient")
    def test_worker_saves_bot_message_ts(self, mock_client_cls, mock_stream, mock_context, mock_mark_done, mock_save_msg, mock_profile):
        import asyncio
        from app.worker import process_job

        async def mock_generator(*args, **kwargs):
            yield "Hello, here is your answer."

        mock_stream.side_effect = mock_generator

        # Mock Slack postMessage response
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "ts": "1788849999.000123"}

        async def mock_post(*args, **kwargs):
            return mock_resp

        mock_client.__aenter__.return_value.post = mock_post
        mock_client_cls.return_value = mock_client

        job = {
            "id": 99,
            "event_id": "test-event-99",
            "team_id": "T123",
            "channel_id": "C123",
            "thread_ts": "1788849000.111111",
            "user_id": "U123",
            "payload": {
                "raw_text": "What is 2+2?",
                "message_id": "msg-99",
                "reply_in_thread": True,
                "user_display": "Alice",
                "files": []
            }
        }

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-fake"}):
            asyncio.run(process_job(job))

        mock_save_msg.assert_called_once()
        save_kwargs = mock_save_msg.call_args.kwargs
        self.assertEqual(save_kwargs.get("message_ts"), "1788849999.000123")
        self.assertEqual(save_kwargs.get("role"), "assistant")

    def test_bot_filtered_event_uses_channel_thread_ts(self):
        with patch("app.slack_router.emit_telemetry") as mock_emit:
            payload = {
                "event": {
                    "type": "message",
                    "channel": "C999888",
                    "bot_id": "B0BS3ECDJK1",
                    "ts": "1788865546.563019",
                    "text": "Bot reply text"
                }
            }
            resp = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp.status_code, 200)
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs
            self.assertEqual(call_kwargs["action"], "BOT_IGNORED")
            self.assertEqual(call_kwargs["thread_id"], "channel_C999888")

    def test_duplicate_ignored_uses_channel_thread_ts(self):
        with patch("app.slack_router.record_processed_activity", return_value=False), \
             patch("app.slack_router.emit_telemetry") as mock_emit:
            payload = {
                "event": {
                    "type": "message",
                    "user": "U12345",
                    "channel": "C999888",
                    "ts": "1788865543.705629",
                    "client_msg_id": "dup-1"
                }
            }
            resp = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp.status_code, 200)
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs
            self.assertEqual(call_kwargs["action"], "DUPLICATE_IGNORED")
            self.assertEqual(call_kwargs["thread_id"], "channel_C999888")

    @patch("app.worker.get_slack_user_profile", return_value={"real_name": "Alice"})
    @patch("app.worker.save_conversation_message")
    @patch("app.worker.mark_job_completed")
    @patch("app.worker.get_thread_context_since_last_reply", return_value=([], {"has_prior_bot_reply": False}))
    @patch("app.worker.stream")
    @patch("app.worker.httpx.AsyncClient")
    def test_worker_cleans_up_thinking_message_when_approval_card_posted(
        self, mock_client_cls, mock_stream, mock_context, mock_mark_done, mock_save_msg, mock_profile
    ):
        import asyncio
        from app.worker import process_job

        posted_urls = []

        async def mock_generator(*args, **kwargs):
            # Simulate tool adapter posting approval card during stream
            adapter = kwargs.get("tool_adapter")
            if adapter:
                adapter.approval_card_posted = True
            yield ""

        mock_stream.side_effect = mock_generator

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "ts": "1788849999.000999"}

        async def mock_post(url, *args, **kwargs):
            posted_urls.append((url, kwargs.get("json", {})))
            return mock_resp

        mock_client.__aenter__.return_value.post = mock_post
        mock_client_cls.return_value = mock_client

        job = {
            "id": 100,
            "event_id": "test-event-100",
            "team_id": "T123",
            "channel_id": "C123",
            "thread_ts": "1788849000.111111",
            "user_id": "U123",
            "payload": {
                "raw_text": "Create index.html",
                "message_id": "msg-100",
                "reply_in_thread": True,
                "user_display": "Alice",
                "files": [],
            },
        }

        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-fake"}):
            asyncio.run(process_job(job))

        # Check that postMessage was called for initial thinking message
        urls = [u[0] for u in posted_urls]
        self.assertIn("https://slack.com/api/chat.postMessage", urls)
        # Check that chat.delete was called to remove the thinking message
        self.assertIn("https://slack.com/api/chat.delete", urls)
        delete_call = next(u for u in posted_urls if u[0] == "https://slack.com/api/chat.delete")
        self.assertEqual(delete_call[1]["channel"], "C123")
        self.assertEqual(delete_call[1]["ts"], "1788849999.000999")


if __name__ == "__main__":
    unittest.main()

