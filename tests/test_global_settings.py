import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth_router import create_session_token

client = TestClient(app)


def test_get_global_settings():
    """Test retrieving global settings."""
    response = client.get(
        "/api/global-settings",
        headers={"X-JTS-Role": "admin"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "timezone" in data
    assert "date_format" in data
    assert "time_format" in data
    assert "show_seconds" in data
    assert "auto_dst" in data
    assert "sync_alerts" in data


def test_update_global_settings_as_admin():
    """Test updating global settings as JTS Admin."""
    admin_token = create_session_token("admin", role="jts_admin")
    payload = {
        "timezone": "Europe/London",
        "date_format": "DD/MM/YYYY",
        "time_format": "24h",
        "show_seconds": False,
        "auto_dst": True,
        "sync_alerts": True,
    }
    response = client.put(
        "/api/global-settings",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}", "X-JTS-Role": "admin"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["timezone"] == "Europe/London"
    assert data["date_format"] == "DD/MM/YYYY"
    assert data["time_format"] == "24h"
    assert data["show_seconds"] is False
    assert data["auto_dst"] is True
    assert data["sync_alerts"] is True


def test_update_global_settings_forbidden_for_client_admin():
    """Test that Client Admin is blocked from modifying global settings."""
    client_token = create_session_token("client_user", role="client_admin")
    payload = {
        "timezone": "America/New_York",
        "date_format": "MM/DD/YYYY",
    }
    response = client.put(
        "/api/global-settings",
        json=payload,
        headers={"Authorization": f"Bearer {client_token}", "X-JTS-Role": "client_admin"},
    )
    assert response.status_code == 403
    assert "Only JTS Master Admin" in response.json()["detail"]


def test_reset_global_settings_as_admin():
    """Test resetting global settings back to default as Admin."""
    admin_token = create_session_token("admin", role="jts_admin")
    response = client.post(
        "/api/global-settings/reset",
        headers={"Authorization": f"Bearer {admin_token}", "X-JTS-Role": "admin"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["timezone"] == "UTC"
    assert data["date_format"] == "YYYY-MM-DD"
    assert data["time_format"] == "12h"
    assert data["show_seconds"] is True
    assert data["auto_dst"] is True
    assert data["sync_alerts"] is True
