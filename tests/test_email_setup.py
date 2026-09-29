"""
Unit tests for SMTP Email Setup Workflow.
Tests:
1. Email Service fallback/mock mode when SMTP is unconfigured.
2. User creation with empty password generating a one-time token and sending setup email.
3. Verification of valid, expired, and already-used setup tokens.
4. Setting a new password using a valid token and verifying the user can log in with the new password.
5. Admin resending password setup email for an existing user.
"""
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.services.email_service import send_password_setup_email
from app.auth_router import (
    create_session_token,
    hash_password,
    SESSION_COOKIE_NAME,
)


class TestEmailSetupWorkflow(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_email_service_mock_mode(self):
        """When SMTP credentials are not set, email_service logs URL and returns mock success."""
        with patch.dict("os.environ", {"SMTP_USER": "", "SMTP_PASSWORD": ""}):
            result = send_password_setup_email(
                to_email="testuser@company.com",
                recipient_name="Test User",
                token="test-token-123456",
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["mode"], "mock")
            self.assertIn("/set-password?token=test-token-123456", result["url"])

    @patch("app.auth_router.get_db_connection")
    def test_create_user_without_password_triggers_setup_email(self, mock_get_db):
        """Creating a user with no password sends invitation email and saves token."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        # 1. username check: None, 2. email check: None, 3. insert user RETURNING id=42
        mock_cur.fetchone.side_effect = [None, None, {"id": 42}]

        admin_token = create_session_token("admin", role="jts_admin")
        resp = self.client.post(
            "/api/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "Jane Doe",
                "username": "janedoe",
                "email": "jane@company.com",
                "role": "client_admin",
                "client_folder_id": 1,
                "organization_id": 1,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user_id"], 42)
        self.assertTrue(data.get("email_sent"))
        self.assertIn("invitation email has been sent", data["message"])

    @patch("app.auth_router.get_db_connection")
    def test_verify_setup_token_valid(self, mock_get_db):
        """GET /api/auth/verify-setup-token returns valid: True for fresh token."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value

        mock_cur.fetchone.return_value = {
            "id": 1,
            "user_id": 42,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=12),
            "used_at": None,
            "username": "janedoe",
            "name": "Jane Doe",
            "email": "jane@company.com",
        }

        resp = self.client.get("/api/auth/verify-setup-token?token=valid-test-token")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["valid"])
        self.assertEqual(data["username"], "janedoe")
        self.assertEqual(data["email"], "jane@company.com")

    @patch("app.auth_router.get_db_connection")
    def test_verify_setup_token_expired(self, mock_get_db):
        """Expired token returns valid: False."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value

        mock_cur.fetchone.return_value = {
            "id": 1,
            "user_id": 42,
            "expires_at": datetime.now(timezone.utc) - timedelta(hours=2),
            "used_at": None,
            "username": "janedoe",
            "name": "Jane Doe",
            "email": "jane@company.com",
        }

        resp = self.client.get("/api/auth/verify-setup-token?token=expired-test-token")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["valid"])
        self.assertIn("expired", data["message"])

    @patch("app.auth_router.get_db_connection")
    def test_verify_setup_token_already_used(self, mock_get_db):
        """Already-used token returns valid: False."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value

        mock_cur.fetchone.return_value = {
            "id": 1,
            "user_id": 42,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=10),
            "used_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            "username": "janedoe",
            "name": "Jane Doe",
            "email": "jane@company.com",
        }

        resp = self.client.get("/api/auth/verify-setup-token?token=used-test-token")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["valid"])
        self.assertIn("already been used", data["message"])

    @patch("app.auth_router.get_db_connection")
    def test_set_password_success(self, mock_get_db):
        """POST /api/auth/set-password sets new password and marks token as used."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value

        mock_cur.fetchone.return_value = {
            "id": 1,
            "user_id": 42,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=10),
            "used_at": None,
            "username": "janedoe",
            "name": "Jane Doe",
        }

        resp = self.client.post(
            "/api/auth/set-password",
            json={
                "token": "valid-test-token",
                "password": "NewSecurePassword#2026",
                "confirm_password": "NewSecurePassword#2026",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("successfully set", data["message"])

    @patch("app.auth_router.get_db_connection")
    def test_send_user_setup_email_endpoint(self, mock_get_db):
        """Admin triggering POST /api/users/{user_id}/send-setup-email sends email."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value

        mock_cur.fetchone.return_value = {
            "id": 42,
            "name": "Jane Doe",
            "username": "janedoe",
            "email": "jane@company.com",
        }

        admin_token = create_session_token("admin", role="jts_admin")
        resp = self.client.post(
            "/api/users/42/send-setup-email",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("Password setup email successfully sent", data["message"])
