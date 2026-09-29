"""
Unit tests for Channel-Specific API Key Management System.
Verifies:
1. Admin RBAC enforcement (403 for unauthorized, 200 for admin).
2. Storing secrets in AWS Secrets Manager (mocked) and DB mapping without plaintext keys.
3. Channel secret isolation (Channel A key != Channel B).
4. Deletion of channel secrets.
5. Worker channel secret resolution.
"""
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.channel_secrets_service import (
    store_channel_secret,
    get_channel_secret_value,
    delete_channel_secret,
    list_channel_secrets,
    list_all_channels,
    _SECRET_VALUE_CACHE,
    _MOCK_AWS_SECRETS,
)


class TestChannelSecretsSystem(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.admin_headers = {"X-JTS-Role": "admin"}
        _SECRET_VALUE_CACHE.clear()
        _MOCK_AWS_SECRETS.clear()

    def tearDown(self):
        _SECRET_VALUE_CACHE.clear()
        _MOCK_AWS_SECRETS.clear()

    def test_rbac_denies_unauthorized_access(self):
        """Endpoints should return 403 when admin role/token is missing."""
        resp = self.client.get("/api/channels")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("JTS Admin RBAC", resp.json().get("detail", ""))

        resp = self.client.post(
            "/api/channels/C_TEST_123/secrets",
            json={"provider": "anthropic", "api_key": "sk-ant-secret123"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_rbac_allows_admin_role_header(self):
        """Endpoints should allow access when X-JTS-Role: admin is provided."""
        with patch("app.channel_secrets_router.list_all_channels", return_value=[]):
            resp = self.client.get("/api/channels", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)

    def test_rbac_allows_admin_token_header(self):
        """Endpoints should allow access when X-JTS-Admin-Token matches."""
        with patch("app.channel_secrets_router.get_secret", return_value="my-secret-token"):
            resp = self.client.get(
                "/api/channels",
                headers={"X-JTS-Admin-Token": "my-secret-token"},
            )
            self.assertEqual(resp.status_code, 200)

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("app.services.channel_secrets_service._get_secretsmanager_client")
    def test_store_channel_secret_in_aws_and_db(self, mock_get_client, mock_get_conn):
        """Stores secret in AWS and mapping in DB, never returning or storing plaintext key in DB."""
        mock_aws_client = MagicMock()
        mock_aws_client.create_secret.return_value = {"ARN": "arn:aws:secretsmanager:us-east-1:123:secret:jts-1"}
        mock_aws_client.exceptions.ResourceNotFoundException = Exception
        mock_aws_client.describe_secret.side_effect = mock_aws_client.exceptions.ResourceNotFoundException
        mock_get_client.return_value = mock_aws_client

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        mock_cur.fetchone.return_value = {
            "id": 1,
            "channel_id": "C_PROJ_ALPHA",
            "channel_name": "#project-alpha",
            "provider": "anthropic",
            "aws_secret_name": "jts-powertool/channels/C_PROJ_ALPHA/anthropic",
            "aws_secret_arn": "arn:aws:secretsmanager:us-east-1:123:secret:jts-1",
            "status": "active",
            "created_at": None,
            "updated_at": None,
            "updated_by": "admin",
        }

        resp = self.client.post(
            "/api/channels/C_PROJ_ALPHA/secrets",
            json={
                "provider": "anthropic",
                "api_key": "sk-ant-testkey12345",
                "channel_name": "#project-alpha",
            },
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        mapping = data["mapping"]
        # Ensure API key is NEVER in mapping response
        self.assertNotIn("api_key", mapping)
        self.assertNotIn("sk-ant-testkey12345", str(mapping))
        self.assertEqual(mapping["channel_id"], "C_PROJ_ALPHA")
        self.assertEqual(mapping["provider"], "anthropic")

        # Verify AWS Secrets Manager was called with secret payload
        mock_aws_client.create_secret.assert_called_once()
        create_args = mock_aws_client.create_secret.call_args[1]
        self.assertIn("SecretString", create_args)
        self.assertIn("sk-ant-testkey12345", create_args["SecretString"])

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("app.services.channel_secrets_service._get_secretsmanager_client")
    def test_store_channel_secret_generates_client_and_project_slug_path(self, mock_get_client, mock_get_conn):
        """Standardized AWS Secret path includes client folder slug and project channel slug."""
        mock_aws_client = MagicMock()
        mock_aws_client.create_secret.return_value = {"ARN": "arn:aws:secretsmanager:us-east-1:123:secret:jts-client"}
        mock_aws_client.exceptions.ResourceNotFoundException = Exception
        mock_aws_client.describe_secret.side_effect = mock_aws_client.exceptions.ResourceNotFoundException
        mock_get_client.return_value = mock_aws_client

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        mock_cur.fetchone.side_effect = [
            {"channel_name": "#acme-website", "folder_name": "Acme Corp"},
            {
                "id": 2,
                "channel_id": "C_ACME_WEB",
                "channel_name": "#acme-website",
                "provider": "openai",
                "aws_secret_name": "jts-powertool/clients/acme-corp/acme-website/openai",
                "aws_secret_arn": "arn:aws:secretsmanager:us-east-1:123:secret:jts-client",
                "status": "active",
                "created_at": None,
                "updated_at": None,
                "updated_by": "admin",
            },
        ]

        resp = self.client.post(
            "/api/channels/C_ACME_WEB/secrets",
            json={
                "provider": "openai",
                "api_key": "sk-openai-secretkey123",
                "channel_name": "#acme-website",
            },
            headers=self.admin_headers,
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")

        # Verify secret path passed to AWS
        create_args = mock_aws_client.create_secret.call_args[1]
        self.assertEqual(create_args["Name"], "jts-powertool/clients/acme-corp/acme-website/openai")

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("app.services.channel_secrets_service._get_secretsmanager_client")
    def test_channel_secret_isolation(self, mock_get_client, mock_get_conn):
        """Channel A secret must not leak to Channel B."""
        mock_aws_client = MagicMock()
        mock_aws_client.get_secret_value.return_value = {
            "SecretString": '{"api_key": "sk-ant-channel-A-key", "provider": "anthropic"}'
        }
        mock_get_client.return_value = mock_aws_client

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        # Channel A has mapping
        mock_cur.fetchone.return_value = {
            "aws_secret_name": "jts-powertool/channels/C_CHAN_A/anthropic",
            "status": "active",
        }

        val_a = get_channel_secret_value("C_CHAN_A", "anthropic")
        self.assertEqual(val_a, "sk-ant-channel-A-key")

        # Channel B has no mapping
        mock_cur.fetchone.return_value = None
        val_b = get_channel_secret_value("C_CHAN_B", "anthropic")
        self.assertIsNone(val_b)

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("app.services.channel_secrets_service._get_secretsmanager_client")
    def test_delete_channel_secret(self, mock_get_client, mock_get_conn):
        """Deleting secret removes DB mapping and calls AWS deletion."""
        mock_aws_client = MagicMock()
        mock_get_client.return_value = mock_aws_client

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn
        mock_cur.fetchone.return_value = {
            "aws_secret_name": "jts-powertool/channels/C_CHAN_A/anthropic"
        }

        resp = self.client.delete(
            "/api/channels/C_CHAN_A/secrets/anthropic",
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

        # AWS delete_secret should be called
        mock_aws_client.delete_secret.assert_called_once_with(
            SecretId="jts-powertool/channels/C_CHAN_A/anthropic",
            ForceDeleteWithoutRecovery=True,
        )

    def test_rename_channel_endpoint(self):
        """PATCH /api/channels/{channel_id}/name updates channel friendly name."""
        with patch("app.channel_secrets_router.set_channel_name", return_value="#my-custom-project"):
            resp = self.client.patch(
                "/api/channels/C_TEST_CHAN/name",
                json={"channel_name": "my-custom-project"},
                headers=self.admin_headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["channel_id"], "C_TEST_CHAN")
            self.assertEqual(data["channel_name"], "#my-custom-project")

    def test_list_channels_returns_friendly_names(self):
        """GET /api/channels returns channels with friendly human-readable names."""
        mock_channels = [
            {"channel_id": "C08MV3EM9PY", "channel_name": "#jts_powertool", "secret_count": 0, "providers": []},
            {"channel_id": "D08SLP9LXUZ", "channel_name": "@Abdul Aleem (DM)", "secret_count": 0, "providers": []},
        ]
        with patch("app.channel_secrets_router.list_all_channels", return_value=mock_channels):
            resp = self.client.get("/api/channels", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["channels"][0]["channel_name"], "#jts_powertool")
            self.assertEqual(data["channels"][1]["channel_name"], "@Abdul Aleem (DM)")

    def test_create_channel_folder_endpoint(self):
        """POST /api/channels/folders creates a new folder in DB."""
        mock_folder = {
            "id": 1,
            "name": "Production Projects",
            "description": "Client bots",
            "channel_count": 0,
            "created_at": "2026-09-14T10:00:00",
            "updated_at": "2026-09-14T10:00:00",
        }
        with patch("app.channel_secrets_router.create_channel_folder", return_value=mock_folder):
            resp = self.client.post(
                "/api/channels/folders",
                json={"name": "Production Projects", "description": "Client bots"},
                headers=self.admin_headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["folder"]["name"], "Production Projects")
            self.assertEqual(data["folder"]["id"], 1)

    def test_list_channel_folders_endpoint(self):
        """GET /api/channels/folders lists all folders with channel counts."""
        mock_folders = [
            {"id": 1, "name": "Client A", "description": "Desc", "channel_count": 2},
            {"id": 2, "name": "Internal Dev", "description": None, "channel_count": 0},
        ]
        with patch("app.channel_secrets_router.list_channel_folders", return_value=mock_folders):
            resp = self.client.get("/api/channels/folders", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["folders"][0]["name"], "Client A")
            self.assertEqual(data["folders"][0]["channel_count"], 2)

    def test_update_channel_folder_endpoint(self):
        """PATCH /api/channels/folders/{folder_id} renames folder."""
        mock_updated = {"id": 1, "name": "Renamed Folder", "description": "New Desc"}
        with patch("app.channel_secrets_router.update_channel_folder", return_value=mock_updated):
            resp = self.client.patch(
                "/api/channels/folders/1",
                json={"name": "Renamed Folder", "description": "New Desc"},
                headers=self.admin_headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["folder"]["name"], "Renamed Folder")

    def test_delete_channel_folder_endpoint(self):
        """DELETE /api/channels/folders/{folder_id} deletes folder safely."""
        with patch("app.channel_secrets_router.delete_channel_folder", return_value=True):
            resp = self.client.delete("/api/channels/folders/1", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")

    def test_assign_channel_folder_endpoint(self):
        """PATCH /api/channels/{channel_id}/folder assigns a channel to a folder."""
        mock_result = {"channel_id": "C_TEST_CHAN", "folder_id": 1, "folder_name": "Production Projects"}
        with patch("app.channel_secrets_router.set_channel_folder", return_value=mock_result):
            resp = self.client.patch(
                "/api/channels/C_TEST_CHAN/folder",
                json={"folder_id": 1},
                headers=self.admin_headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["folder_id"], 1)
            self.assertEqual(data["folder_name"], "Production Projects")

    def test_get_folder_details_endpoint(self):
        """GET /api/channels/folders/{folder_id} returns folder and channels."""
        mock_folder = {
            "id": 1,
            "name": "Production Projects",
            "description": "Main projects",
            "channels": [
                {"channel_id": "C_PROJ_1", "channel_name": "#proj-1", "secret_count": 2, "providers": ["anthropic"]}
            ],
            "channel_count": 1,
        }
        with patch("app.channel_secrets_router.get_channel_folder", return_value=mock_folder):
            resp = self.client.get("/api/channels/folders/1", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["folder"]["name"], "Production Projects")
            self.assertEqual(len(data["channels"]), 1)
            self.assertEqual(data["channels"][0]["channel_id"], "C_PROJ_1")

    def test_get_folder_details_not_found(self):
        """GET /api/channels/folders/{folder_id} returns 404 when folder does not exist."""
        with patch("app.channel_secrets_router.get_channel_folder", return_value=None):
            resp = self.client.get("/api/channels/folders/999", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 404)

    def test_create_slack_channel_in_folder_endpoint(self):
        """POST /api/channels/folders/{folder_id}/channels creates Slack channel and stores in folder."""
        mock_created = {
            "channel_id": "C_NEW_SLACK_123",
            "channel_name": "#marketing-ops",
            "channel_type": "channel",
            "folder_id": 1,
            "folder_name": "Marketing Folder",
            "is_private": False,
            "secret_count": 0,
            "providers": [],
        }
        with patch("app.channel_secrets_router.create_slack_channel", return_value=mock_created):
            resp = self.client.post(
                "/api/channels/folders/1/channels",
                json={"name": "marketing-ops", "is_private": False, "topic": "Marketing discussion"},
                headers=self.admin_headers,
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["channel"]["channel_id"], "C_NEW_SLACK_123")
            self.assertEqual(data["channel"]["channel_name"], "#marketing-ops")

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("httpx.Client.post")
    def test_create_slack_channel_service_success(self, mock_post, mock_get_conn):
        """create_slack_channel calls Slack conversations.create and persists metadata."""
        from app.services.channel_secrets_service import create_slack_channel

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        # 1st fetch: folder exists
        mock_cur.fetchone.return_value = {"id": 1, "name": "Production Projects"}

        mock_slack_response = MagicMock()
        mock_slack_response.json.return_value = {
            "ok": True,
            "channel": {
                "id": "C_SLACK_NEW_456",
                "name": "client-portal",
                "is_channel": True,
            }
        }
        mock_post.return_value = mock_slack_response

        with patch("app.tools.secrets_manager.get_secret", return_value="xoxb-fake-token"):
            result = create_slack_channel(name="Client Portal", folder_id=1, is_private=False)
            self.assertEqual(result["channel_id"], "C_SLACK_NEW_456")
            self.assertEqual(result["channel_name"], "#client-portal")
            self.assertEqual(result["folder_id"], 1)

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("httpx.Client.post")
    def test_create_slack_channel_service_name_taken(self, mock_post, mock_get_conn):
        """create_slack_channel raises ValueError when Slack returns name_taken."""
        from app.services.channel_secrets_service import create_slack_channel

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn
        mock_cur.fetchone.return_value = {"id": 1, "name": "Production Projects"}

        mock_slack_response = MagicMock()
        mock_slack_response.json.return_value = {"ok": False, "error": "name_taken"}
        mock_post.return_value = mock_slack_response

        with patch("app.tools.secrets_manager.get_secret", return_value="xoxb-fake-token"):
            with self.assertRaises(ValueError) as ctx:
                create_slack_channel(name="existing-channel", folder_id=1)
            self.assertIn("already exists in your workspace", str(ctx.exception))

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("httpx.Client.get")
    def test_sync_bot_conversations_from_slack(self, mock_get, mock_get_conn):
        """sync_bot_conversations_from_slack fetches channels & DMs and upserts to channel_metadata."""
        from app.services.channel_secrets_service import sync_bot_conversations_from_slack

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        # Mock Slack users.conversations and users.info responses
        def side_effect_get(url, *args, **kwargs):
            mock_resp = MagicMock()
            if "users.conversations" in url:
                mock_resp.json.return_value = {
                    "ok": True,
                    "channels": [
                        {"id": "C_TEST_SYNC", "name": "project-sync", "is_channel": True, "is_private": False},
                        {"id": "D_TEST_DM", "is_im": True, "user": "U_PEER_1"},
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            elif "users.info" in url:
                mock_resp.json.return_value = {
                    "ok": True,
                    "user": {"real_name": "Alice Smith", "name": "alices"},
                }
            return mock_resp

        mock_get.side_effect = side_effect_get

        with patch("app.tools.secrets_manager.get_secret", return_value="xoxb-fake-token"):
            result = sync_bot_conversations_from_slack()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["synced_count"], 2)
        channel_names = [c["channel_name"] for c in result["channels"]]
        self.assertIn("#project-sync", channel_names)
        self.assertIn("@Alice Smith (DM)", channel_names)
        self.assertEqual(mock_cur.execute.call_count, 2)

    def test_sync_slack_channels_endpoint(self):
        """POST /api/channels/sync calls sync_bot_conversations_from_slack."""
        with patch("app.channel_secrets_router.sync_bot_conversations_from_slack") as mock_sync:
            mock_sync.return_value = {
                "status": "success",
                "synced_count": 3,
                "channels": [],
            }
            resp = self.client.post("/api/channels/sync", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["synced_count"], 3)

    def test_get_unassigned_channels_endpoint(self):
        """GET /api/channels/unassigned returns channels with folder_id is None."""
        mock_channels = [
            {"channel_id": "C1", "channel_name": "#chan-1", "folder_id": None},
            {"channel_id": "C2", "channel_name": "#chan-2", "folder_id": 5},
        ]
        with patch("app.channel_secrets_router.list_unassigned_channels", return_value=[mock_channels[0]]):
            resp = self.client.get("/api/channels/unassigned", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(len(data["channels"]), 1)
            self.assertEqual(data["channels"][0]["channel_id"], "C1")

    def test_member_joined_channel_event_handling(self):
        """Slack events endpoint handles member_joined_channel and auto-registers channel."""
        with patch("app.services.channel_secrets_service.resolve_slack_channel_name") as mock_resolve:
            mock_resolve.return_value = "#new-joined-chan"
            payload = {
                "type": "event_callback",
                "event": {
                    "type": "member_joined_channel",
                    "user": "U_BOT_USER",
                    "channel": "C_AUTO_DISCOVERED",
                }
            }
            resp = self.client.post("/api/slack/events", json=payload)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json().get("status"), "channel_registered")
            mock_resolve.assert_called_with("C_AUTO_DISCOVERED")

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("app.services.channel_secrets_service._get_secretsmanager_client")
    def test_list_vault_secrets_fetches_key_names_without_secret_values(self, mock_get_client, mock_get_conn):
        """GET /api/vault returns secret key names from AWS Secrets Manager without exposing actual secret values."""
        mock_aws_client = MagicMock()
        def side_effect_gsv(SecretId, **kwargs):
            if SecretId == "my-aws-secrets-bundle":
                return {"SecretString": '{"OPENAI_API_KEY": "sk-proj-secret-12345", "ANTHROPIC_API_KEY": "sk-ant-secret-67890"}'}
            elif "github-token" in SecretId:
                return {"SecretString": '{"key_name": "Github Token", "key_value": "ghp_123456789"}'}
            return {}
        mock_aws_client.get_secret_value.side_effect = side_effect_gsv
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"SecretList": [{"Name": "jts-powertool/vault/github-token", "CreatedDate": None}]}
        ]
        mock_aws_client.get_paginator.return_value = paginator
        mock_get_client.return_value = mock_aws_client

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn
        mock_cur.fetchall.return_value = []

        with patch.dict("os.environ", {"AWS_SECRET_NAME": "my-aws-secrets-bundle"}):
            resp = self.client.get("/api/vault", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            secrets = data.get("secrets", [])
            key_names = [s["key_name"] for s in secrets]
            self.assertIn("OPENAI_API_KEY", key_names)
            self.assertIn("ANTHROPIC_API_KEY", key_names)
            self.assertIn("Github Token", key_names)

            # Strict privacy check: secret values MUST NOT appear in the JSON output
            raw_text = resp.text
            self.assertNotIn("sk-proj-secret-12345", raw_text)
            self.assertNotIn("sk-ant-secret-67890", raw_text)

    @patch("app.services.channel_secrets_service.get_db_connection")
    @patch("app.services.channel_secrets_service._get_secretsmanager_client")
    def test_store_vault_secret_stores_in_aws_and_db(self, mock_get_client, mock_get_conn):
        """POST /api/vault stores key-value in AWS Secrets Manager using IAM boto3 client and returns safe metadata."""
        mock_aws_client = MagicMock()
        mock_get_client.return_value = mock_aws_client

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_get_conn.return_value = mock_conn
        mock_cur.fetchone.return_value = {
            "id": 10,
            "key_name": "JIRA_API_TOKEN",
            "aws_secret_name": "jts-powertool/vault/jira-api-token",
            "name": "Admin User",
            "created_at": None,
        }

        resp = self.client.post(
            "/api/vault",
            json={"key_name": "Jira API Token", "key_value": "ATATT3xFfGF0123456789"},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        secret = data["secret"]
        self.assertEqual(secret["key_name"], "JIRA_API_TOKEN")
        self.assertEqual(secret["name"], "Admin User")
        self.assertNotIn("ATATT3xFfGF0123456789", str(secret))

        # Verify AWS Secrets Manager create_secret was called with encrypted payload
        mock_aws_client.create_secret.assert_called_once()
        create_args = mock_aws_client.create_secret.call_args[1]
        self.assertIn("SecretString", create_args)
        self.assertIn("ATATT3xFfGF0123456789", create_args["SecretString"])


if __name__ == "__main__":
    unittest.main()




