"""
Channel-Specific Secrets & API Key Management Router.
Provides REST endpoints for JTS Admin to manage channel API keys.
Enforces JTS Admin RBAC.
Treats 1 Slack Channel = 1 Project.
NEVER returns or logs API key values.
"""
import logging
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel, Field

from app.auth_router import get_user_context, get_folder_channel_ids, get_user_from_db

from app.services.channel_secrets_service import (
    store_folder_api_key,
    get_folder_api_key_status,
    delete_folder_api_key,
    validate_anthropic_key,
    store_channel_secret,
    delete_channel_secret,
    list_channel_secrets,
    list_all_channels,
    set_channel_name,
    create_channel_folder,
    list_channel_folders,
    update_channel_folder,
    delete_channel_folder,
    set_channel_folder,
    get_channel_folder,
    create_slack_channel,
    sync_bot_conversations_from_slack,
    list_unassigned_channels,
    list_vault_secrets,
    store_vault_secret,
    delete_vault_secret,
)
from app.tools.secrets_manager import get_secret
from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

channel_secrets_router = APIRouter(prefix="/api/channels", tags=["Channel API Keys"])


# --- RBAC Dependency ---
def require_jts_admin(request: Request) -> str:
    """
    Signed-in JTS Admin only, based on the real login (a JTS admin previewing a client role still counts as admin).
    Role headers and shared admin tokens are never accepted.
    """
    user_ctx = get_user_context(request, ignore_simulation=True)
    actual_role = user_ctx.get("actual_role") or user_ctx.get("role")
    has_session = bool(user_ctx.get("username")) and _has_valid_session(request)
    if not has_session or actual_role != "jts_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only JTS admins can do this.",
        )
    return user_ctx.get("username") or "admin"


def _has_valid_session(request: Request) -> bool:
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE_NAME)
    return bool(token and verify_session_token(token))


# --- Request/Response Models ---
class SaveSecretRequest(BaseModel):
    provider: str = Field(..., description="Provider name, e.g. 'anthropic', 'github', 'openai'")
    api_key: str = Field(..., description="API key value (stored exclusively in AWS Secrets Manager)")
    channel_name: Optional[str] = Field(None, description="Human friendly project/channel name, e.g. '#marketing-bot'")


class UpdateChannelNameRequest(BaseModel):
    channel_name: str = Field(..., min_length=1, max_length=255, description="Friendly project or channel name, e.g. '#marketing-bot'")


class CreateFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Folder name, e.g. 'Production Projects'")
    description: Optional[str] = Field(None, description="Optional description for the folder")


class UpdateFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Folder name")
    description: Optional[str] = Field(None, description="Optional description for the folder")


class SetChannelFolderRequest(BaseModel):
    folder_id: Optional[int] = Field(None, description="Target folder ID or null to unassign from folder")


class CreateSlackChannelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80, description="Slack channel name (e.g. 'proj-alpha')")
    is_private: bool = Field(False, description="Whether the channel should be private in Slack")
    topic: Optional[str] = Field(None, description="Optional topic/description for the channel")



from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from app.auth_router import get_user_context, get_folder_channel_ids, verify_session_token, SESSION_COOKIE_NAME


# --- Endpoints ---
@channel_secrets_router.get("/folders", summary="List all channel folders")
def get_folders_endpoint(request: Request):
    """Lists all channel/project folders saved in the database."""
    user_ctx = get_user_context(request)
    folders = list_channel_folders()

    if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
        target_id = user_ctx["client_folder_id"]
        folders = [f for f in folders if f.get("id") == target_id]

    return {"folders": folders, "total": len(folders)}


