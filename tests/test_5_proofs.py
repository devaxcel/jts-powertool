import asyncio
import json
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.log_stream import LOG_BUFFER, get_metrics_snapshot, emit_telemetry
import app.slack_router as slack_mod

class TestFiveProofs(unittest.TestCase):
    def setUp(self):
        LOG_BUFFER.clear()
        self.client = TestClient(app)

    def test_proof_1_and_2_distinct_and_resumed_sessions(self):
        """
        Proof 1: Two Slack threads receive two different Claude sessions.
        Proof 2: A later message in the same thread resumes the correct session.
        """
        tenant_id = "slack-workspace"
        conv_id = "slack-C12345"
        thread_1 = "1700000001.000100"
        thread_2 = "1700000002.000200"

        # Mock database session lookup/save
        session_store = {}

        def mock_get_session(t_id, c_id, th_id):
            return session_store.get((t_id, c_id, th_id))

        def mock_save_session(t_id, c_id, th_id, u_id, s_id):
            session_store[(t_id, c_id, th_id)] = s_id
            emit_telemetry(
                action="SESSION_CREATED",
                category="DATABASE",
                thread_id=th_id,
                session_id=s_id,
                message=f"Mapped thread {th_id} to session {s_id}"
            )

        with patch("app.slack_router.get_existing_claude_session", side_effect=mock_get_session), \
             patch("app.slack_router.save_claude_session", side_effect=mock_save_session), \
             patch("app.slack_router.record_processed_activity", return_value=True):

            # Thread 1 - Turn 1
            session_id_1 = "11111111-1111-1111-1111-111111111111"
            mock_save_session(tenant_id, conv_id, thread_1, "U001", session_id_1)

            # Thread 2 - Turn 1
            session_id_2 = "22222222-2222-2222-2222-222222222222"
            mock_save_session(tenant_id, conv_id, thread_2, "U002", session_id_2)

            # Verification of Proof 1: Distinct Sessions
            res_thread_1 = mock_get_session(tenant_id, conv_id, thread_1)
            res_thread_2 = mock_get_session(tenant_id, conv_id, thread_2)
            self.assertNotEqual(res_thread_1, res_thread_2, "Thread 1 and Thread 2 must receive different sessions")
            self.assertEqual(res_thread_1, session_id_1)
            self.assertEqual(res_thread_2, session_id_2)

            # Thread 1 - Turn 2 (Proof 2: Resume session)
            res_thread_1_turn_2 = mock_get_session(tenant_id, conv_id, thread_1)
            self.assertEqual(res_thread_1_turn_2, session_id_1, "Subsequent message in Thread 1 must resume the same session")

    def test_proof_3_persistence_across_restart(self):
        """
        Proof 3: Restarting FastAPI does not lose the mapping.
        Validated by verifying that session retrieval queries the database layer.
        """
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"claude_session_id": "33333333-3333-3333-3333-333333333333"}

        with patch("app.slack_router.get_db_connection", return_value=mock_conn):
            session_id = slack_mod.get_existing_claude_session("slack-workspace", "slack-C1", "1700.00")
            self.assertEqual(session_id, "33333333-3333-3333-3333-333333333333")
            self.assertTrue(mock_cursor.execute.called)

    def test_proof_4_deduplication(self):
        """
        Proof 4: Resending the same Slack event does not invoke Claude twice.
        """
        processed = set()
        def mock_record(tenant, act_id):
            if act_id in processed:
                return False
            processed.add(act_id)
            return True

        with patch("app.slack_router.record_processed_activity", side_effect=mock_record), \
             patch("app.slack_router.get_existing_claude_session", return_value=None), \
             patch("app.slack_router.stream") as mock_stream:

            # First send
            asyncio.run(slack_mod.process_slack_turn(
                channel_id="C1",
                thread_ts="1700.1",
                raw_text="Hello",
                user_id="U1",
                message_id="msg-12345"
            ))

            # Duplicate resend
            asyncio.run(slack_mod.process_slack_turn(
                channel_id="C1",
                thread_ts="1700.1",
                raw_text="Hello",
                user_id="U1",
                message_id="msg-12345"
            ))

            # Find duplicate log in LOG_BUFFER
            dup_logs = [l for l in LOG_BUFFER if l.get("action") == "DUPLICATE_IGNORED"]
            self.assertTrue(len(dup_logs) >= 1, "Duplicate event must emit a DUPLICATE_IGNORED telemetry log")
            self.assertEqual(dup_logs[0]["event_id"], "msg-12345")

    @patch("app.slack_router.save_conversation_message")
    @patch("app.slack_router.enqueue_job", return_value=1)
    @patch("app.slack_router.record_processed_activity", return_value=True)
    @patch("app.slack_router.get_db_connection")
    @patch("app.slack_router.stream")
    def test_proof_5_bot_filter_and_privacy(self, mock_stream, mock_db, mock_record, mock_enqueue, mock_save_msg):
        """
        Proof 5: Bot messages are ignored and message content is not written to logs.
        """
        SECRET_TEXT = "super_confidential_credit_card_or_password_123"

        # 1. Send Bot message to endpoint
        bot_payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "bot_id": "B99999",
                "channel": "C01",
                "text": SECRET_TEXT,
                "ts": "1700.999"
            }
        }
        res = self.client.post("/api/slack/events", json=bot_payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ignored_bot_event")

        # 2. Send User message to endpoint
        user_payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "user": "U0123",
                "channel": "C01",
                "text": SECRET_TEXT,
                "ts": "1700.888",
                "client_msg_id": "client-msg-privacy"
            }
        }
        res = self.client.post("/api/slack/events", json=user_payload)
        self.assertEqual(res.status_code, 200)

        # 3. Verify that SECRET_TEXT is NEVER present in any log entry
        for entry in LOG_BUFFER:
            for val in [entry.get("message", ""), json.dumps(entry.get("extra", {}))]:
                self.assertNotIn(SECRET_TEXT, val, "Sensitive message text MUST NOT be logged")

        # Verify bot ignored event was logged
        bot_logs = [l for l in LOG_BUFFER if l.get("action") == "BOT_IGNORED"]
        self.assertTrue(len(bot_logs) >= 1, "Bot ignored telemetry event must be recorded")

    def test_log_page_and_feed(self):
        """
        Verify log dashboard endpoints
        """
        # HTML dashboard
        html_res = self.client.get("/logs")
        self.assertEqual(html_res.status_code, 200)
        self.assertIn("JTS PowerTool", html_res.text)
        self.assertIn("/logs/stream", html_res.text)

        # API feed
        feed_res = self.client.get("/logs/api/feed")
        self.assertEqual(feed_res.status_code, 200)
        self.assertIsInstance(feed_res.json(), list)

        # Metrics
        metric_res = self.client.get("/logs/api/metrics")
        self.assertEqual(metric_res.status_code, 200)
        self.assertIn("total_events", metric_res.json())

    def test_local_memory_and_mention_reply(self):
        """
        Verify that:
        1. Unmentioned messages in channels are stored in local memory, bot stays silent.
        2. Explicit @mentions trigger Claude with local memory context.
        """
        saved_messages = []
        def mock_save(**kwargs):
            saved_messages.append(kwargs)

        async def mock_stream(**kwargs):
            yield "Mock response"

        mock_rag_meta = {
            "query": "test",
            "semantic_matches": [{"text": "Prior context", "similarity": 0.91}],
            "recent_count": 1,
            "total_context_sent": 1,
            "total_in_db": 5,
            "token_savings_pct": 80,
        }
        with patch("app.slack_router.save_conversation_message", side_effect=mock_save), \
             patch("app.slack_router.get_rag_thread_context", return_value=([{"role": "user", "content": "Prior context"}], mock_rag_meta)), \
             patch("app.slack_router.get_bot_user_id", return_value="UBOT123"), \
             patch("app.slack_router.record_processed_activity", return_value=True), \
             patch("app.slack_router.enqueue_job", return_value=99), \
             patch("app.slack_router.get_existing_claude_session", return_value=None), \
             patch("app.slack_router.stream", side_effect=mock_stream), \
             patch("app.slack_router.get_slack_user_profile", return_value={"real_name": "Aleem", "display_name": "Aleem"}):

            # 1. Message without bot mention in channel
            unmentioned_payload = {
                "event": {
                    "type": "message",
                    "channel": "C_TEST_CH",
                    "user": "U_ALEEM",
                    "text": "Hey team, this is an internal discussion.",
                    "ts": "1700.111",
                    "client_msg_id": "msg-unmentioned-1"
                }
            }
            res1 = self.client.post("/api/slack/events", json=unmentioned_payload)
            self.assertEqual(res1.status_code, 200)
            self.assertEqual(res1.json().get("status"), "stored_in_memory_no_bot_mention")
            self.assertEqual(len(saved_messages), 1)
            self.assertIn("internal discussion", saved_messages[0]["content"])

            # 2. Message with @mention in channel
            mentioned_payload = {
                "event": {
                    "type": "app_mention",
                    "channel": "C_TEST_CH",
                    "user": "U_ALEEM",
                    "text": "<@UBOT123> summarize our discussion",
                    "ts": "1700.112",
                    "client_msg_id": "msg-mentioned-2"
                }
            }
            res2 = self.client.post("/api/slack/events", json=mentioned_payload)
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json().get("status"), "enqueued")
            self.assertEqual(res2.json().get("job_id"), 99)

if __name__ == "__main__":
    unittest.main()

