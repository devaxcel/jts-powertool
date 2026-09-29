import unittest
from unittest.mock import patch, MagicMock
from app.memory_manager import get_thread_context_since_last_reply

class TestThreadContext(unittest.TestCase):

    def test_context_without_prior_bot_reply(self):
        """
        When the bot has never replied in the thread, all messages so far are returned.
        """
        mock_messages = [
            {"id": 1, "role": "user", "content": "Hello team", "user_name": "Alice", "user_id": "U1"},
            {"id": 2, "role": "user", "content": "Let's discuss the API design", "user_name": "Bob", "user_id": "U2"},
            {"id": 3, "role": "user", "content": "@jpt what is your thought?", "user_name": "Alice", "user_id": "U1"},
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # First query (last bot reply) returns None, second query returns all messages
        mock_cursor.fetchone.return_value = None
        mock_cursor.fetchall.return_value = mock_messages

        with patch("app.memory_manager.get_db_connection", return_value=mock_conn):
            formatted_messages, meta = get_thread_context_since_last_reply("C123", "1700.100")
            
            self.assertFalse(meta["has_prior_bot_reply"])
            self.assertEqual(len(formatted_messages), 3)
            self.assertEqual(formatted_messages[0]["content"], "[Alice]: Hello team")
            self.assertEqual(formatted_messages[1]["content"], "[Bob]: Let's discuss the API design")
            self.assertEqual(formatted_messages[2]["content"], "[Alice]: @jpt what is your thought?")

    def test_context_with_prior_bot_reply_slices_correctly(self):
        """
        When the bot previously replied, context starts from that bot reply and includes all subsequent messages.
        """
        # Bot's last reply was id=4, subsequent discussion id=5, 6, 7
        mock_sliced_messages = [
            {"id": 4, "role": "assistant", "content": "I previously suggested using PostgreSQL.", "user_name": "Bot", "user_id": "bot"},
            {"id": 5, "role": "user", "content": "I agree with that proposal", "user_name": "Bob", "user_id": "U2"},
            {"id": 6, "role": "user", "content": "What about indexing?", "user_name": "Charlie", "user_id": "U3"},
            {"id": 7, "role": "user", "content": "@jpt can you give us an indexing plan?", "user_name": "Alice", "user_id": "U1"},
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # First query returns last bot reply (id=4), second query returns the slice starting from id=4
        mock_cursor.fetchone.return_value = {"id": 4}
        mock_cursor.fetchall.return_value = mock_sliced_messages

        with patch("app.memory_manager.get_db_connection", return_value=mock_conn):
            formatted_messages, meta = get_thread_context_since_last_reply("C123", "1700.100")
            
            self.assertTrue(meta["has_prior_bot_reply"])
            self.assertEqual(len(formatted_messages), 4)
            # First item in context is the bot's prior response
            self.assertEqual(formatted_messages[0]["role"], "assistant")
            self.assertEqual(formatted_messages[0]["content"], "I previously suggested using PostgreSQL.")
            # Subsequent messages are all the human turns
            self.assertEqual(formatted_messages[1]["role"], "user")
            self.assertEqual(formatted_messages[1]["content"], "[Bob]: I agree with that proposal")
            self.assertEqual(formatted_messages[2]["content"], "[Charlie]: What about indexing?")
            self.assertEqual(formatted_messages[3]["content"], "[Alice]: @jpt can you give us an indexing plan?")

    def test_context_thread_started_on_bot_message(self):
        """
        When a user creates a Slack thread by clicking 'Reply in thread' on a bot message,
        the bot message has message_ts == thread_ts, and user replies have thread_ts == thread_ts.
        The context must include the bot message as Turn 1, followed by user replies.
        """
        bot_root_ts = "1788849221.951039"
        mock_thread_messages = [
            {"id": 142, "role": "assistant", "content": "I am JTS Powertool. How can I help?", "user_name": "Bot", "user_id": "bot"},
            {"id": 143, "role": "user", "content": "give me first line of your last reply", "user_name": "Abdul Aleem", "user_id": "U1"},
            {"id": 144, "role": "user", "content": "give me the last line of your last reply", "user_name": "Abdul Aleem", "user_id": "U1"},
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Bot row query finds id=142
        mock_cursor.fetchone.return_value = {"id": 142}
        mock_cursor.fetchall.return_value = mock_thread_messages

        with patch("app.memory_manager.get_db_connection", return_value=mock_conn):
            formatted_messages, meta = get_thread_context_since_last_reply("C08MV3EM9PY", bot_root_ts)

            self.assertTrue(meta["has_prior_bot_reply"])
            self.assertEqual(len(formatted_messages), 3)
            self.assertEqual(formatted_messages[0]["role"], "assistant")
            self.assertEqual(formatted_messages[0]["content"], "I am JTS Powertool. How can I help?")
            self.assertEqual(formatted_messages[1]["role"], "user")
            self.assertEqual(formatted_messages[1]["content"], "[Abdul Aleem]: give me first line of your last reply")
            self.assertEqual(formatted_messages[2]["role"], "user")
            self.assertEqual(formatted_messages[2]["content"], "[Abdul Aleem]: give me the last line of your last reply")

    def test_context_legacy_thread_fallback_to_preceding_bot_message(self):
        """
        Fallback for legacy threads: if no bot reply matches (thread_ts or message_ts),
        it inspects if (min_id - 1) is an assistant message and anchors the context to it.
        """
        bot_root_ts = "1788849221.951039"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # 1. First fetchone (has_msg_col check): (1,)
        # 2. Second fetchone (assistant search): None
        # 3. Third fetchone (min_id search): {"first_id": 143}
        # 4. Fourth fetchone (row 142 assistant check): {"id": 142, "role": "assistant"}
        mock_cursor.fetchone.side_effect = [
            (1,),
            None,
            {"first_id": 143},
            {"id": 142, "role": "assistant"},
        ]

        mock_thread_messages = [
            {"id": 142, "role": "assistant", "content": "I am JTS Powertool. How can I help?", "user_name": "Bot", "user_id": "bot"},
            {"id": 143, "role": "user", "content": "give me first line of your last reply", "user_name": "Abdul Aleem", "user_id": "U1"},
            {"id": 144, "role": "user", "content": "give me the last line of your last reply", "user_name": "Abdul Aleem", "user_id": "U1"},
        ]
        mock_cursor.fetchall.return_value = mock_thread_messages

        with patch("app.memory_manager.get_db_connection", return_value=mock_conn):
            formatted_messages, meta = get_thread_context_since_last_reply("C08MV3EM9PY", bot_root_ts)

            self.assertTrue(meta["has_prior_bot_reply"])
            self.assertEqual(len(formatted_messages), 3)
            self.assertEqual(formatted_messages[0]["role"], "assistant")
            self.assertEqual(formatted_messages[0]["content"], "I am JTS Powertool. How can I help?")


    def test_multi_turn_accumulative_chain_preserves_all_history(self):
        """
        Verify that multi-turn history is fully preserved and not truncated at the last bot reply.
        Turn 1 (Alice + Bot), Intervening (Bob + Charlie), Turn 2 (Alice + Bot), Intervening (Bob), Turn 3 (Alice).
        All 8 messages should be returned in the context pool.
        """
        mock_messages = [
            {"id": 1, "role": "user", "content": "What is Python?", "user_name": "Alice", "user_id": "U1"},
            {"id": 2, "role": "assistant", "content": "Python is a dynamic programming language.", "user_name": "Bot", "user_id": "bot"},
            {"id": 3, "role": "user", "content": "We also use TypeScript.", "user_name": "Bob", "user_id": "U2"},
            {"id": 4, "role": "user", "content": "And Go for backend.", "user_name": "Charlie", "user_id": "U3"},
            {"id": 5, "role": "user", "content": "@jpt what backend framework should we use?", "user_name": "Alice", "user_id": "U1"},
            {"id": 6, "role": "assistant", "content": "FastAPI is excellent for Python, and Gin is great for Go.", "user_name": "Bot", "user_id": "bot"},
            {"id": 7, "role": "user", "content": "Let's stick with FastAPI.", "user_name": "Bob", "user_id": "U2"},
            {"id": 8, "role": "user", "content": "@jpt give me a starter template.", "user_name": "Alice", "user_id": "U1"},
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # has_msg_col check returns True, bot query returns id=6, all messages query returns all 8 rows
        mock_cursor.fetchone.side_effect = [(1,), {"id": 6}]
        mock_cursor.fetchall.return_value = mock_messages

        with patch("app.memory_manager.get_db_connection", return_value=mock_conn):
            formatted_messages, meta = get_thread_context_since_last_reply("C123", "1700.100", reply_in_thread=True)

            self.assertTrue(meta["has_prior_bot_reply"])
            self.assertGreaterEqual(meta["prior_bot_replies_count"], 1)
            self.assertEqual(meta["intervening_human_count"], 2)  # id 7 and id 8
            self.assertGreaterEqual(len(formatted_messages), 6)
            # Verify messages are retrieved
            self.assertTrue(len(formatted_messages) > 0)
            self.assertIn("content", formatted_messages[0])

    def test_build_claude_messages_payload_strict_alternation(self):
        """
        Verify that build_claude_messages_payload aggregates intervening human chatter
        and creates strictly alternating [user, assistant, user, assistant, user] turns.
        """
        from app.claude import build_claude_messages_payload

        context_messages = [
            {"role": "user", "content": "[Alice]: What is Python?", "user_name": "Alice"},
            {"role": "assistant", "content": "Python is a dynamic language.", "user_name": "Bot"},
            {"role": "user", "content": "[Bob]: We also use TypeScript.", "user_name": "Bob"},
            {"role": "user", "content": "[Charlie]: And Go for backend.", "user_name": "Charlie"},
            {"role": "user", "content": "[Alice]: what backend framework?", "user_name": "Alice"},
            {"role": "assistant", "content": "FastAPI or Gin.", "user_name": "Bot"},
            {"role": "user", "content": "[Bob]: Let's stick with FastAPI.", "user_name": "Bob"},
            {"role": "user", "content": "[Alice]: give me a starter template.", "user_name": "Alice"},
        ]

        payload = build_claude_messages_payload(context_messages, "[Alice]: give me a starter template.")

        # Should produce exactly 5 strictly alternating turns:
        # Turn 1 (user): Alice
        # Turn 2 (assistant): Bot
        # Turn 3 (user): Bob + Charlie + Alice merged
        # Turn 4 (assistant): Bot
        # Turn 5 (user): Bob (intervening) + Alice (current prompt) merged
        self.assertEqual(len(payload), 5)
        self.assertEqual([m["role"] for m in payload], ["user", "assistant", "user", "assistant", "user"])

        # Check Turn 3 merged human messages
        self.assertIn("[Bob]: We also use TypeScript.", payload[2]["content"])
        self.assertIn("[Charlie]: And Go for backend.", payload[2]["content"])
        self.assertIn("[Alice]: what backend framework?", payload[2]["content"])

        # Check Turn 5 merged intervening human message and current prompt
        self.assertIn("[Bob]: Let's stick with FastAPI.", payload[4]["content"])
        self.assertIn("[Alice]: give me a starter template.", payload[4]["content"])

    def test_build_claude_messages_payload_thread_started_on_bot_message(self):
        """
        If the thread started on a bot message, ensure Anthropic API requirement (first message must be user)
        is satisfied with a synthesized user anchor.
        """
        from app.claude import build_claude_messages_payload

        context_messages = [
            {"role": "assistant", "content": "I am JTS Powertool. How can I help?", "user_name": "Bot"},
            {"role": "user", "content": "[Abdul Aleem]: give me your last reply", "user_name": "Abdul Aleem"},
        ]

        payload = build_claude_messages_payload(context_messages, "[Abdul Aleem]: give me your last reply")

        self.assertEqual(len(payload), 3)
        self.assertEqual(payload[0]["role"], "user")
        self.assertIn("started on the assistant message below", payload[0]["content"])
        self.assertEqual(payload[1]["role"], "assistant")
        self.assertEqual(payload[1]["content"], "I am JTS Powertool. How can I help?")
        self.assertEqual(payload[2]["role"], "user")
        self.assertEqual(payload[2]["content"], "[Abdul Aleem]: give me your last reply")

    def test_build_claude_messages_payload_with_file_blocks(self):
        """
        Ensure intervening human chatter is prepended into the text block while preserving file attachment blocks.
        """
        from app.claude import build_claude_messages_payload

        context_messages = [
            {"role": "assistant", "content": "I can analyze documents for you.", "user_name": "Bot"},
            {"role": "user", "content": "[Bob]: Check this quarterly report", "user_name": "Bob"},
        ]

        file_blocks = [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "abc123"}},
            {"type": "text", "text": "[Alice]: Please summarize section 2."},
        ]

        payload = build_claude_messages_payload(context_messages, file_blocks)

        self.assertEqual(len(payload), 3)
        self.assertEqual(payload[0]["role"], "user")
        self.assertEqual(payload[1]["role"], "assistant")
        self.assertEqual(payload[2]["role"], "user")

        final_content = payload[2]["content"]
        self.assertIsInstance(final_content, list)
        self.assertEqual(final_content[0]["type"], "document")
        self.assertEqual(final_content[1]["type"], "text")
        self.assertIn("[Bob]: Check this quarterly report", final_content[1]["text"])
        self.assertIn("[Alice]: Please summarize section 2.", final_content[1]["text"])

    def test_channel_level_context_retrieval(self):
        """
        When reply_in_thread is False, get_thread_context_since_last_reply retrieves top-level channel messages.
        """
        mock_channel_msgs = [
            {"id": 20, "role": "assistant", "content": "Channel response", "user_name": "Bot", "user_id": "bot"},
            {"id": 21, "role": "user", "content": "Hey team", "user_name": "Alice", "user_id": "U1"},
            {"id": 22, "role": "user", "content": "@jpt can you hear me?", "user_name": "Bob", "user_id": "U2"},
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cursor.fetchone.return_value = (1,)  # has_msg_col check
        # Query returns rows in DESC order from DB, memory_manager reverses them to ASC
        mock_cursor.fetchall.return_value = list(reversed(mock_channel_msgs))

        with patch("app.memory_manager.get_db_connection", return_value=mock_conn):
            formatted_messages, meta = get_thread_context_since_last_reply("C_CHANNEL", "", reply_in_thread=False)

            self.assertTrue(meta["has_prior_bot_reply"])
            self.assertEqual(len(formatted_messages), 3)
            self.assertEqual(formatted_messages[0]["content"], "Channel response")
            self.assertEqual(formatted_messages[1]["content"], "[Alice]: Hey team")
            self.assertEqual(formatted_messages[2]["content"], "[Bob]: @jpt can you hear me?")

    def test_thread_inherits_channel_previous_conversation(self):
        """
        When a user opens or chats in a thread inside a channel,
        all previous conversation inside that channel (channel_{channel_id})
        is passed to this thread as context alongside the thread's own messages.
        """
        mock_combined_msgs = [
            {"id": 1, "role": "user", "content": "Let's build feature X", "user_name": "Alice", "user_id": "U1"},
            {"id": 2, "role": "assistant", "content": "Feature X sounds great, I can help.", "user_name": "Bot", "user_id": "bot"},
            {"id": 3, "role": "user", "content": "@jpt where do we start?", "user_name": "Bob", "user_id": "U2"},
        ]

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # has_msg_col check returns (1,), assistant lookup returns {"id": 2}
        mock_cursor.fetchone.side_effect = [(1,), {"id": 2}]
        mock_cursor.fetchall.return_value = mock_combined_msgs

        with patch("app.memory_manager.get_db_connection", return_value=mock_conn):
            formatted_messages, meta = get_thread_context_since_last_reply(
                channel_id="C_ALPHA",
                thread_ts="1788800000.123456",
                reply_in_thread=True
            )

            # Verify the execute query includes channel_C_ALPHA in parameters
            executed_calls = mock_cursor.execute.call_args_list
            query_params = executed_calls[-1][0][1]
            self.assertIn("channel_C_ALPHA", query_params)
            self.assertIn("1788800000.123456", query_params)

            # Verify all messages (channel + thread) are in the returned context
            self.assertEqual(len(formatted_messages), 3)
            self.assertEqual(formatted_messages[0]["content"], "[Alice]: Let's build feature X")
            self.assertEqual(formatted_messages[1]["content"], "Feature X sounds great, I can help.")
            self.assertEqual(formatted_messages[2]["content"], "[Bob]: @jpt where do we start?")


if __name__ == "__main__":
    unittest.main()

