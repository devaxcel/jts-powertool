"""
Organization Management Router for JTS-PowerTool Console.
Provides REST endpoints for JTS Admin to create and manage organizations.
Enforces email uniqueness across organization records (allowing email == billing_email for the same organization).
"""
import logging
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth_router import get_user_context
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

organizations_router = APIRouter(prefix="/api/organizations", tags=["Organizations"])

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CreateOrganizationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Organization Name")
    poc: Optional[str] = Field(None, max_length=255, description="Point of Contact (if any)")
    phone: Optional[str] = Field(None, max_length=50, description="Phone number")
    email: Optional[str] = Field(None, max_length=255, description="Primary contact email")
    billing_email: Optional[str] = Field(None, max_length=255, description="Billing email")
    address: Optional[str] = Field(None, description="Physical/mailing address")


class UpdateOrganizationRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Organization Name")
    poc: Optional[str] = Field(None, max_length=255, description="Point of Contact")
    phone: Optional[str] = Field(None, max_length=50, description="Phone number")
    email: Optional[str] = Field(None, max_length=255, description="Primary contact email")
    billing_email: Optional[str] = Field(None, max_length=255, description="Billing email")
    address: Optional[str] = Field(None, description="Physical/mailing address")


def require_admin(request: Request):
    """Enforces JTS Admin role for organization management."""
    user_ctx = get_user_context(request, ignore_simulation=True)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    if actual_role in ("client_admin", "client_standard"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: JTS Admin RBAC permission required to manage organizations.",
        )


def ensure_organizations_resequenced(cur):
    """Ensures any legacy organization records with ID < 101 are migrated to 101+."""
    try:
        cur.execute("SELECT 1 FROM organizations WHERE id < 101 LIMIT 1;")
        if cur.fetchone():
            cur.execute("""
                DO $$
                DECLARE
                    rec RECORD;
                    new_id_val INT := 101;
                BEGIN
                    IF EXISTS (SELECT 1 FROM organizations WHERE id < 101) THEN
                        UPDATE organizations SET id = id + 100000;
                        FOR rec IN SELECT id FROM organizations ORDER BY id ASC LOOP
                            UPDATE dashboard_users SET organization_id = new_id_val WHERE organization_id = rec.id;
                            UPDATE organizations SET id = new_id_val WHERE id = rec.id;
                            new_id_val := new_id_val + 1;
                        END LOOP;
                    END IF;
                END $$;
                SELECT setval('organizations_id_seq', (SELECT GREATEST(COALESCE(MAX(id), 100), 100) FROM organizations), true);
            """)
    except Exception as e:
        logger.warning(f"[ORGANIZATIONS] Resequence check notice: {e}")


def find_organization_row(cur, org_id: int):
    """Finds an organization row by exact ID, or bidirectional fallback (101 <-> 1)."""
    # 1. Exact ID
    cur.execute("""
        SELECT id, name, poc, phone, email, billing_email, address, workspace_id, workspace_name, created_at, updated_at
        FROM organizations WHERE id = %s;
    """, (org_id,))
    row = cur.fetchone()
    if row:
        return row
    
    # 2. If org_id >= 101, check org_id - 100 (e.g. 101 -> 1)
    if org_id >= 101:
        cur.execute("""
            SELECT id, name, poc, phone, email, billing_email, address, workspace_id, workspace_name, created_at, updated_at
            FROM organizations WHERE id = %s;
        """, (org_id - 100,))
        row = cur.fetchone()
        if row:
            return row

    # 3. If org_id < 101, check org_id + 100 (e.g. 1 -> 101)
    if org_id < 101:
        cur.execute("""
            SELECT id, name, poc, phone, email, billing_email, address, workspace_id, workspace_name, created_at, updated_at
            FROM organizations WHERE id = %s;
        """, (org_id + 100,))
        row = cur.fetchone()
        if row:
            return row
            
    return None


@organizations_router.get("/next-id")
@organizations_router.get("/next-id/")
def get_next_org_id(request: Request):
    """Returns the preview of the next auto-incrementing Organization ID starting from 101."""
    require_admin(request)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            ensure_organizations_resequenced(cur)
            conn.commit()
            cur.execute("""
                SELECT GREATEST(COALESCE(MAX(id), 0) + 1, 101) AS next_id FROM organizations;
            """)
            row = cur.fetchone()
            next_id = row["next_id"] if isinstance(row, dict) else row[0]
            if not next_id or next_id < 101:
                next_id = 101
            return {"next_id": next_id}
    except Exception as e:
        logger.error(f"[ORGANIZATIONS] Failed to retrieve next org ID: {e}")
        return {"next_id": 101}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@organizations_router.get("")