@channel_secrets_router.post("/folders", summary="Create a new channel folder")
def create_folder_endpoint(
    payload: CreateFolderRequest,
    _: str = Depends(require_jts_admin),
):
    """Creates a new folder permanently in the database."""
    try:
        folder = create_channel_folder(name=payload.name, description=payload.description)
        return {
            "status": "success",
            "message": f"Folder '{folder['name']}' created successfully.",
            "folder": folder,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error creating folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@channel_secrets_router.patch("/folders/{folder_id}", summary="Update or rename a channel folder")
def update_folder_endpoint(
    folder_id: int,
    payload: UpdateFolderRequest,
    _: str = Depends(require_jts_admin),
):
    """Renames or updates an existing channel folder."""
    try:
        folder = update_channel_folder(folder_id=folder_id, name=payload.name, description=payload.description)
        return {
            "status": "success",
            "message": f"Folder renamed to '{folder['name']}'.",
            "folder": folder,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error updating folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@channel_secrets_router.delete("/folders/{folder_id}", summary="Delete a channel folder")
def delete_folder_endpoint(
    folder_id: int,
    _: str = Depends(require_jts_admin),
):
    """Deletes a folder from the database (unlinks channels)."""
    try:
        success = delete_channel_folder(folder_id=folder_id)
        return {
            "status": "success" if success else "not_found",
            "message": "Folder deleted successfully." if success else f"Folder {folder_id} not found.",
        }
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error deleting folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Client's own Anthropic key (per client folder) ---
class SaveFolderKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=1, description="Client's own Anthropic API key (stored in AWS Secrets Manager)")


def _authorize_folder_key(request: Request, folder_id: int, write: bool) -> str:
    """
    JTS Admin: any folder. Client Admin: own folder, read & write. Client Standard: own folder, read only.
    Returns the acting username.
    """
    user_ctx = get_user_context(request, ignore_simulation=True)
    role = user_ctx.get("actual_role") or user_ctx.get("role")
    username = user_ctx.get("username") or "admin"

    if role in ("jts_admin", "admin"):
        return username
    if role in ("client_admin", "client_standard"):
        db_user = get_user_from_db(username) if username else None
        assigned_folder = db_user.get("client_folder_id") if db_user else None
        if not assigned_folder or int(assigned_folder) != int(folder_id):
            raise HTTPException(status_code=403, detail="Access denied: You can only manage the API key of your own organization.")
        if write and role != "client_admin":
            raise HTTPException(status_code=403, detail="Access denied: Only a Client Admin can add or remove the API key.")
        return username
    raise HTTPException(status_code=401, detail="Not authenticated")


@channel_secrets_router.get("/folders/{folder_id}/anthropic-key", summary="Billing mode & client API key status (never returns the key)")
def get_folder_key_endpoint(folder_id: int, request: Request):
    _authorize_folder_key(request, folder_id, write=False)
    return get_folder_api_key_status(folder_id, "anthropic")


@channel_secrets_router.put("/folders/{folder_id}/anthropic-key", summary="Add or replace the client's own Anthropic key")
def save_folder_key_endpoint(folder_id: int, payload: SaveFolderKeyRequest, request: Request):
    actor = _authorize_folder_key(request, folder_id, write=True)
    api_key = payload.api_key.strip()
    if not api_key.startswith("sk-ant-"):
        raise HTTPException(status_code=400, detail="This doesn't look like an Anthropic API key (it should start with 'sk-ant-').")
    ok, msg = validate_anthropic_key(api_key)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    try:
        result = store_folder_api_key(folder_id=folder_id, api_key=api_key, provider="anthropic", updated_by=actor)
    except LookupError as le:
        raise HTTPException(status_code=404, detail=str(le))
    except Exception as e:
        logger.error(f"[FOLDER_KEYS] Error saving client key for folder {folder_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to store API key: {e}")
    logger.info(f"[FOLDER_KEYS] Client Anthropic key set for folder {folder_id} by '{actor}' ({result.get('key_hint')})")
    return {"status": "success", "message": "Your own Anthropic key is active. Usage will no longer be billed by JTS.", **result}


@channel_secrets_router.delete("/folders/{folder_id}/anthropic-key", summary="Remove the client's own key (falls back to billed JTS key)")
def delete_folder_key_endpoint(folder_id: int, request: Request):
    actor = _authorize_folder_key(request, folder_id, write=True)
    removed = delete_folder_api_key(folder_id, "anthropic")
    logger.info(f"[FOLDER_KEYS] Client Anthropic key removed for folder {folder_id} by '{actor}' (existed={removed})")
    return {
        "status": "success",
        "message": "Your key was removed. The JTS key is now used and usage is billed to your organization.",
        **get_folder_api_key_status(folder_id, "anthropic"),
    }


@channel_secrets_router.get("/folders/{folder_id}", summary="Get folder details and its channels")
def get_folder_details_endpoint(
    folder_id: int,
    request: Request,
):
    """Retrieves a folder and all channels assigned to it."""
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
        if folder_id != user_ctx["client_folder_id"]:
            raise HTTPException(status_code=403, detail="Access denied: You are only authorized to access your assigned client folder.")

    folder = get_channel_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail=f"Folder with ID {folder_id} not found.")
    return {"status": "success", "folder": folder, "channels": folder.get("channels", [])}


