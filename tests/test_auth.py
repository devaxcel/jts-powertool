"""
Unit tests for Dashboard Authentication System.
Verifies:
1. Valid login returns 200, JWT/HMAC token, and sets session cookie.
2. Invalid password returns 401.
3. Invalid username returns 401.
4. /api/auth/me returns 200 for valid session, 401 for invalid.
5. Logout clears session cookie.
"""
import unittest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.auth_router import (
    SESSION_COOKIE_NAME,
    create_session_token,
    verify_session_token,
)


class TestDashboardAuth(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_successful_login(self):
        """Valid credentials return 200 and set session cookie."""
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "JTSAdmin#2026!"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("token", data)
        self.assertEqual(data["user"]["username"], "admin")
        self.assertEqual(data["user"]["role"], "jts_admin")

        # Verify cookie is set
        self.assertIn(SESSION_COOKIE_NAME, resp.cookies)
        cookie_val = resp.cookies[SESSION_COOKIE_NAME]
        self.assertEqual(cookie_val, data["token"])

    def test_invalid_password_rejected(self):
        """Invalid password returns 401 Unauthorized."""
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "WrongPassword123"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid User ID or Password", resp.json()["detail"])

    def test_invalid_username_rejected(self):
        """Invalid username returns 401 Unauthorized."""
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "hacker", "password": "JTSAdmin#2026!"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid User ID or Password", resp.json()["detail"])

    def test_auth_me_with_valid_cookie(self):
        """GET /api/auth/me succeeds with valid cookie."""
        login_resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "JTSAdmin#2026!"},
        )
        token = login_resp.json()["token"]

        me_resp = self.client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: token})
        self.assertEqual(me_resp.status_code, 200)
        data = me_resp.json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["username"], "admin")

    def test_auth_me_unauthorized(self):
        """GET /api/auth/me returns 401 when unauthenticated."""
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_auth_me_invalid_token(self):
        """GET /api/auth/me returns 401 with forged or expired token."""
        resp = self.client.get("/api/auth/me", cookies={SESSION_COOKIE_NAME: "forged.token"})
        self.assertEqual(resp.status_code, 401)

    def test_logout(self):
        """POST /api/auth/logout deletes cookie."""
        resp = self.client.post("/api/auth/logout")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

    def test_token_creation_and_verification(self):
        """Session tokens verify correctly and reject tampering."""
        token = create_session_token("admin")
        payload = verify_session_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["user"], "admin")

        # Tampered token
        tampered = token[:-4] + "xxxx"
        self.assertIsNone(verify_session_token(tampered))

    @patch("app.auth_router.get_db_connection")
    def test_create_user_success(self, mock_get_db):
        """Creating a user with valid mandatory fields succeeds."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        # 1. ensure columns, 2. username check (None), 3. email check (None), 4. insert RETURNING id
        mock_cur.fetchone.side_effect = [None, None, {"id": 10}]

        admin_token = create_session_token("admin", role="jts_admin")
        resp = self.client.post(
            "/api/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "Sarah Connor",
                "username": "sconnor",
                "email": "sarah@cyberdyne.com",
                "password": "SecretPassword#123",
                "role": "jts_admin",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user_id"], 10)

    @patch("app.auth_router.get_db_connection")
    def test_create_user_duplicate_email_rejected(self, mock_get_db):
        """Creating a user with duplicate email returns 400."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        # 1. username check (None), 2. email check returns existing user ID
        mock_cur.fetchone.side_effect = [None, {"id": 5}]

        admin_token = create_session_token("admin", role="jts_admin")
        resp = self.client.post(
            "/api/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "Sarah Connor",
                "username": "sconnor2",
                "email": "sarah@cyberdyne.com",
                "password": "SecretPassword#123",
                "role": "jts_admin",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already registered", resp.json()["detail"])

    @patch("app.auth_router.get_db_connection")
    def test_create_user_missing_mandatory_name_rejected(self, mock_get_db):
        """Missing Name field is rejected."""
        admin_token = create_session_token("admin", role="jts_admin")
        resp = self.client.post(
            "/api/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "",
                "username": "sconnor",
                "email": "sarah@cyberdyne.com",
                "password": "SecretPassword#123",
                "role": "jts_admin",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Name is required", resp.json()["detail"])

    @patch("app.auth_router.get_db_connection")
    def test_update_user_success(self, mock_get_db):
        """Updating existing user's info succeeds."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        # 1. user lookup: (id=10, username='sconnor'), 2. email duplicate lookup: None
        mock_cur.fetchone.side_effect = [{"id": 10, "username": "sconnor"}, None]

        admin_token = create_session_token("admin", role="jts_admin")
        resp = self.client.put(
            "/api/users/10",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "Sarah Connor Updated",
                "email": "sarah.new@cyberdyne.com",
                "role": "jts_admin",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("updated successfully", data["message"])

    @patch("app.auth_router.get_db_connection")
    def test_update_user_duplicate_email_rejected(self, mock_get_db):
        """Updating existing user with an email already belonging to another user returns 400."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        # 1. user lookup: (id=10, username='sconnor'), 2. email duplicate lookup: found id=2
        mock_cur.fetchone.side_effect = [{"id": 10, "username": "sconnor"}, {"id": 2}]

        admin_token = create_session_token("admin", role="jts_admin")
        resp = self.client.put(
            "/api/users/10",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "Sarah Connor",
                "email": "taken@othercompany.com",
                "role": "jts_admin",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already registered", resp.json()["detail"])

    @patch("app.auth_router.get_db_connection")
    def test_get_my_profile(self, mock_get_db):
        """GET /api/users/me returns the logged in user's profile."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        mock_cur.fetchone.return_value = {
            "id": 5,
            "name": "Test User",
            "username": "testuser",
            "email": "test@example.com",
            "role": "client_admin",
            "client_folder_id": 2,
            "client_folder_name": "Test Folder",
            "created_at": None,
        }
        token = create_session_token("testuser", role="client_admin", client_folder_id=2)
        resp = self.client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["name"], "Test User")
        self.assertEqual(data["user"]["username"], "testuser")

    @patch("app.auth_router.get_db_connection")
    def test_update_my_profile(self, mock_get_db):
        """PUT /api/users/me updates the logged in user's own profile."""
        mock_conn = mock_get_db.return_value
        mock_cur = mock_conn.cursor.return_value.__enter__.return_value
        # 1. lookup user: id=5, 2. email unique: None, 3. update returning id=5
        mock_cur.fetchone.side_effect = [
            {"id": 5, "username": "testuser", "role": "client_admin", "client_folder_id": 2},
            None,
            {"id": 5, "name": "Updated Name", "username": "testuser", "email": "updated@example.com", "role": "client_admin", "client_folder_id": 2},
            {"name": "Test Folder"},
        ]
        token = create_session_token("testuser", role="client_admin", client_folder_id=2)
        resp = self.client.put(
            "/api/users/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Updated Name", "email": "updated@example.com"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user"]["name"], "Updated Name")
        self.assertEqual(data["user"]["email"], "updated@example.com")


if __name__ == "__main__":
    unittest.main()