@organizations_router.get("/")
def list_organizations(request: Request):
    """Lists all registered organizations."""
    require_admin(request)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            ensure_organizations_resequenced(cur)
            conn.commit()
            cur.execute("""
                SELECT id, name, poc, phone, email, billing_email, address, workspace_id, workspace_name, created_at, updated_at
                FROM organizations
                ORDER BY id ASC;
            """)
            rows = cur.fetchall()
            orgs = []
            for r in rows:
                if isinstance(r, dict):
                    created_at = r.get("created_at")
                    updated_at = r.get("updated_at")
                    orgs.append({
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "poc": r.get("poc") or "",
                        "phone": r.get("phone") or "",
                        "email": r.get("email") or "",
                        "billing_email": r.get("billing_email") or "",
                        "address": r.get("address") or "",
                        "workspace_id": r.get("workspace_id"),
                        "workspace_name": r.get("workspace_name"),
                        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
                        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at or ""),
                    })
                else:
                    orgs.append({
                        "id": r[0],
                        "name": r[1],
                        "poc": r[2] or "",
                        "phone": r[3] or "",
                        "email": r[4] or "",
                        "billing_email": r[5] or "",
                        "address": r[6] or "",
                        "workspace_id": r[7],
                        "workspace_name": r[8],
                        "created_at": r[9].isoformat() if hasattr(r[9], "isoformat") else str(r[9] or ""),
                        "updated_at": r[10].isoformat() if hasattr(r[10], "isoformat") else str(r[10] or ""),
                    })
            return {"organizations": orgs, "total": len(orgs)}
    except Exception as e:
        logger.error(f"[ORGANIZATIONS] List error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list organizations: {str(e)}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@organizations_router.get("/{org_id}")
@organizations_router.get("/{org_id}/")
def get_organization(org_id: int, request: Request):
    """Retrieves single organization details by ID (with bidirectional fallback)."""
    require_admin(request)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            ensure_organizations_resequenced(cur)
            conn.commit()
            row = find_organization_row(cur, org_id)
            if not row:
                raise HTTPException(status_code=404, detail=f"Organization with ID {org_id} not found.")

            if isinstance(row, dict):
                created_at = row.get("created_at")
                updated_at = row.get("updated_at")
                return {
                    "organization": {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "poc": row.get("poc") or "",
                        "phone": row.get("phone") or "",
                        "email": row.get("email") or "",
                        "billing_email": row.get("billing_email") or "",
                        "address": row.get("address") or "",
                        "workspace_id": row.get("workspace_id"),
                        "workspace_name": row.get("workspace_name"),
                        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
                        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at or ""),
                    }
                }
            else:
                return {
                    "organization": {
                        "id": row[0],
                        "name": row[1],
                        "poc": row[2] or "",
                        "phone": row[3] or "",
                        "email": row[4] or "",
                        "billing_email": row[5] or "",
                        "address": row[6] or "",
                        "workspace_id": row[7],
                        "workspace_name": row[8],
                        "created_at": row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9] or ""),
                        "updated_at": row[10].isoformat() if hasattr(row[10], "isoformat") else str(row[10] or ""),
                    }
                }
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@organizations_router.post("")
@organizations_router.post("/")
def create_organization(body: CreateOrganizationRequest, request: Request):
    """
    Creates a new organization record.
    Enforces uniqueness of email and billing_email across other organizations.
    (Email and Billing Email can match within the same organization).
    """
    require_admin(request)

    org_name = body.name.strip()
    if not org_name:
        raise HTTPException(status_code=400, detail="Organization Name is required.")

    poc = body.poc.strip() if body.poc else None
    phone = body.phone.strip() if body.phone else None
    email = body.email.strip().lower() if body.email and body.email.strip() else None
    billing_email = body.billing_email.strip().lower() if body.billing_email and body.billing_email.strip() else None
    address = body.address.strip() if body.address else None

    # Format validation if provided
    if email and not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Invalid email format for primary email.")
    if billing_email and not EMAIL_REGEX.match(billing_email):
        raise HTTPException(status_code=400, detail="Invalid email format for billing email.")

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Check for email uniqueness across existing organizations
            # An email or billing_email cannot already exist in any other organization record
            if email:
                cur.execute("""
                    SELECT id, name FROM organizations 
                    WHERE (LOWER(TRIM(email)) = %s OR LOWER(TRIM(billing_email)) = %s)
                    LIMIT 1;
                """, (email, email))
                conflict = cur.fetchone()
                if conflict:
                    conf_name = conflict["name"] if isinstance(conflict, dict) else conflict[1]
                    raise HTTPException(
                        status_code=400,
                        detail=f"Email '{email}' is already registered with organization '{conf_name}'. Please enter a unique email."
                    )

            if billing_email:
                # If billing_email is different from email, check if it's already used by another org
                if billing_email != email:
                    cur.execute("""
                        SELECT id, name FROM organizations 
                        WHERE (LOWER(TRIM(email)) = %s OR LOWER(TRIM(billing_email)) = %s)
                        LIMIT 1;
                    """, (billing_email, billing_email))
                    conflict = cur.fetchone()
                    if conflict:
                        conf_name = conflict["name"] if isinstance(conflict, dict) else conflict[1]
                        raise HTTPException(
                            status_code=400,
                            detail=f"Billing Email '{billing_email}' is already registered with organization '{conf_name}'. Please enter a unique billing email."
                        )

            # Calculate next id starting from 101
            cur.execute("""
                SELECT GREATEST(COALESCE(MAX(id), 0) + 1, 101) AS next_id FROM organizations;
            """)
            id_row = cur.fetchone()
            next_id = id_row["next_id"] if isinstance(id_row, dict) else id_row[0]
            if not next_id or next_id < 101:
                next_id = 101

            # Insert organization
            cur.execute("""
                INSERT INTO organizations (id, name, poc, phone, email, billing_email, address, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id, name, poc, phone, email, billing_email, address, created_at, updated_at;
            """, (next_id, org_name, poc, phone, email, billing_email, address))
            created_org = cur.fetchone()

            try:
                cur.execute("SELECT setval('organizations_id_seq', (SELECT GREATEST(MAX(id), 101) FROM organizations), true);")
            except Exception:
                pass

            conn.commit()

            if isinstance(created_org, dict):
                org_dict = {
                    "id": created_org.get("id"),
                    "name": created_org.get("name"),
                    "poc": created_org.get("poc") or "",
                    "phone": created_org.get("phone") or "",
                    "email": created_org.get("email") or "",
                    "billing_email": created_org.get("billing_email") or "",
                    "address": created_org.get("address") or "",
                    "created_at": created_org.get("created_at").isoformat() if hasattr(created_org.get("created_at"), "isoformat") else str(created_org.get("created_at")),
                }
            else:
                org_dict = {
                    "id": created_org[0],
                    "name": created_org[1],
                    "poc": created_org[2] or "",
                    "phone": created_org[3] or "",
                    "email": created_org[4] or "",
                    "billing_email": created_org[5] or "",
                    "address": created_org[6] or "",
                    "created_at": created_org[7].isoformat() if hasattr(created_org[7], "isoformat") else str(created_org[7]),
                }

            return {
                "status": "success",
                "message": f"Organization '{org_name}' created successfully.",
                "organization": org_dict,
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"[ORGANIZATIONS] Create error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error creating organization: {str(e)}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@organizations_router.put("/{org_id}")
@organizations_router.put("/{org_id}/")
@organizations_router.patch("/{org_id}")
@organizations_router.patch("/{org_id}/")
@organizations_router.post("/{org_id}")
@organizations_router.post("/{org_id}/")
@organizations_router.post("/{org_id}/update")
@organizations_router.post("/{org_id}/update/")
def update_organization(org_id: int, body: UpdateOrganizationRequest, request: Request):
    """Updates an existing organization record (with bidirectional fallback for legacy IDs)."""
    require_admin(request)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            ensure_organizations_resequenced(cur)
            conn.commit()

            existing = find_organization_row(cur, org_id)
            if not existing:
                raise HTTPException(status_code=404, detail=f"Organization with ID {org_id} not found.")

            resolved_id = existing["id"] if isinstance(existing, dict) else existing[0]
            existing_name = existing["name"] if isinstance(existing, dict) else existing[1]
            existing_poc = existing["poc"] if isinstance(existing, dict) else existing[2]
            existing_phone = existing["phone"] if isinstance(existing, dict) else existing[3]
            existing_email = existing["email"] if isinstance(existing, dict) else existing[4]
            existing_billing = existing["billing_email"] if isinstance(existing, dict) else existing[5]
            existing_addr = existing["address"] if isinstance(existing, dict) else existing[6]

            org_name = body.name.strip() if body.name is not None and body.name.strip() else existing_name
            poc = body.poc.strip() if body.poc is not None else existing_poc
            phone = body.phone.strip() if body.phone is not None else existing_phone
            email = body.email.strip().lower() if body.email is not None and body.email.strip() else (None if body.email == "" else existing_email)
            billing_email = body.billing_email.strip().lower() if body.billing_email is not None and body.billing_email.strip() else (None if body.billing_email == "" else existing_billing)
            address = body.address.strip() if body.address is not None else existing_addr

            # Format validation
            if email and not EMAIL_REGEX.match(email):
                raise HTTPException(status_code=400, detail="Invalid email format for primary email.")
            if billing_email and not EMAIL_REGEX.match(billing_email):
                raise HTTPException(status_code=400, detail="Invalid email format for billing email.")

            # Email uniqueness check against OTHER organizations
            if email:
                cur.execute("""
                    SELECT id, name FROM organizations 
                    WHERE (LOWER(TRIM(email)) = %s OR LOWER(TRIM(billing_email)) = %s)
                      AND id != %s
                    LIMIT 1;
                """, (email, email, resolved_id))
                conflict = cur.fetchone()
                if conflict:
                    conf_name = conflict["name"] if isinstance(conflict, dict) else conflict[1]
                    raise HTTPException(
                        status_code=400,
                        detail=f"Email '{email}' is already registered with organization '{conf_name}'. Please enter a unique email."
                    )

            if billing_email:
                if billing_email != email:
                    cur.execute("""
                        SELECT id, name FROM organizations 
                        WHERE (LOWER(TRIM(email)) = %s OR LOWER(TRIM(billing_email)) = %s)
                          AND id != %s
                        LIMIT 1;
                    """, (billing_email, billing_email, resolved_id))
                    conflict = cur.fetchone()
                    if conflict:
                        conf_name = conflict["name"] if isinstance(conflict, dict) else conflict[1]
                        raise HTTPException(
                            status_code=400,
                            detail=f"Billing Email '{billing_email}' is already registered with organization '{conf_name}'. Please enter a unique billing email."
                        )

            cur.execute("""
                UPDATE organizations
                SET name = %s, poc = %s, phone = %s, email = %s, billing_email = %s, address = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, name, poc, phone, email, billing_email, address, created_at, updated_at;
            """, (org_name, poc, phone, email, billing_email, address, resolved_id))
            updated_org = cur.fetchone()
            conn.commit()

            if isinstance(updated_org, dict):
                org_dict = {
                    "id": updated_org.get("id"),
                    "name": updated_org.get("name"),
                    "poc": updated_org.get("poc") or "",
                    "phone": updated_org.get("phone") or "",
                    "email": updated_org.get("email") or "",
                    "billing_email": updated_org.get("billing_email") or "",
                    "address": updated_org.get("address") or "",
                    "created_at": updated_org.get("created_at").isoformat() if hasattr(updated_org.get("created_at"), "isoformat") else str(updated_org.get("created_at")),
                    "updated_at": updated_org.get("updated_at").isoformat() if hasattr(updated_org.get("updated_at"), "isoformat") else str(updated_org.get("updated_at")),
                }
            else:
                org_dict = {
                    "id": updated_org[0],
                    "name": updated_org[1],
                    "poc": updated_org[2] or "",
                    "phone": updated_org[3] or "",
                    "email": updated_org[4] or "",
                    "billing_email": updated_org[5] or "",
                    "address": updated_org[6] or "",
                    "created_at": updated_org[7].isoformat() if hasattr(updated_org[7], "isoformat") else str(updated_org[7]),
                    "updated_at": updated_org[8].isoformat() if hasattr(updated_org[8], "isoformat") else str(updated_org[8]),
                }

            return {
                "status": "success",
                "message": f"Organization '{org_name}' updated successfully.",
                "organization": org_dict,
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"[ORGANIZATIONS] Update error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error updating organization: {str(e)}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@organizations_router.delete("/{org_id}")
@organizations_router.delete("/{org_id}/")
@organizations_router.post("/{org_id}/delete")
@organizations_router.post("/{org_id}/delete/")
def delete_organization(org_id: int, request: Request):
    """Deletes an organization record and removes associations (with bidirectional fallback)."""
    require_admin(request)
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            ensure_organizations_resequenced(cur)
            conn.commit()

            existing = find_organization_row(cur, org_id)
            if not existing:
                raise HTTPException(status_code=404, detail=f"Organization with ID {org_id} not found.")
            resolved_id = existing["id"] if isinstance(existing, dict) else existing[0]
            org_name = existing["name"] if isinstance(existing, dict) else existing[1]

            # Unset organization_id from dashboard_users
            try:
                cur.execute("UPDATE dashboard_users SET organization_id = NULL WHERE organization_id = %s;", (resolved_id,))
            except Exception:
                pass

            cur.execute("DELETE FROM organizations WHERE id = %s;", (resolved_id,))
            conn.commit()

            return {
                "status": "success",
                "message": f"Organization '{org_name}' (ID: {resolved_id}) deleted successfully."
            }
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"[ORGANIZATIONS] Delete error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error deleting organization: {str(e)}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