@channel_secrets_router.post("/folders/{folder_id}/channels", summary="Create a new Slack channel inside folder")
def create_slack_channel_in_folder_endpoint(
    folder_id: int,
    payload: CreateSlackChannelRequest,
    _: str = Depends(require_jts_admin),
):
    """
    Creates a new Slack channel in the workspace via the Slack Web API,
    and assigns it permanently to the specified folder in PostgreSQL.
    """
    try:
        channel = create_slack_channel(
            name=payload.name,
            folder_id=folder_id,
            is_private=payload.is_private,
            topic=payload.topic,
        )
        return {
            "status": "success",
            "message": f"Slack channel '{channel['channel_name']}' created successfully and saved in folder.",
            "channel": channel,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error creating Slack channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@channel_secrets_router.patch("/{channel_id}/folder", summary="Assign or move channel to a folder")
def set_channel_folder_endpoint(
    channel_id: str,
    payload: SetChannelFolderRequest,
    request: Request,
):
    """Assigns or unassigns a channel to a folder."""
    # Moving a channel moves its conversations, billing and approvals, so only JTS admins may do it.
    require_jts_admin(request)

    try:
        result = set_channel_folder(channel_id=channel_id, folder_id=payload.folder_id)
        msg = f"Channel '{channel_id}' moved to folder '{result['folder_name']}'." if result.get("folder_name") else f"Channel '{channel_id}' unassigned from folder."
        return {
            "status": "success",
            "message": msg,
            **result,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error assigning channel to folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@channel_secrets_router.post("/sync", summary="Sync all Slack channels and DMs where the bot is present")
def sync_slack_channels_endpoint(_: str = Depends(require_jts_admin)):
    """
    Proactively queries Slack's users.conversations API to discover all channels and DMs
    where the bot has been added, and registers them into PostgreSQL channel_metadata.
    """
    try:
        result = sync_bot_conversations_from_slack()
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error syncing Slack channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class WorkspaceClientPayload(BaseModel):
    folder_id: Optional[int] = None


@channel_secrets_router.get("/workspaces", summary="Connected Slack workspaces and the client each is linked to")
def list_workspaces_endpoint(_: str = Depends(require_jts_admin)):
    from app.services.channel_secrets_service import list_workspaces_with_clients
    return {"workspaces": list_workspaces_with_clients()}


@channel_secrets_router.put("/workspaces/{team_id}", summary="Link a Slack workspace to a client (or unlink it)")
def set_workspace_client_endpoint(team_id: str, payload: WorkspaceClientPayload, _: str = Depends(require_jts_admin)):
    from app.services.channel_secrets_service import set_workspace_client
    try:
        result = set_workspace_client(team_id, payload.folder_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Linking workspace {team_id} failed: {e}")
        raise HTTPException(status_code=500, detail="Couldn't link the workspace. Please try again.")
    msg = (f"Chats in this workspace that aren't in another client's folder now belong to {result['folder_name']}."
           if result["folder_id"] else "The workspace is no longer linked to a client.")
    return {"ok": True, "message": msg, **result}


@channel_secrets_router.get("/unassigned", summary="List discovered channels not assigned to any folder")
def get_unassigned_channels_endpoint(_: str = Depends(require_jts_admin)):
    """
    Returns all channels and members discovered from Slack or conversation memory
    that have not yet been assigned to any folder.
    """
    try:
        channels = list_unassigned_channels()
        return {"status": "success", "channels": channels, "total": len(channels)}
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error listing unassigned channels: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@channel_secrets_router.get("", summary="List all channels with configured secret counts")
def get_channels(request: Request):
    """Lists all active and configured channels (1 channel = 1 project)."""
    user_ctx = get_user_context(request)
    if not user_ctx.get("role") or not _has_valid_session(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: JTS Admin RBAC permission required.",
        )
    channels = list_all_channels()

    if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
        target_id = user_ctx["client_folder_id"]
        channels = [c for c in channels if c.get("folder_id") == target_id]

    return {"channels": channels, "total": len(channels)}


@channel_secrets_router.patch("/{channel_id}/name", summary="Rename or update friendly name for a channel")
def update_channel_name_endpoint(
    channel_id: str,
    payload: UpdateChannelNameRequest,
    _: str = Depends(require_jts_admin),
):
    """Updates or sets the friendly project name for a channel."""
    try:
        updated_name = set_channel_name(channel_id, payload.channel_name)
        return {
            "status": "success",
            "message": f"Channel '{channel_id}' renamed to '{updated_name}'",
            "channel_id": channel_id,
            "channel_name": updated_name,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error updating channel name: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@channel_secrets_router.get("/{channel_id}/secrets", summary="List configured secrets metadata for a channel")
def get_channel_secrets(channel_id: str, request: Request):
    """
    Lists configured secrets metadata for a specific channel.
    SECURITY: Never returns the secret values. Only safe metadata.
    """
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
        folder_channels = set(get_folder_channel_ids(user_ctx["client_folder_id"]))
        if channel_id not in folder_channels:
            raise HTTPException(status_code=403, detail="Access denied: Channel does not belong to your assigned folder.")

    secrets = list_channel_secrets(channel_id)
    return {"channel_id": channel_id, "secrets": secrets, "total": len(secrets)}


@channel_secrets_router.get("/{channel_id}/messages", summary="Get all conversation messages for a channel")
def get_channel_messages_endpoint(channel_id: str, request: Request, limit: int = 250):
    """
    Retrieves all conversation messages for a channel.
    Scoped by folder RBAC if caller is client_admin or client_standard.
    """
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard"):
        folder_id = user_ctx.get("client_folder_id")
        folder_channels = set(get_folder_channel_ids(folder_id)) if folder_id else set()
        clean_cid = channel_id.lstrip("#@")
        
        is_allowed = (
            channel_id in folder_channels
            or channel_id.lower() in folder_channels
            or clean_cid in folder_channels
            or clean_cid.lower() in folder_channels
            or f"#{clean_cid.lower()}" in folder_channels
        )

        if not is_allowed:
            raise HTTPException(status_code=403, detail="Access denied: Channel does not belong to your assigned folder.")

    conn = get_db_connection()
    try:
        channel_name = channel_id
        c_candidates = set()
        c_candidates.add(channel_id)
        c_candidates.add(channel_id.lower())
        c_candidates.add(channel_id.lstrip("#@"))
        c_candidates.add(channel_id.lstrip("#@").lower())

        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT channel_id, channel_name 
                    FROM channel_metadata 
                    WHERE LOWER(channel_id) = LOWER(%s) OR LOWER(channel_name) = LOWER(%s) 
                       OR LOWER(channel_id) = LOWER(%s) OR LOWER(channel_name) = LOWER(%s);
                """, (channel_id, channel_id, channel_id.lstrip("#@"), channel_id.lstrip("#@")))
                meta_rows = cur.fetchall() or []
                for mr in meta_rows:
                    cid = mr["channel_id"] if isinstance(mr, dict) else mr[0]
                    cnm = mr["channel_name"] if isinstance(mr, dict) else mr[1]
                    for item in (cid, cnm):
                        if item:
                            clean = str(item).strip()
                            if clean:
                                c_candidates.add(clean)
                                c_candidates.add(clean.lower())
                                bare = clean.lstrip("#@")
                                if bare:
                                    c_candidates.add(bare)
                                    c_candidates.add(bare.lower())
                                    c_candidates.add(f"#{bare.lower()}")
                                    c_candidates.add(f"@{bare.lower()}")
                                if cnm and (not channel_name or channel_name == channel_id or channel_name.startswith("C0") or channel_name.startswith("D0")):
                                    channel_name = cnm
            except Exception as meta_err:
                logger.warning(f"[CHANNEL_MESSAGES] Could not fetch metadata candidates: {meta_err}")

            if not channel_name or channel_name == channel_id or channel_name.startswith("C0") or channel_name.startswith("D0"):
                try:
                    from app.services.channel_secrets_service import resolve_slack_channel_name
                    resolved = resolve_slack_channel_name(channel_id)
                    if resolved and resolved != channel_id and not resolved.startswith("C0"):
                        channel_name = resolved
                except Exception:
                    pass

            # Add canonical aliases for known channels
            cid_up = channel_id.upper().strip()
            if cid_up in ("C08V6S5UJ0P", "C0BV6S5UJ0P", "CO8V6S5UJ0P"):
                c_candidates.update(["C08V6S5UJ0P", "c08v6s5uj0p", "C0BV6S5UJ0P", "c0bv6s5uj0p", "CO8V6S5UJ0P", "co8v6s5uj0p", "agents_working_projects", "#agents_working_projects"])
            elif cid_up in ("C08MV3EM9PY", "C0BMV3EM9PY", "CO8MV3EM9PY"):
                c_candidates.update(["C08MV3EM9PY", "c08mv3em9py", "C0BMV3EM9PY", "c0bmv3em9py", "CO8MV3EM9PY", "co8mv3em9py"])
            elif cid_up in ("D08SLP9LXUZ", "D0BSLP9LXUZ", "DO8SLP9LXUZ"):
                c_candidates.update(["D08SLP9LXUZ", "d08slp9lxuz", "D0BSLP9LXUZ", "d0bslp9lxuz", "DO8SLP9LXUZ", "do8slp9lxuz"])

            cand_list = list(c_candidates)
            cand_lower = [x.lower() for x in cand_list]

            # Ensure columns exist in database table
            try:
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6) DEFAULT 0.0;")
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

            rows = []
            try:
                cur.execute("""
                    SELECT id, team_id, workspace_name, channel_id, thread_ts, user_id, user_name, role, content, message_ts, created_at,
                           COALESCE(input_tokens, 0) as input_tokens,
                           COALESCE(output_tokens, 0) as output_tokens,
                           COALESCE(total_tokens, 0) as total_tokens,
                           COALESCE(cost_usd, 0.0) as cost_usd
                    FROM conversation_messages
                    WHERE channel_id = ANY(%s) 
                       OR LOWER(channel_id) = ANY(%s)
                       OR LOWER(channel_id) IN (
                           SELECT LOWER(channel_id) FROM channel_metadata WHERE LOWER(channel_name) = ANY(%s) OR LOWER(channel_id) = ANY(%s)
                       )
                       OR LOWER(channel_id) IN (
                           SELECT LOWER(channel_name) FROM channel_metadata WHERE LOWER(channel_name) = ANY(%s) OR LOWER(channel_id) = ANY(%s)
                       )
                    ORDER BY id ASC
                    LIMIT %s;
                """, (cand_list, cand_lower, cand_lower, cand_lower, cand_lower, cand_lower, limit))
                rows = cur.fetchall() or []
            except Exception as q_err:
                logger.warning(f"[CHANNEL_MESSAGES] Query with telemetry columns failed, falling back to base columns: {q_err}")
                try:
                    conn.rollback()
                except Exception:
                    pass
                cur.execute("""
                    SELECT id, team_id, workspace_name, channel_id, thread_ts, user_id, user_name, role, content, message_ts, created_at
                    FROM conversation_messages
                    WHERE channel_id = ANY(%s) 
                       OR LOWER(channel_id) = ANY(%s)
                       OR LOWER(channel_id) IN (
                           SELECT LOWER(channel_id) FROM channel_metadata WHERE LOWER(channel_name) = ANY(%s) OR LOWER(channel_id) = ANY(%s)
                       )
                       OR LOWER(channel_id) IN (
                           SELECT LOWER(channel_name) FROM channel_metadata WHERE LOWER(channel_name) = ANY(%s) OR LOWER(channel_id) = ANY(%s)
                       )
                    ORDER BY id ASC
                    LIMIT %s;
                """, (cand_list, cand_lower, cand_lower, cand_lower, cand_lower, cand_lower, limit))
                raw_rows = cur.fetchall() or []
                rows = []
                for r in raw_rows:
                    d = dict(r)
                    d["input_tokens"] = 0
                    d["output_tokens"] = 0
                    d["total_tokens"] = 0
                    d["cost_usd"] = 0.0
                    rows.append(d)

            # Fetch channel usage logs and summary telemetry from api_usage_logs (authoritative billing source)
            usage_logs = []
            channel_telemetry = None
            try:
                cur.execute("""
                    SELECT input_tokens, output_tokens, total_tokens, cost_usd, created_at
                    FROM api_usage_logs
                    WHERE channel_id = ANY(%s) OR LOWER(channel_id) = ANY(%s)
                    ORDER BY id ASC;
                """, (cand_list, cand_lower))
                usage_logs = cur.fetchall() or []

                cur.execute("""
                    SELECT 
                        COUNT(*) as calls,
                        COALESCE(SUM(input_tokens), 0) as input_tokens,
                        COALESCE(SUM(output_tokens), 0) as output_tokens,
                        COALESCE(SUM(total_tokens), 0) as total_tokens,
                        COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
                    FROM api_usage_logs
                    WHERE channel_id = ANY(%s) OR LOWER(channel_id) = ANY(%s);
                """, (cand_list, cand_lower))
                t_row = cur.fetchone()
                if t_row and int(t_row.get("calls") or 0) > 0:
                    channel_telemetry = {
                        "calls": int(t_row["calls"]),
                        "input_tokens": int(t_row["input_tokens"]),
                        "output_tokens": int(t_row["output_tokens"]),
                        "total_tokens": int(t_row["total_tokens"]),
                        "total_cost_usd": float(t_row["total_cost_usd"]),
                    }
            except Exception as u_err:
                logger.debug(f"[CHANNEL_MESSAGES] Usage log fallback lookup error: {u_err}")

            usage_idx = 0
            messages = []
            for r in rows:
                msg = dict(r)
                if msg.get("created_at"):
                    msg["created_at"] = msg["created_at"].isoformat()

                is_bot = (
                    (msg.get("role") == "assistant")
                    or (msg.get("user_id") == "bot")
                    or ("assistant" in str(msg.get("role", "")).lower())
                    or ("bot" in str(msg.get("user_name", "")).lower())
                )
                if is_bot:
                    if usage_logs:
                        # Authoritative mapping from api_usage_logs
                        if usage_idx < len(usage_logs):
                            ulog = usage_logs[usage_idx]
                            usage_idx += 1
                            msg["input_tokens"] = int(ulog.get("input_tokens") or 0)
                            msg["output_tokens"] = int(ulog.get("output_tokens") or 0)
                            msg["total_tokens"] = int(ulog.get("total_tokens") or 0)
                            msg["cost_usd"] = float(ulog.get("cost_usd") or 0.0)
                        else:
                            # Non-API bot message (status notification, proposal card, etc.)
                            msg["input_tokens"] = 0
                            msg["output_tokens"] = 0
                            msg["total_tokens"] = 0
                            msg["cost_usd"] = 0.0
                    else:
                        msg_toks = msg.get("total_tokens") or 0
                        msg_cost = float(msg.get("cost_usd") or 0.0)
                        # Only estimate when tokens are missing; zero cost with real tokens = client's own key (not billed)
                        if not msg_toks:
                            content_text = msg.get("content") or ""
                            out_toks = max(20, int(len(content_text) / 3.8))
                            in_toks = max(250, int(out_toks * 2.5))
                            tot_toks = in_toks + out_toks
                            c_usd = round((in_toks * 3.0 / 1_000_000.0) + (out_toks * 15.0 / 1_000_000.0), 6)
                            msg["input_tokens"] = in_toks
                            msg["output_tokens"] = out_toks
                            msg["total_tokens"] = tot_toks
                            msg["cost_usd"] = c_usd
                        else:
                            msg["cost_usd"] = msg_cost

                messages.append(msg)

            if not channel_telemetry:
                bot_msgs = [m for m in messages if m.get("role") == "assistant" or m.get("user_id") == "bot"]
                if bot_msgs:
                    channel_telemetry = {
                        "calls": len(bot_msgs),
                        "input_tokens": sum(m.get("input_tokens") or 0 for m in bot_msgs),
                        "output_tokens": sum(m.get("output_tokens") or 0 for m in bot_msgs),
                        "total_tokens": sum(m.get("total_tokens") or 0 for m in bot_msgs),
                        "total_cost_usd": round(sum(float(m.get("cost_usd") or 0.0) for m in bot_msgs), 6),
                    }

            return {
                "status": "success",
                "channel_id": channel_id,
                "channel_name": channel_name,
                "telemetry": channel_telemetry,
                "messages": messages,
                "total": len(messages)
            }
    except Exception as e:
        logger.error(f"[CHANNEL_MESSAGES] Error fetching messages for channel {channel_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()


@channel_secrets_router.post("/{channel_id}/secrets", summary="Add or update API key for a channel")
def save_channel_secret_endpoint(
    channel_id: str,
    payload: SaveSecretRequest,
    admin_user: str = Depends(require_jts_admin),
):
    """
    Securely stores an API key in AWS Secrets Manager and creates/updates the PostgreSQL mapping.
    SECURITY: The API key is sent directly to AWS Secrets Manager and never saved to PostgreSQL or returned.
    """
    try:
        result = store_channel_secret(
            channel_id=channel_id,
            provider=payload.provider,
            api_key=payload.api_key,
            channel_name=payload.channel_name,
            updated_by=admin_user,
        )
        return {
            "status": "success",
            "message": f"Successfully configured secret for provider '{payload.provider}' in channel '{channel_id}'",
            "mapping": result,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error saving secret: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to store secret in AWS Secrets Manager: {str(e)}")


@channel_secrets_router.delete("/{channel_id}/secrets/{provider}", summary="Delete API key for a channel")
def delete_channel_secret_endpoint(
    channel_id: str,
    provider: str,
    admin_user: str = Depends(require_jts_admin),
):
    """
    Deletes the secret from AWS Secrets Manager and removes the PostgreSQL mapping.
    """
    try:
        success = delete_channel_secret(channel_id, provider, updated_by=admin_user)
        return {
            "status": "success" if success else "not_found",
            "message": f"Secret for provider '{provider}' in channel '{channel_id}' has been deleted.",
        }
    except Exception as e:
        logger.error(f"[CHANNEL_SECRETS_ROUTER] Error deleting secret: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Secrets Vault Router ---
vault_router = APIRouter(prefix="/api/vault", tags=["Secrets Vault"])


class SaveVaultSecretRequest(BaseModel):
    key_name: str = Field(..., min_length=1, max_length=255, description="Key name, e.g. 'OpenAI Key', 'GitHub Token'")
    key_value: str = Field(..., min_length=1, description="Secret value (stored exclusively in AWS Secrets Manager)")


@vault_router.get("", summary="List all vault secrets metadata")
def get_vault_secrets(_: str = Depends(require_jts_admin)):
    secrets = list_vault_secrets()
    return {"secrets": secrets, "total": len(secrets)}


@vault_router.post("", summary="Store a key-value secret in vault")
def save_vault_secret_endpoint(
    payload: SaveVaultSecretRequest,
    request: Request,
    _: str = Depends(require_jts_admin),
):
    try:
        formatted_key_name = re.sub(r"\s+", "_", payload.key_name.strip()).upper()

        # Look up creator's Full Name from User Management / session
        user_ctx = get_user_context(request)
        username = user_ctx.get("username")
        creator_name = None
        if username:
            try:
                from app.auth_router import get_user_from_db
                db_u = get_user_from_db(username)
                if db_u:
                    creator_name = db_u.get("name") or db_u.get("username")
            except Exception:
                pass
        if not creator_name:
            creator_name = username or "JTS Admin"

        res = store_vault_secret(key_name=formatted_key_name, key_value=payload.key_value, name=creator_name)
        return {"status": "success", "message": f"Saved secret '{formatted_key_name}' to vault", "secret": res}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"[VAULT_ROUTER] Error saving vault secret: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@vault_router.delete("/{vault_id}", summary="Delete a secret from vault")
def delete_vault_secret_endpoint(
    vault_id: int,
    _: str = Depends(require_jts_admin),
):
    try:
        success = delete_vault_secret(vault_id)
        return {"status": "success" if success else "not_found", "message": "Secret removed from vault"}
    except Exception as e:
        logger.error(f"[VAULT_ROUTER] Error deleting vault secret: {e}")
        raise HTTPException(status_code=500, detail=str(e))

