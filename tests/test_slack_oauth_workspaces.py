import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.tools.secrets_manager import get_slack_bot_token


class TestSlackOAuthAndWorkspaces(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.tools.secrets_manager.get_secret")
    def test_get_slack_bot_token_fallback(self, mock_get_secret):
        mock_get_secret.side_effect = lambda key, default="": (
            "xoxb-default-token" if key == "SLACK_BOT_TOKEN" else default
        )
        token = get_slack_bot_token(None)
        self.assertEqual(token, "xoxb-default-token")

    @patch("app.tools.secrets_manager.get_secret")
    def test_get_slack_bot_token_workspace_specific_from_secrets(self, mock_get_secret):
        mock_get_secret.side_effect = lambda key, default="": (
            "xoxb-workspace-2-token" if key == "SLACK_BOT_TOKEN_T09876543" else (
                "xoxb-default-token" if key == "SLACK_BOT_TOKEN" else default
            )
        )
        token = get_slack_bot_token("T09876543")
        self.assertEqual(token, "xoxb-workspace-2-token")

    @patch("app.db.repositories.get_slack_workspace_token")
    @patch("app.tools.secrets_manager.get_secret")
    def test_get_slack_bot_token_from_db(self, mock_get_secret, mock_db_token):
        mock_get_secret.side_effect = lambda key, default="": (
            "xoxb-default-token" if key == "SLACK_BOT_TOKEN" else default
        )
        mock_db_token.return_value = "xoxb-db-token"
        token = get_slack_bot_token("T_FROM_DB")
        self.assertEqual(token, "xoxb-db-token")

    def test_install_redirect(self):
        resp = self.client.get("/api/slack/install", follow_redirects=False)
        self.assertEqual(resp.status_code, 307)
        location = resp.headers.get("location", "")
        self.assertIn("https://slack.com/oauth/v2/authorize", location)
        self.assertIn("client_id=203729176583.11900714527686", location)
        self.assertIn("redirect_uri=https://journeys.pe/api/slack/oauth/callback", location)

    def test_oauth_callback_error(self):
        resp = self.client.get("/api/slack/oauth/callback?error=access_denied")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Installation Cancelled", resp.text)

    @patch("app.slack_router.save_slack_workspace")
    @patch("httpx.AsyncClient.post")
    def test_oauth_callback_success(self, mock_post, mock_save_ws):
        from unittest.mock import AsyncMock, MagicMock
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "ok": True,
            "access_token": "xoxb-new-workspace-token-12345",
            "team": {"id": "T_NEW_WORKSPACE", "name": "Second Workspace"},
            "bot_user_id": "U_BOT_123",
        }
        mock_post.return_value = mock_resp
        resp = self.client.get("/api/slack/oauth/callback?code=mock-code-123")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Workspace Connected!", resp.text)
        self.assertIn("Second Workspace", resp.text)
    @patch("app.db.repositories.get_slack_workspace_token", return_value=None)
    @patch("app.tools.secrets_manager.get_secret")
    def test_get_slack_bot_token_axcel_aliases(self, mock_get_secret, mock_db_tok):
        mock_get_secret.side_effect = lambda key, default="": (
            "xoxb-axcel-world-secret-token" if key == "SLACK_BOT_TOKEN_AXCELWORLD" else (
                "xoxb-default-token" if key == "SLACK_BOT_TOKEN" else default
            )
        )
        token = get_slack_bot_token("T5ZMF56H5")
        self.assertEqual(token, "xoxb-axcel-world-secret-token")

    @patch("app.db.repositories.get_slack_workspace_token", return_value=None)
    @patch("app.tools.secrets_manager.get_secret")
    def test_get_slack_bot_token_jts_aliases(self, mock_get_secret, mock_db_tok):
        mock_get_secret.side_effect = lambda key, default="": (
            "xoxb-jts-team-secret-token" if key == "SLACK_BOT_TOKEN_JTSTEAM" else (
                "xoxb-default-token" if key == "SLACK_BOT_TOKEN" else default
            )
        )
        token = get_slack_bot_token("T02HKMBE09K")
        self.assertEqual(token, "xoxb-jts-team-secret-token")


if __name__ == "__main__":
    unittest.main()

