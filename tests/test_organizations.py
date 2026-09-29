"""
Unit tests for Organizations Management Router and Email Uniqueness Rules.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.auth_router import create_session_token
from app.memory_manager import run_database_migrations

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    run_database_migrations()

@pytest.fixture
def admin_headers():
    token = create_session_token("admin", role="jts_admin")
    return {
        "Authorization": f"Bearer {token}",
        "X-JTS-Role": "admin"
    }

@pytest.fixture
def client_headers():
    token = create_session_token("client_user", role="client_standard", client_folder_id=2)
    return {
        "Authorization": f"Bearer {token}",
        "X-JTS-Role": "client_standard"
    }

def test_get_next_org_id(admin_headers):
    resp = client.get("/api/organizations/next-id", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "next_id" in data
    assert isinstance(data["next_id"], int)

def test_create_organization_success(admin_headers):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    email = f"contact_{suffix}@testorg.com"
    billing_email = f"contact_{suffix}@testorg.com"  # identical within same org is allowed!

    payload = {
        "name": f"Test Org {suffix}",
        "poc": "John Doe",
        "phone": "+1 555-0199",
        "email": email,
        "billing_email": billing_email,
        "address": "123 Business Way, Suite 400"
    }

    resp = client.post("/api/organizations", json=payload, headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    org = data["organization"]
    assert org["id"] is not None
    assert org["name"] == payload["name"]
    assert org["poc"] == "John Doe"
    assert org["email"] == email
    assert org["billing_email"] == billing_email
    assert org["address"] == "123 Business Way, Suite 400"

def test_create_organization_duplicate_email_rejected(admin_headers):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    email = f"shared_{suffix}@corp.com"

    # Org 1 with email
    payload1 = {
        "name": f"Org One {suffix}",
        "email": email,
        "billing_email": f"billing_{suffix}@corp.com"
    }
    resp1 = client.post("/api/organizations", json=payload1, headers=admin_headers)
    assert resp1.status_code == 200

    # Org 2 attempting to use same email as Org 1
    payload2 = {
        "name": f"Org Two {suffix}",
        "email": email,
        "billing_email": f"other_billing_{suffix}@corp.com"
    }
    resp2 = client.post("/api/organizations", json=payload2, headers=admin_headers)
    assert resp2.status_code == 400
    assert "already registered with organization" in resp2.json()["detail"]

    # Org 3 attempting to use Org 1's billing_email as its primary email
    payload3 = {
        "name": f"Org Three {suffix}",
        "email": f"billing_{suffix}@corp.com",
        "billing_email": f"unique_billing_{suffix}@corp.com"
    }
    resp3 = client.post("/api/organizations", json=payload3, headers=admin_headers)
    assert resp3.status_code == 400
    assert "already registered with organization" in resp3.json()["detail"]

def test_organization_email_separate_from_user_email(admin_headers):
    """Organization emails should be separate from dashboard_users emails."""
    import uuid
    suffix = uuid.uuid4().hex[:6]
    user_email = f"admin_{suffix}@jts-powertool.com"

    # Even if this matches a user account, organization table allows it because they are separate
    payload = {
        "name": f"Org User Email {suffix}",
        "email": user_email,
        "billing_email": user_email
    }
    resp = client.post("/api/organizations", json=payload, headers=admin_headers)
    assert resp.status_code == 200

def test_client_cannot_create_organization(client_headers):
    payload = {
        "name": "Unauthorized Org",
        "email": "unauth@org.com"
    }
    resp = client.post("/api/organizations", json=payload, headers=client_headers)
    assert resp.status_code == 403

def test_update_organization(admin_headers):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    payload = {
        "name": f"Initial Org {suffix}",
        "email": f"orig_{suffix}@corp.com",
        "poc": "Original POC"
    }
    create_resp = client.post("/api/organizations", json=payload, headers=admin_headers)
    assert create_resp.status_code == 200
    org_id = create_resp.json()["organization"]["id"]

    update_payload = {
        "name": f"Updated Org {suffix}",
        "poc": "Updated POC",
        "phone": "+1 800-555-0100",
        "email": f"updated_{suffix}@corp.com",
        "billing_email": f"updated_billing_{suffix}@corp.com",
        "address": "456 New Road"
    }
    update_resp = client.put(f"/api/organizations/{org_id}", json=update_payload, headers=admin_headers)
    assert update_resp.status_code == 200
    updated_org = update_resp.json()["organization"]
    assert updated_org["name"] == f"Updated Org {suffix}"
    assert updated_org["poc"] == "Updated POC"
    assert updated_org["email"] == f"updated_{suffix}@corp.com"

def test_get_single_organization(admin_headers):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    payload = {
        "name": f"Single Get Org {suffix}",
        "email": f"get_{suffix}@corp.com",
        "poc": "Get POC"
    }
    create_resp = client.post("/api/organizations", json=payload, headers=admin_headers)
    assert create_resp.status_code == 200
    org_id = create_resp.json()["organization"]["id"]

    get_resp = client.get(f"/api/organizations/{org_id}", headers=admin_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["organization"]["id"] == org_id
    assert get_resp.json()["organization"]["name"] == f"Single Get Org {suffix}"

    # Cleanup
    client.delete(f"/api/organizations/{org_id}", headers=admin_headers)

def test_delete_organization(admin_headers):
    import uuid
    suffix = uuid.uuid4().hex[:6]
    payload = {
        "name": f"To Delete Org {suffix}",
        "email": f"delete_{suffix}@corp.com"
    }
    create_resp = client.post("/api/organizations", json=payload, headers=admin_headers)
    assert create_resp.status_code == 200
    org_id = create_resp.json()["organization"]["id"]

    del_resp = client.delete(f"/api/organizations/{org_id}", headers=admin_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "success"

    # Confirm it no longer exists
    list_resp = client.get("/api/organizations", headers=admin_headers)
    assert list_resp.status_code == 200
    org_ids = [o["id"] for o in list_resp.json()["organizations"]]
    assert org_id not in org_ids

@pytest.fixture(scope="module", autouse=True)
def cleanup_test_data():
    yield
    from app.db.session import get_db_connection
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM organizations 
            WHERE name LIKE 'Test Org %' 
               OR name LIKE 'Org One %' 
               OR name LIKE 'Org Two %' 
               OR name LIKE 'Org Three %' 
               OR name LIKE 'Org User Email %' 
               OR name LIKE 'Initial Org %' 
               OR name LIKE 'Updated Org %' 
               OR name LIKE 'Single Get Org %'
               OR name LIKE 'To Delete Org %';
        """)
        conn.commit()
    conn.close()


