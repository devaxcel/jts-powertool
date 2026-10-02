"""
Channel-Specific API Key Management Service.
Treats 1 Slack Channel = 1 Project.
All actual API keys are stored exclusively in AWS Secrets Manager using EC2 IAM role.
PostgreSQL stores ONLY the metadata mapping (channel_id -> provider -> aws_secret_name).
Never returns or logs plaintext API keys.
"""
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Any
import httpx
from dotenv import load_dotenv

from app.db.session import get_db_connection

load_dotenv()
logger = logging.getLogger(__name__)

# In-memory TTL cache for resolved channel secrets to prevent rate-limiting during streams
# Format: {(channel_id, provider): (secret_value, expire_timestamp)}
_SECRET_VALUE_CACHE: Dict[tuple, tuple] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes

# In-memory cache for resolved human-friendly channel names
_CHANNEL_NAME_CACHE: Dict[str, str] = {}

CANONICAL_CHANNEL_MAP: Dict[str, str] = {}

def canonical_channel_id(
    channel_id: Optional[str],
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    channel_name: Optional[str] = None,
) -> str:
    """Preserves and normalizes Slack channel ID per-workspace."""
    if not channel_id:
        return ""
    cid = str(channel_id).strip()
    return CANONICAL_CHANNEL_MAP.get(cid.upper(), cid)

OBSOLETE_CHANNEL_IDS = set()

AUTHORITATIVE_CHANNEL_WORKSPACES: Dict[str, tuple[str, str]] = {
    "C08MV3EM9PY": ("T5ZMF56H5", "Axcel World"),
    "C0BMV3EM9PY": ("T5ZMF56H5", "Axcel World"),
    "C08V6S5UJ0P": ("T5ZMF56H5", "Axcel World"),
    "C0BV6S5UJ0P": ("T5ZMF56H5", "Axcel World"),
    "D08SLP9LXUZ": ("T5ZMF56H5", "Axcel World"),
    "D0BSLP9LXUZ": ("T5ZMF56H5", "Axcel World"),
    "C0C28B8V2PK": ("T02HKMBE09K", "JTS Team"),
}

AUTHORITATIVE_CHANNEL_NAMES: Dict[str, str] = {
    "C08MV3EM9PY": "#jts_powertool",
    "C0BMV3EM9PY": "#jts_powertool",
    "C08V6S5UJ0P": "#agents_working_projects",
    "C0BV6S5UJ0P": "#agents_working_projects",
    "D08SLP9LXUZ": "@Admin User (DM)",
    "D0BSLP9LXUZ": "@Admin User (DM)",
    "C0C28B8V2PK": "#jts_powertool",
}

# In-memory fallback mock storage for local development / testing when AWS is not available
_MOCK_AWS_SECRETS: Dict[str, str] = {}


_SECRETS_CLIENT = None


def _get_secretsmanager_client():
    """Initializes boto3 Secrets Manager client using EC2 IAM Role credentials."""
    global _SECRETS_CLIENT
    if _SECRETS_CLIENT is not None:
        return _SECRETS_CLIENT
    try:
        import boto3
        region_name = os.getenv("AWS_REGION", "us-east-2").strip() or "us-east-2"
        session = boto3.session.Session()
        _SECRETS_CLIENT = session.client(service_name="secretsmanager", region_name=region_name)
        return _SECRETS_CLIENT
    except Exception as e:
        logger.warning(f"[CHANNEL_SECRETS] Could not initialize boto3 Secrets Manager client: {e}")
        return None


def _slugify(text: str) -> str:
    """Converts a client or channel name into a clean, AWS-compliant slug."""
    import re
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lstrip("#@")).strip("-").lower()
    return cleaned or "default"


def _format_aws_secret_name(
    channel_id: str,
    provider: str,
    folder_name: Optional[str] = None,
    channel_name: Optional[str] = None,
) -> str:
    """
    Standardized secret naming schema for AWS Secrets Manager.
    - If channel belongs to a client/folder:
        jts-powertool/clients/<client-slug>/<project-slug>/<provider>
    - If channel is unassigned:
        jts-powertool/channels/<channel-id>/<provider>
    """
    clean_provider = provider.strip().lower()
    prefix = os.getenv("AWS_SECRET_PREFIX", "jts-powertool").strip().strip("/")

    if folder_name:
        client_slug = _slugify(folder_name)
        project_slug = _slugify(channel_name) if channel_name else _slugify(channel_id)
        return f"{prefix}/clients/{client_slug}/{project_slug}/{clean_provider}"
    else:
        clean_channel = channel_id.strip().replace(" ", "-").replace("/", "-")
        return f"{prefix}/channels/{clean_channel}/{clean_provider}"


def store_channel_secret(
    *,
    channel_id: str,
    provider: str,
    api_key: str,
    channel_name: Optional[str] = None,
    updated_by: str = "admin",
) -> Dict[str, Any]:
    """
    Stores an API key exclusively in AWS Secrets Manager and records the mapping in PostgreSQL.
    Follows standard path: jts-powertool/clients/<client-slug>/<project-slug>/<provider>.
    Returns safe metadata only. The API key is NEVER logged or saved to the database.
    """
    if not channel_id or not channel_id.strip():
        raise ValueError("channel_id is required")
    if not provider or not provider.strip():
        raise ValueError("provider is required")
    if not api_key or not api_key.strip():
        raise ValueError("api_key cannot be empty")

    channel_id = channel_id.strip()
    provider = provider.strip().lower()
    channel_name = (channel_name or "").strip()

    # Look up folder/client and channel friendly name from database if not passed in
    folder_name = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT cm.channel_name, cf.name as folder_name
                FROM channel_metadata cm
                LEFT JOIN channel_folders cf ON cm.folder_id = cf.id
                WHERE cm.channel_id = %s;
            """, (channel_id,))
            meta_row = cur.fetchone()
            if meta_row:
                if not channel_name and meta_row.get("channel_name"):
                    channel_name = meta_row["channel_name"]
                if meta_row.get("folder_name"):
                    folder_name = meta_row["folder_name"]
    except Exception as ex:
        logger.debug(f"[CHANNEL_SECRETS] Could not lookup folder/channel meta: {ex}")
    finally:
        conn.close()

    secret_name = _format_aws_secret_name(
        channel_id=channel_id,
        provider=provider,
        folder_name=folder_name,
        channel_name=channel_name,
    )

    secret_payload = json.dumps({
        "api_key": api_key.strip(),
        "provider": provider,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "client_name": folder_name,
        "updated_at": time.time(),
        "updated_by": updated_by,
    })

    client = _get_secretsmanager_client()
    aws_secret_arn = None

    if client:
        try:
            # Try creating the secret directly
            try:
                resp = client.create_secret(
                    Name=secret_name,
                    Description=f"JTS-PowerTool API Key for channel '{channel_id}' ({channel_name or 'project'}) and provider '{provider}'",
                    SecretString=secret_payload,
                    Tags=[
                        {"Key": "Application", "Value": "JTS-PowerTool"},
                        {"Key": "ChannelId", "Value": channel_id},
                        {"Key": "Provider", "Value": provider},
                    ],
                )
                aws_secret_arn = resp.get("ARN")
                logger.info(f"[CHANNEL_SECRETS] Created secret in AWS Secrets Manager: {secret_name}")
            except Exception as ce:
                err_str = str(ce)
                if "ResourceExists" in err_str or "already exists" in err_str:
                    # Secret already exists -> update secret value directly
                    resp = client.put_secret_value(
                        SecretId=secret_name,
                        SecretString=secret_payload,
                    )
                    aws_secret_arn = resp.get("ARN")
                    logger.info(f"[CHANNEL_SECRETS] Updated existing secret in AWS Secrets Manager: {secret_name}")
                else:
                    raise ce
        except Exception as e:
            err_msg = str(e)
            if "AccessDeniedException" in err_msg:
                logger.error(f"[CHANNEL_SECRETS] AWS IAM AccessDenied: {e}")
                raise RuntimeError(
                    f"AWS IAM AccessDenied: The EC2 IAM role 'JTSP_IAM_Role' is not authorized to create/access secret '{secret_name}'. "
                    f"Please attach an IAM policy to 'JTSP_IAM_Role' allowing 'secretsmanager:*' on 'arn:aws:secretsmanager:*:*:secret:jts-powertool/*'."
                )
            logger.error(f"[CHANNEL_SECRETS] Failed to save secret to AWS Secrets Manager: {e}")
            raise RuntimeError(f"AWS Secrets Manager error: {err_msg}")
    else:
        # Local development / test fallback
        _MOCK_AWS_SECRETS[secret_name] = secret_payload
        aws_secret_arn = f"arn:aws:secretsmanager:local:000000000000:secret:{secret_name}"
        logger.info(f"[CHANNEL_SECRETS] Saved to in-memory mock AWS Secrets: {secret_name}")

    # Invalidate in-memory cached value
    _SECRET_VALUE_CACHE.pop((channel_id, provider), None)

    # Upsert mapping in PostgreSQL (NO api_key is stored)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channel_secret_mappings (
                    channel_id, channel_name, provider, aws_secret_name, aws_secret_arn, status, updated_by, updated_at
                ) VALUES (%s, %s, %s, %s, %s, 'active', %s, CURRENT_TIMESTAMP)
                ON CONFLICT (channel_id, provider) DO UPDATE SET
                    channel_name = COALESCE(EXCLUDED.channel_name, channel_secret_mappings.channel_name),
                    aws_secret_name = EXCLUDED.aws_secret_name,
                    aws_secret_arn = COALESCE(EXCLUDED.aws_secret_arn, channel_secret_mappings.aws_secret_arn),
                    status = 'active',
                    updated_by = EXCLUDED.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id, channel_id, channel_name, provider, aws_secret_name, aws_secret_arn, status, created_at, updated_at, updated_by;
            """, (channel_id, channel_name, provider, secret_name, aws_secret_arn, updated_by))
            row = cur.fetchone()

            if channel_name:
                channel_type = "dm" if channel_id.startswith("D") else "channel"
                _save_channel_metadata(cur, channel_id, channel_name, channel_type)
                _CHANNEL_NAME_CACHE[channel_id] = channel_name

            conn.commit()

            result = dict(row) if row else {}
            # Format datetime
            if "created_at" in result and result["created_at"]:
                result["created_at"] = result["created_at"].isoformat()
            if "updated_at" in result and result["updated_at"]:
                result["updated_at"] = result["updated_at"].isoformat()
            return result
    finally:
        conn.close()


def get_channel_secret_value(
    channel_id: str,
    provider: str,
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    channel_name: Optional[str] = None,
) -> Optional[str]:
    """
    Retrieves the actual API key for a channel from AWS Secrets Manager.
    Used by Claude and Worker execution pipelines.
    Returns None if no secret is mapped for this channel and provider.
    """
    if not channel_id or not provider:
        return None

    channel_id = canonical_channel_id(
        str(channel_id).strip(),
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        channel_name=channel_name,
    )
    provider = provider.strip().lower()

    # 1. Check in-memory cache
    cached = _SECRET_VALUE_CACHE.get((channel_id, provider))
    if cached:
        val, exp = cached
        if time.time() < exp:
            return val

    # 2. Look up AWS secret name from PostgreSQL mapping
    aws_secret_name = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT aws_secret_name, status
                FROM channel_secret_mappings
                WHERE UPPER(channel_id) = ANY(%s) AND provider = %s
                ORDER BY (status = 'active') DESC
                LIMIT 1;
            """, (channel_id_variants(channel_id), provider))
            row = cur.fetchone()
            if row and row.get("status") == "active":
                aws_secret_name = row.get("aws_secret_name")
    finally:
        conn.close()

    if not aws_secret_name:
        return None

    # 3. Retrieve secret from AWS Secrets Manager using IAM Role
    raw_secret = None
    client = _get_secretsmanager_client()
    if client:
        try:
            resp = client.get_secret_value(SecretId=aws_secret_name)
            if "SecretString" in resp:
                raw_secret = resp["SecretString"]
        except Exception as e:
            logger.warning(f"[CHANNEL_SECRETS] Could not fetch secret '{aws_secret_name}' from AWS: {e}")
            return None
    else:
        raw_secret = _MOCK_AWS_SECRETS.get(aws_secret_name)

    if not raw_secret:
        return None

    # 4. Extract API key from payload
    try:
        parsed = json.loads(raw_secret)
        key_val = parsed.get("api_key") or parsed.get("value") or raw_secret
    except Exception:
        key_val = raw_secret

    if key_val:
        # Cache for TTL
        _SECRET_VALUE_CACHE[(channel_id, provider)] = (key_val, time.time() + CACHE_TTL_SECONDS)

    return key_val


def delete_channel_secret(channel_id: str, provider: str, updated_by: str = "admin") -> bool:
    """
    Deletes the secret from AWS Secrets Manager and removes the PostgreSQL mapping.
    """
    if not channel_id or not provider:
        return False

    channel_id = channel_id.strip()
    provider = provider.strip().lower()

    # Lookup mapping
    aws_secret_name = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT aws_secret_name FROM channel_secret_mappings
                WHERE channel_id = %s AND provider = %s;
            """, (channel_id, provider))
            row = cur.fetchone()
            if row:
                aws_secret_name = row.get("aws_secret_name")

            # Delete from DB
            cur.execute("""
                DELETE FROM channel_secret_mappings
                WHERE channel_id = %s AND provider = %s;
            """, (channel_id, provider))
            conn.commit()
    finally:
        conn.close()

    # Invalidate cache
    _SECRET_VALUE_CACHE.pop((channel_id, provider), None)

    # Delete from AWS Secrets Manager
    if aws_secret_name:
        client = _get_secretsmanager_client()
        if client:
            try:
                client.delete_secret(
                    SecretId=aws_secret_name,
                    ForceDeleteWithoutRecovery=True,
                )
                logger.info(f"[CHANNEL_SECRETS] Deleted secret from AWS: {aws_secret_name}")
            except Exception as e:
                logger.warning(f"[CHANNEL_SECRETS] Could not delete secret '{aws_secret_name}' from AWS: {e}")
        else:
            _MOCK_AWS_SECRETS.pop(aws_secret_name, None)

    return True


def list_channel_secrets(channel_id: str) -> List[Dict[str, Any]]:
    """
    Lists configured secrets for a channel.
    SECURITY: Never returns secret values. Only safe metadata.
    """
    if not channel_id:
        return []

    channel_id = channel_id.strip()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, channel_id, channel_name, provider, aws_secret_name, aws_secret_arn,
                       status, created_at, updated_at, updated_by
                FROM channel_secret_mappings
                WHERE channel_id = %s
                ORDER BY provider ASC;
            """, (channel_id,))
            rows = cur.fetchall()

            result = []
            for r in rows:
                item = dict(r)
                if "created_at" in item and item["created_at"]:
                    item["created_at"] = item["created_at"].isoformat()
                if "updated_at" in item and item["updated_at"]:
                    item["updated_at"] = item["updated_at"].isoformat()
                result.append(item)
            return result
    finally:
        conn.close()


def _team_name(cur, team_id: Optional[str]) -> Optional[str]:
    """Friendly workspace name for a Slack team id (from the registered workspaces), or None."""
    if not team_id:
        return None
    for known_id, known_name in AUTHORITATIVE_CHANNEL_WORKSPACES.values():
        if known_id == team_id:
            return known_name
    try:
        cur.execute("SELECT team_name FROM slack_workspaces WHERE team_id = %s;", (team_id,))
        row = cur.fetchone()
        if row:
            name = (row.get("team_name") if isinstance(row, dict) else row[0]) or None
            return None if (name and name.strip().upper() == team_id.strip().upper()) else name  # an id is not a name
    except Exception:
        pass
    return None


def _save_channel_metadata(
    cur,
    channel_id: str,
    channel_name: str,
    channel_type: str = "channel",
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
):
    """Internal helper to upsert channel metadata with workspace info."""
    clean_cid = (channel_id or "").strip().upper()
    if clean_cid in AUTHORITATIVE_CHANNEL_WORKSPACES:
        workspace_id, workspace_name = AUTHORITATIVE_CHANNEL_WORKSPACES[clean_cid]
    if clean_cid in AUTHORITATIVE_CHANNEL_NAMES:
        channel_name = AUTHORITATIVE_CHANNEL_NAMES[clean_cid]

    try:
        cur.execute("""
            INSERT INTO channel_metadata (channel_id, channel_name, channel_type, workspace_id, workspace_name, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (channel_id) DO UPDATE SET
                channel_name = EXCLUDED.channel_name,
                channel_type = EXCLUDED.channel_type,
                workspace_id = COALESCE(EXCLUDED.workspace_id, channel_metadata.workspace_id),
                workspace_name = COALESCE(EXCLUDED.workspace_name, channel_metadata.workspace_name),
                updated_at = CURRENT_TIMESTAMP;
        """, (channel_id, channel_name, channel_type, workspace_id, workspace_name))
    except Exception as e:
        logger.debug(f"[CHANNEL_SECRETS] Could not upsert channel_metadata: {e}")



def resolve_slack_channel_name(channel_id: str, token: Optional[str] = None, team_id: Optional[str] = None) -> str:
    """
    Resolves friendly channel name (e.g. #general, @alex) for a Slack channel_id or DM.
    Checks PostgreSQL channel_metadata table first, then queries Slack API if necessary.
    """
    if not channel_id:
        return "unknown"

    channel_id = channel_id.strip()

    # 1. In-memory cache
    if channel_id in _CHANNEL_NAME_CACHE and not _CHANNEL_NAME_CACHE[channel_id].startswith("#Channel C") and _CHANNEL_NAME_CACHE[channel_id] not in ("#jts-test", "jts-test"):
        return _CHANNEL_NAME_CACHE[channel_id]

    channel_type = "dm" if channel_id.startswith("D") else "channel"

    # 2. Check channel_metadata table for fast DB resolution
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT channel_name, workspace_id FROM channel_metadata WHERE channel_id = %s;", (channel_id,))
                row = cur.fetchone()
                if row and row.get("channel_name") and not row["channel_name"].startswith("#Channel C") and row["channel_name"] not in ("#jts-test", "jts-test"):
                    name = row["channel_name"]
                    # A channel saved without a workspace is shown under the default one. Slack just told us the real
                    # workspace, so record it.
                    if team_id and row.get("workspace_id") != team_id:
                        _save_channel_metadata(cur, channel_id, name, channel_type, team_id, _team_name(cur, team_id))
                        conn.commit()
                    _CHANNEL_NAME_CACHE[channel_id] = name
                    return name
            except Exception:
                pass
    finally:
        conn.close()

    # 3. Query Slack API directly using workspace-specific token
    if not token and team_id:
        from app.tools.secrets_manager import get_slack_bot_token
        token = get_slack_bot_token(team_id)
    if not token:
        from app.tools.secrets_manager import get_slack_bot_token
        token = get_slack_bot_token()

    resolved_name = None

    if token:
        try:
            with httpx.Client(timeout=4.0) as client:
                # 4a. Direct message Slack user resolution
                if channel_id.startswith("D"):
                    try:
                        hist = client.get(
                            "https://slack.com/api/conversations.history",
                            params={"channel": channel_id, "limit": 10},
                            headers={"Authorization": f"Bearer {token}"}
                        )
                        hdata = hist.json()
                        if hdata.get("ok"):
                            for m in hdata.get("messages", []):
                                uid = m.get("user")
                                if uid and not m.get("bot_id"):
                                    uresp = client.get(
                                        "https://slack.com/api/users.info",
                                        params={"user": uid},
                                        headers={"Authorization": f"Bearer {token}"}
                                    )
                                    udata = uresp.json()
                                    if udata.get("ok"):
                                        u = udata.get("user", {})
                                        rname = u.get("real_name") or u.get("name")
                                        if rname:
                                            resolved_name = f"@{rname} (DM)"
                                            break
                    except Exception as ex:
                        logger.debug(f"[CHANNEL_SECRETS] DM resolution error: {ex}")

                # 4b. Channel Slack resolution
                if not resolved_name and channel_id.startswith("C"):
                    try:
                        cresp = client.get(
                            "https://slack.com/api/conversations.info",
                            params={"channel": channel_id},
                            headers={"Authorization": f"Bearer {token}"}
                        )
                        cdata = cresp.json()
                        if cdata.get("ok"):
                            cname = cdata.get("channel", {}).get("name")
                            if cname:
                                resolved_name = f"#{cname}"
                    except Exception as ex:
                        logger.debug(f"[CHANNEL_SECRETS] conversations.info error: {ex}")

                    # If private channel without groups:read, check history rename events
                    if not resolved_name:
                        try:
                            cursor = None
                            for _ in range(3):
                                params = {"channel": channel_id, "limit": 100}
                                if cursor:
                                    params["cursor"] = cursor
                                hresp = client.get(
                                    "https://slack.com/api/conversations.history",
                                    params=params,
                                    headers={"Authorization": f"Bearer {token}"}
                                )
                                hdata = hresp.json()
                                if not hdata.get("ok"):
                                    break
                                for m in hdata.get("messages", []):
                                    if m.get("subtype") == "channel_name" and m.get("name"):
                                        resolved_name = f"#{m.get('name')}"
                                        break
                                if resolved_name:
                                    break
                                cursor = hdata.get("response_metadata", {}).get("next_cursor")
                                if not cursor:
                                    break
                        except Exception as ex:
                            logger.debug(f"[CHANNEL_SECRETS] rename history error: {ex}")
        except Exception as e:
            logger.debug(f"[CHANNEL_SECRETS] Slack client error: {e}")

    # 5. Final fallback
    if not resolved_name:
        if channel_id.startswith("D"):
            resolved_name = f"@DM ({channel_id})"
        else:
            resolved_name = f"#Channel {channel_id}"

    # 6. Save to DB and cache
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _save_channel_metadata(cur, channel_id, resolved_name, channel_type, team_id, _team_name(cur, team_id))
            conn.commit()
    except Exception as e:
        logger.debug(f"[CHANNEL_SECRETS] Failed to save metadata: {e}")
    finally:
        conn.close()

    _CHANNEL_NAME_CACHE[channel_id] = resolved_name
    return resolved_name


def set_channel_name(channel_id: str, channel_name: str) -> str:
    """
    Manually sets or renames a channel/project friendly name.
    """
    if not channel_id or not channel_id.strip():
        raise ValueError("channel_id is required")
    if not channel_name or not channel_name.strip():
        raise ValueError("channel_name cannot be empty")

    channel_id = channel_id.strip()
    channel_name = channel_name.strip()
    channel_type = "dm" if channel_id.startswith("D") else "channel"

    # Ensure prefix (# for channel, @ for DM)
    if channel_type == "channel" and not channel_name.startswith("#") and not channel_name.startswith("@"):
        channel_name = f"#{channel_name}"
    elif channel_type == "dm" and not channel_name.startswith("@") and not channel_name.startswith("#"):
        channel_name = f"@{channel_name}"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _save_channel_metadata(cur, channel_id, channel_name, channel_type)
            try:
                cur.execute("""
                    UPDATE channel_secret_mappings
                    SET channel_name = %s
                    WHERE channel_id = %s;
                """, (channel_name, channel_id))
            except Exception:
                pass
            conn.commit()
    finally:
        conn.close()

    _CHANNEL_NAME_CACHE[channel_id] = channel_name
    return channel_name


def list_all_channels() -> List[Dict[str, Any]]:
    """
    Returns an aggregated list of all known channels/projects across:
    1. channel_metadata
    2. channel_secret_mappings
    3. conversation_messages
    Includes count of configured API keys per channel, resolved human names, and assigned folder.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Distinct channels with configured secrets
            cur.execute("""
                SELECT channel_id, MAX(channel_name) as channel_name,
                       COUNT(id) as secret_count,
                       array_agg(provider) as providers
                FROM channel_secret_mappings
                GROUP BY channel_id;
            """)
            mapped_rows = {row["channel_id"]: dict(row) for row in cur.fetchall()}

            # 2. Saved channel metadata including folders and workspaces
            saved_metadata = {}
            try:
                cur.execute("""
                    SELECT cm.channel_id, cm.channel_name, cm.channel_type, cm.folder_id, cf.name as folder_name,
                           COALESCE(sw.team_id, cm.workspace_id, 'T5ZMF56H5') as workspace_id,
                           COALESCE(sw.team_name, cm.workspace_name, 'Axcel World') as workspace_name
                    FROM channel_metadata cm
                    LEFT JOIN channel_folders cf ON cm.folder_id = cf.id
                    LEFT JOIN slack_workspaces sw ON cm.workspace_id = sw.team_id;
                """)
                for r in cur.fetchall():
                    saved_metadata[r["channel_id"]] = dict(r)
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    cur.execute("SELECT channel_id, channel_name, channel_type, workspace_id, workspace_name FROM channel_metadata;")
                    for r in cur.fetchall():
                        saved_metadata[r["channel_id"]] = dict(r)
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

            # 3. Distinct channels and workspace info from conversation_messages, claude_context_snapshots, and api_usage_logs
            msg_channels = set()
            ws_map = {}
            for tbl in ("conversation_messages", "claude_context_snapshots", "api_usage_logs"):
                try:
                    cur.execute(f"SELECT DISTINCT channel_id, workspace_id, workspace_name FROM {tbl} WHERE channel_id IS NOT NULL AND channel_id != '' AND channel_id != 'unknown';")
                    for row in cur.fetchall():
                        cid = row["channel_id"] if isinstance(row, dict) else row[0]
                        wid = (row.get("workspace_id") if isinstance(row, dict) else (row[1] if len(row) > 1 else None)) or None
                        wname = (row.get("workspace_name") if isinstance(row, dict) else (row[2] if len(row) > 2 else None)) or None
                        if cid:
                            msg_channels.add(cid)
                            if wid and cid not in ws_map:
                                ws_map[cid] = (wid, wname)
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

            # 4. Combine and sort
            all_channel_ids = {canonical_channel_id(cid) for cid in (set(mapped_rows.keys()).union(msg_channels).union(saved_metadata.keys()))} - OBSOLETE_CHANNEL_IDS

            raw_channels = []
            for cid in sorted(all_channel_ids):
                mapping = mapped_rows.get(cid, {})
                meta = saved_metadata.get(cid, {})
                friendly_name = meta.get("channel_name") or mapping.get("channel_name")

                # If missing or raw default name, auto-resolve
                if not friendly_name or friendly_name in [cid, f"Channel {cid}", f"channel_{cid}"]:
                    friendly_name = resolve_slack_channel_name(cid)

                channel_type = meta.get("channel_type") or ("dm" if cid.startswith("D") else "channel")
                cid_upper = cid.upper()
                if cid_upper in AUTHORITATIVE_CHANNEL_WORKSPACES:
                    m_wid, m_wname = AUTHORITATIVE_CHANNEL_WORKSPACES[cid_upper]
                else:
                    m_wid = meta.get("workspace_id") or (ws_map[cid][0] if cid in ws_map and ws_map[cid][0] else "T5ZMF56H5")
                    m_wname = meta.get("workspace_name") or (ws_map[cid][1] if cid in ws_map and ws_map[cid][1] else "Axcel World")

                if cid_upper in AUTHORITATIVE_CHANNEL_NAMES:
                    friendly_name = AUTHORITATIVE_CHANNEL_NAMES[cid_upper]

                raw_channels.append({
                    "workspace_id": m_wid,
                    "workspace_name": m_wname,
                    "channel_id": cid,
                    "channel_name": friendly_name,
                    "channel_type": channel_type,
                    "secret_count": mapping.get("secret_count", 0),
                    "providers": mapping.get("providers") or [],
                    "folder_id": meta.get("folder_id"),
                    "folder_name": meta.get("folder_name"),
                })

            # Deduplicate strictly by canonical channel ID so each ID appears exactly once
            seen_cids = set()
            channels = []
            for ch in raw_channels:
                ch_id = ch["channel_id"].strip().upper()
                if ch_id in seen_cids:
                    for idx, existing in enumerate(channels):
                        if existing["channel_id"].strip().upper() == ch_id:
                            # Prioritize the one assigned to a folder or with active secrets
                            if (not existing.get("folder_id") and ch.get("folder_id")) or (existing.get("secret_count", 0) < ch.get("secret_count", 0)):
                                channels[idx] = ch
                            break
                else:
                    seen_cids.add(ch_id)
                    channels.append(ch)

            return channels
    finally:
        conn.close()


def create_channel_folder(name: str, description: Optional[str] = None) -> Dict[str, Any]:
    """
    Creates a new folder permanently in the database to group channels/projects.
    """
    if not name or not name.strip():
        raise ValueError("Folder name cannot be empty")
    name = name.strip()
    description = description.strip() if description else None

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Check if name already exists
            cur.execute("SELECT id FROM channel_folders WHERE LOWER(name) = LOWER(%s);", (name,))
            if cur.fetchone():
                raise ValueError(f"A folder named '{name}' already exists.")

            cur.execute("""
                INSERT INTO channel_folders (name, description, created_at, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id, name, description, created_at, updated_at;
            """, (name, description))
            row = cur.fetchone()
            conn.commit()

            res = dict(row)
            if res.get("created_at"):
                res["created_at"] = res["created_at"].isoformat()
            if res.get("updated_at"):
                res["updated_at"] = res["updated_at"].isoformat()
            res["channel_count"] = 0
            return res
    finally:
        conn.close()


def list_channel_folders() -> List[Dict[str, Any]]:
    """
    Lists all folders permanently saved in the database with their channel counts.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    SELECT f.id, f.name, f.description, f.created_at, f.updated_at,
                           COUNT(cm.channel_id) as channel_count
                    FROM channel_folders f
                    LEFT JOIN channel_metadata cm ON f.id = cm.folder_id AND cm.channel_id NOT IN ('C0BMV3EM9PY', 'C0BV6S5UJ0P', 'D0BSLP9LXUZ')
                    GROUP BY f.id, f.name, f.description, f.created_at, f.updated_at
                    ORDER BY f.name ASC;
                """)
                rows = cur.fetchall()
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                logger.warning(f"[CHANNEL_SECRETS] Could not list channel_folders: {e}")
                return []

            result = []
            for r in rows:
                item = dict(r)
                if item.get("created_at"):
                    item["created_at"] = item["created_at"].isoformat()
                if item.get("updated_at"):
                    item["updated_at"] = item["updated_at"].isoformat()
                item["channel_count"] = int(item.get("channel_count") or 0)
                result.append(item)
            return result
    finally:
        conn.close()


def update_channel_folder(folder_id: int, name: str, description: Optional[str] = None) -> Dict[str, Any]:
    """
    Renames or updates an existing channel folder.
    """
    if not folder_id:
        raise ValueError("folder_id is required")
    if not name or not name.strip():
        raise ValueError("Folder name cannot be empty")
    name = name.strip()
    description = description.strip() if description else None

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Check duplicate name on another folder
            cur.execute("SELECT id FROM channel_folders WHERE LOWER(name) = LOWER(%s) AND id != %s;", (name, folder_id))
            if cur.fetchone():
                raise ValueError(f"A folder named '{name}' already exists.")

            cur.execute("""
                UPDATE channel_folders
                SET name = %s, description = COALESCE(%s, description), updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING id, name, description, created_at, updated_at;
            """, (name, description, folder_id))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Folder with id {folder_id} not found.")
            conn.commit()

            res = dict(row)
            if res.get("created_at"):
                res["created_at"] = res["created_at"].isoformat()
            if res.get("updated_at"):
                res["updated_at"] = res["updated_at"].isoformat()
            return res
    finally:
        conn.close()


def delete_channel_folder(folder_id: int) -> bool:
    """
    Deletes a folder from PostgreSQL.
    Channels belonging to this folder are unassigned (folder_id set to NULL).
    """
    if not folder_id:
        return False

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("UPDATE channel_metadata SET folder_id = NULL WHERE folder_id = %s;", (folder_id,))
            except Exception:
                pass

            cur.execute("DELETE FROM channel_folders WHERE id = %s RETURNING id;", (folder_id,))
            row = cur.fetchone()
            conn.commit()
            return bool(row)
    finally:
        conn.close()


def set_channel_folder(channel_id: str, folder_id: Optional[int]) -> Dict[str, Any]:
    """
    Assigns or unassigns a channel to a folder.
    """
    if not channel_id or not channel_id.strip():
        raise ValueError("channel_id is required")
    channel_id = channel_id.strip()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            folder_name = None
            if folder_id is not None:
                cur.execute("SELECT name FROM channel_folders WHERE id = %s;", (folder_id,))
                frow = cur.fetchone()
                if not frow:
                    raise ValueError(f"Folder with id {folder_id} does not exist.")
                folder_name = frow["name"]

            # Ensure channel exists in channel_metadata
            cur.execute("SELECT channel_id FROM channel_metadata WHERE channel_id = %s;", (channel_id,))
            if not cur.fetchone():
                # Auto-resolve friendly name and insert
                channel_name = resolve_slack_channel_name(channel_id)
                channel_type = "dm" if channel_id.startswith("D") else "channel"
                cur.execute("""
                    INSERT INTO channel_metadata (channel_id, channel_name, channel_type, folder_id, updated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (channel_id) DO UPDATE SET
                        folder_id = EXCLUDED.folder_id,
                        updated_at = CURRENT_TIMESTAMP;
                """, (channel_id, channel_name, channel_type, folder_id))
            else:
                cur.execute("""
                    UPDATE channel_metadata
                    SET folder_id = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE channel_id = %s;
                """, (folder_id, channel_id))

            conn.commit()
            return {
                "channel_id": channel_id,
                "folder_id": folder_id,
                "folder_name": folder_name,
            }
    finally:
        conn.close()


def get_channel_folder(folder_id: int) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single folder by ID along with all channels assigned to it.
    """
    if not folder_id:
        return None

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, description, created_at, updated_at
                FROM channel_folders
                WHERE id = %s;
            """, (folder_id,))
            frow = cur.fetchone()
            if not frow:
                return None

            folder = dict(frow)
            if folder.get("created_at"):
                folder["created_at"] = folder["created_at"].isoformat()
            if folder.get("updated_at"):
                folder["updated_at"] = folder["updated_at"].isoformat()

            # Query channels assigned to this folder
            cur.execute("""
                SELECT cm.channel_id, cm.channel_name, cm.channel_type, cm.folder_id,
                       COALESCE(sw.team_id, cm.workspace_id, 'T5ZMF56H5') as workspace_id,
                       COALESCE(sw.team_name, cm.workspace_name, 'Axcel World') as workspace_name,
                       COUNT(cs.id) as secret_count,
                       COALESCE(array_agg(cs.provider) FILTER (WHERE cs.provider IS NOT NULL), '{}') as providers
                FROM channel_metadata cm
                LEFT JOIN slack_workspaces sw ON cm.workspace_id = sw.team_id
                LEFT JOIN channel_secret_mappings cs ON cm.channel_id = cs.channel_id AND cs.status = 'active'
                WHERE cm.folder_id = %s
                  AND cm.channel_id NOT IN ('C0BMV3EM9PY', 'C0BV6S5UJ0P', 'D0BSLP9LXUZ')
                GROUP BY cm.channel_id, cm.channel_name, cm.channel_type, cm.folder_id, sw.team_id, cm.workspace_id, sw.team_name, cm.workspace_name
                ORDER BY cm.channel_name ASC;
            """, (folder_id,))
            ch_rows = cur.fetchall()

            channels = []
            seen_ids = set()
            for r in ch_rows:
                ch = dict(r)
                raw_cid = ch.get("channel_id")
                cid = canonical_channel_id(raw_cid)
                if not cid or cid in OBSOLETE_CHANNEL_IDS:
                    continue
                cid_upper = cid.upper()
                if cid_upper in seen_ids:
                    continue
                seen_ids.add(cid_upper)

                ch["channel_id"] = cid
                if cid_upper in AUTHORITATIVE_CHANNEL_WORKSPACES:
                    ch["workspace_id"], ch["workspace_name"] = AUTHORITATIVE_CHANNEL_WORKSPACES[cid_upper]
                if cid_upper in AUTHORITATIVE_CHANNEL_NAMES:
                    ch["channel_name"] = AUTHORITATIVE_CHANNEL_NAMES[cid_upper]

                ch["secret_count"] = int(ch.get("secret_count") or 0)
                ch["providers"] = list(set(ch.get("providers") or []))
                ch["folder_name"] = folder["name"]
                channels.append(ch)

            folder["channels"] = channels
            folder["channel_count"] = len(channels)
            return folder
    finally:
        conn.close()


def create_slack_channel(
    name: str,
    folder_id: int,
    is_private: bool = False,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Creates a Slack channel in the configured Slack workspace using conversations.create API,
    and assigns it to the specified folder in PostgreSQL.
    """
    from app.tools.secrets_manager import get_secret
    import re

    if not folder_id:
        raise ValueError("folder_id is required")

    # 1. Clean & validate name
    clean_name = name.strip().lstrip("#").lower()
    clean_name = re.sub(r"[^a-z0-9_-]+", "-", clean_name).strip("-")
    if not clean_name:
        raise ValueError("Channel name is invalid. Must contain lowercase alphanumeric characters.")
    if len(clean_name) > 80:
        clean_name = clean_name[:80].rstrip("-")

    # 2. Check that the folder exists in DB
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM channel_folders WHERE id = %s;", (folder_id,))
            frow = cur.fetchone()
            if not frow:
                raise ValueError(f"Folder with ID {folder_id} does not exist.")
            folder_name = frow["name"]
    finally:
        conn.close()

    # 3. Retrieve Slack bot token
    token = get_secret("SLACK_BOT_TOKEN", "").strip() or os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is not configured. Please set your Slack Bot Token in AWS Secrets Manager or environment variables.")

    # 4. Call Slack API conversations.create
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "name": clean_name,
        "is_private": is_private,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://slack.com/api/conversations.create", json=payload, headers=headers)
            sdata = resp.json()
    except Exception as ex:
        logger.error(f"[CHANNEL_SECRETS] Slack API network error: {ex}")
        raise ValueError(f"Failed to connect to Slack API: {ex}")

    if not sdata.get("ok"):
        error_code = sdata.get("error", "unknown_error")
        logger.warning(f"[CHANNEL_SECRETS] Slack conversations.create failed: {error_code}")
        if error_code == "name_taken":
            raise ValueError(f"A Slack channel named '#{clean_name}' already exists in your workspace.")
        elif error_code == "restricted_action":
            raise ValueError("Slack channel creation is restricted by workspace admin policy.")
        elif error_code == "missing_scope":
            needed_scope = "groups:write" if is_private else "channels:manage"
            raise ValueError(f"Slack bot token is missing the required '{needed_scope}' scope to create channels.")
        elif error_code == "invalid_name_specials":
            raise ValueError(f"Channel name '#{clean_name}' contains invalid characters for Slack.")
        elif error_code in ("not_authed", "invalid_auth", "token_revoked"):
            raise ValueError("Slack authentication failed. Please verify your SLACK_BOT_TOKEN.")
        else:
            raise ValueError(f"Slack API error: {error_code}")

    slack_chan = sdata.get("channel", {})
    channel_id = slack_chan.get("id")
    actual_name = slack_chan.get("name", clean_name)
    friendly_name = f"#{actual_name}"

    # 5. Optionally set topic in Slack
    if topic and topic.strip():
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(
                    "https://slack.com/api/conversations.setTopic",
                    json={"channel": channel_id, "topic": topic.strip()},
                    headers=headers,
                )
        except Exception as tex:
            logger.warning(f"[CHANNEL_SECRETS] Could not set topic for channel {channel_id}: {tex}")

    # 6. Save channel metadata to PostgreSQL with folder_id
    channel_type = "private_channel" if is_private else "channel"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channel_metadata (channel_id, channel_name, channel_type, folder_id, updated_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (channel_id) DO UPDATE SET
                    channel_name = EXCLUDED.channel_name,
                    channel_type = EXCLUDED.channel_type,
                    folder_id = EXCLUDED.folder_id,
                    updated_at = CURRENT_TIMESTAMP;
            """, (channel_id, friendly_name, channel_type, folder_id))
            conn.commit()
    finally:
        conn.close()

    _CHANNEL_NAME_CACHE[channel_id] = friendly_name

    return {
        "channel_id": channel_id,
        "channel_name": friendly_name,
        "channel_type": channel_type,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "is_private": is_private,
        "secret_count": 0,
        "providers": [],
    }


def sync_bot_conversations_from_slack() -> Dict[str, Any]:
    """
    Discovers the channels and DMs the bot has joined in EVERY connected Slack workspace (the original bot token plus each
    workspace installed through /api/slack/install) and records which workspace each belongs to.
    """
    from app.tools.secrets_manager import get_secret

    tokens: List[tuple] = []  # (token, team_id, team_name)
    seen_tokens = set()
    candidates: List[tuple] = []  # (token, stored team_id, stored team_name)
    default_token = get_secret("SLACK_BOT_TOKEN", "").strip() or os.getenv("SLACK_BOT_TOKEN", "").strip()
    if default_token:
        candidates.append((default_token, None, None))
    try:
        from app.db.repositories import list_slack_workspaces
        for ws in list_slack_workspaces():
            tok = (ws.get("bot_token") or "").strip()
            if len(tok) > 10:
                candidates.append((tok, ws.get("team_id"), ws.get("team_name")))
    except Exception as ex:
        logger.warning(f"[CHANNEL_SECRETS] Could not list the connected Slack workspaces: {ex}")

    for tok, stored_id, stored_name in candidates:
        if tok in seen_tokens:
            continue
        seen_tokens.add(tok)
        team_id, team_name = stored_id, stored_name
        try:
            # Slack knows the real team id and name for this token; use it (and repair a wrong stored name).
            with httpx.Client(timeout=10.0) as client:
                who = client.post("https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {tok}"}).json()
            if who.get("ok"):
                team_id, team_name = who.get("team_id") or stored_id, who.get("team") or stored_name
                if team_id and team_name and (stored_name or "").strip() != team_name:
                    try:
                        from app.db.repositories import save_slack_workspace
                        save_slack_workspace(team_id=team_id, team_name=team_name, bot_token=tok)
                        logger.info(f"[CHANNEL_SECRETS] Workspace name for {team_id} set to '{team_name}' (was '{stored_name}')")
                    except Exception as ex:
                        logger.debug(f"[CHANNEL_SECRETS] Could not repair the workspace name for {team_id}: {ex}")
        except Exception as ex:
            logger.debug(f"[CHANNEL_SECRETS] auth.test failed for a workspace token: {ex}")
        tokens.append((tok, team_id, team_name))

    if not tokens:
        raise ValueError("SLACK_BOT_TOKEN is not configured. Please set your Slack Bot Token in AWS Secrets Manager or environment variables.")

    all_channels: List[Dict[str, Any]] = []
    problems: List[str] = []
    done_teams = set()
    for i, (tok, tid, tname) in enumerate(tokens):
        if tid and tid in done_teams:
            continue
        try:
            part = _sync_workspace_conversations(tok, tid, tname)
            all_channels.extend(part["channels"])
            logger.info(f"[CHANNEL_SECRETS] Synced {len(part['channels'])} channel(s) from workspace '{tname}' ({tid})")
            if tid:
                done_teams.add(tid)
        except Exception as ex:
            if i == 0 and len(tokens) == 1:
                raise
            problems.append(f"{tname or tid or 'a workspace'}: {ex}")
            logger.warning(f"[CHANNEL_SECRETS] Sync failed for {tname or tid}: {ex}")
    logger.info(f"[CHANNEL_SECRETS] Slack sync finished: {len(all_channels)} channel(s), {len(tokens)} token(s) tried, {len(problems)} problem(s)")
    msg = f"Successfully synced {len(all_channels)} channels and conversations from {len(done_teams) or len(tokens)} workspace(s)."
    if problems:
        msg += " Problems: " + "; ".join(problems)
    return {"status": "success", "message": msg, "synced_count": len(all_channels), "channels": all_channels}


def _sync_workspace_conversations(token: str, team_id: Optional[str] = None, team_name: Optional[str] = None) -> Dict[str, Any]:
    """Syncs one workspace (see sync_bot_conversations_from_slack). Upserts into channel_metadata, keeping folder assignments."""

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    synced_channels = []
    user_name_cache: Dict[str, str] = {}
    cursor = None

    with httpx.Client(timeout=15.0) as client:
        while True:
            params: Dict[str, Any] = {
                "types": "public_channel,private_channel,im,mpim",
                "exclude_archived": "true",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            try:
                resp = client.get("https://slack.com/api/users.conversations", params=params, headers=headers)
                sdata = resp.json()
            except Exception as ex:
                logger.error(f"[CHANNEL_SECRETS] Error calling users.conversations: {ex}")
                raise ValueError(f"Slack API network error: {ex}")

            if not sdata.get("ok"):
                err = sdata.get("error", "unknown_error")
                logger.error(f"[CHANNEL_SECRETS] users.conversations returned error: {err}")
                raise ValueError(f"Slack API error: {err}")

            raw_channels = sdata.get("channels", [])
            for c in raw_channels:
                cid = c.get("id")
                if not cid or cid in OBSOLETE_CHANNEL_IDS:
                    continue

                is_im = bool(c.get("is_im"))
                if is_im and c.get("user") == "USLACKBOT":
                    continue  # Slack's built-in chat, never useful
                is_mpim = bool(c.get("is_mpim"))
                is_private = bool(c.get("is_private"))

                channel_type = "dm" if is_im else ("mpim" if is_mpim else ("private_channel" if is_private else "channel"))
                friendly_name = None

                if is_im:
                    peer_user_id = c.get("user")
                    if peer_user_id:
                        if peer_user_id in user_name_cache:
                            friendly_name = f"@{user_name_cache[peer_user_id]} (DM)"
                        else:
                            try:
                                uresp = client.get(
                                    "https://slack.com/api/users.info",
                                    params={"user": peer_user_id},
                                    headers=headers,
                                )
                                udata = uresp.json()
                                if udata.get("ok"):
                                    u = udata.get("user", {})
                                    rname = u.get("real_name") or u.get("name") or peer_user_id
                                    user_name_cache[peer_user_id] = rname
                                    friendly_name = f"@{rname} (DM)"
                            except Exception:
                                pass
                    if not friendly_name:
                        friendly_name = f"@DM ({cid})"
                else:
                    cname = c.get("name")
                    if cname:
                        friendly_name = f"#{cname}"
                    else:
                        friendly_name = f"#Channel {cid}"

                synced_channels.append({
                    "channel_id": cid,
                    "channel_name": friendly_name,
                    "channel_type": channel_type,
                    "is_private": is_private,
                })

            cursor = sdata.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    # Upsert all synced channels into PostgreSQL channel_metadata (preserving existing folder_id)
    if synced_channels:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for sc in synced_channels:
                    cid = sc["channel_id"]
                    cname = sc["channel_name"]
                    ctype = sc["channel_type"]
                    ws_id, ws_name = team_id, team_name
                    if cid.upper() in AUTHORITATIVE_CHANNEL_WORKSPACES:
                        ws_id, ws_name = AUTHORITATIVE_CHANNEL_WORKSPACES[cid.upper()]
                    cur.execute("""
                        INSERT INTO channel_metadata (channel_id, channel_name, channel_type, workspace_id, workspace_name, updated_at)
                        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (channel_id) DO UPDATE SET
                            channel_name = EXCLUDED.channel_name,
                            channel_type = EXCLUDED.channel_type,
                            workspace_id = COALESCE(EXCLUDED.workspace_id, channel_metadata.workspace_id),
                            workspace_name = COALESCE(EXCLUDED.workspace_name, channel_metadata.workspace_name),
                            updated_at = CURRENT_TIMESTAMP;
                    """, (cid, cname, ctype, ws_id, ws_name))
                    _CHANNEL_NAME_CACHE[cid] = cname
                conn.commit()
        except Exception as dberr:
            logger.error(f"[CHANNEL_SECRETS] Failed to upsert synced channels into database: {dberr}")
            raise RuntimeError(f"Database error during Slack sync: {dberr}")
        finally:
            conn.close()

    return {
        "status": "success",
        "message": f"Successfully synced {len(synced_channels)} channels and conversations from Slack.",
        "synced_count": len(synced_channels),
        "channels": synced_channels,
    }


def list_unassigned_channels() -> List[Dict[str, Any]]:
    """
    Returns all channels and members discovered from Slack / conversations / metadata
    that are NOT yet assigned to any folder (folder_id IS NULL).
    Includes configured secret counts and providers.
    """
    all_channels = list_all_channels()
    # Only real channels are offered for assignment: direct messages (including group DMs and Slack's built-in
    # Slackbot chat) are left out of this list. The bot still works in them.
    def _is_dm(ch: Dict[str, Any]) -> bool:
        cid = str(ch.get("channel_id") or "")
        name = str(ch.get("channel_name") or "")
        return (
            ch.get("channel_type") in ("dm", "mpim")
            or cid.startswith("D")
            or name.lower().startswith("@slackbot")
        )

    return [ch for ch in all_channels if not ch.get("folder_id") and not _is_dm(ch)]


# --- Generic Secrets Vault Service ---

# --- Generic Secrets Vault Service ---

def list_vault_secrets() -> List[Dict[str, Any]]:
    """
    Lists all stored secret keys across AWS Secrets Manager, secrets_vault, channel_secret_mappings, and environment configuration.
    SECURITY: NEVER returns actual secret values, only safe key names and metadata.
    """
    seen_key_names = set()
    result = []

    # 1. Fetch metadata from PostgreSQL secrets_vault table
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("ALTER TABLE secrets_vault ADD COLUMN IF NOT EXISTS name VARCHAR(255);")
                conn.commit()
            except Exception:
                conn.rollback()

            cur.execute("""
                SELECT id, key_name, aws_secret_name, name, created_at, updated_at
                FROM secrets_vault
                ORDER BY created_at DESC;
            """)
            rows = cur.fetchall()
            for r in rows:
                kname = r["key_name"]
                seen_key_names.add(kname.strip().lower())
                u_name = r.get("name") or "JTS Admin"
                result.append({
                    "id": r["id"],
                    "key_name": kname,
                    "aws_secret_name": r["aws_secret_name"],
                    "name": u_name,
                    "full_name": u_name,
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                })

            # 2. Fetch channel-specific secret mappings from PostgreSQL
            try:
                cur.execute("""
                    SELECT csm.id, csm.channel_id, csm.channel_name, csm.provider, csm.aws_secret_name, csm.created_at
                    FROM channel_secret_mappings csm
                    ORDER BY csm.created_at DESC;
                """)
                csm_rows = cur.fetchall()
                for r in csm_rows:
                    provider = (r.get("provider") or "API Key").title()
                    ch_name = r.get("channel_name") or r.get("channel_id") or ""
                    display_name = f"{provider} Key ({ch_name})" if ch_name else f"{provider} Key"
                    norm_name = display_name.strip().lower()
                    if norm_name not in seen_key_names:
                        seen_key_names.add(norm_name)
                        result.append({
                            "id": r["id"] + 5000,
                            "key_name": display_name,
                            "aws_secret_name": r.get("aws_secret_name") or f"jts-powertool/channels/{r.get('channel_id')}/{r.get('provider')}",
                            "name": "JTS Admin",
                            "full_name": "JTS Admin",
                            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                            "updated_at": None,
                        })
            except Exception as csmerr:
                logger.debug(f"[VAULT] Could not query channel_secret_mappings: {csmerr}")

    except Exception as dberr:
        logger.warning(f"[VAULT] Database query error in list_vault_secrets: {dberr}")
    finally:
        conn.close()

    # 3. Query AWS Secrets Manager secrets (via _load_aws_secrets & boto3 client)
    try:
        from app.tools.secrets_manager import _load_aws_secrets
        aws_secrets_map = _load_aws_secrets()
        if isinstance(aws_secrets_map, dict):
            for aws_k in aws_secrets_map.keys():
                norm_ak = aws_k.strip().lower()
                if norm_ak not in seen_key_names:
                    seen_key_names.add(norm_ak)
                    result.append({
                        "id": len(result) + 1000,
                        "key_name": aws_k,
                        "aws_secret_name": f"AWS Secrets Manager ({os.getenv('AWS_SECRET_NAME', 'JTSP_secret_manager')})",
                        "created_at": None,
                        "updated_at": None,
                    })
    except Exception as ex:
        logger.debug(f"[VAULT] Error loading AWS secrets map: {ex}")

    # 4. Discover all individual secrets & JSON keys in AWS Secrets Manager using boto3
    client = _get_secretsmanager_client()
    aws_secret_bundle_str = os.getenv("AWS_SECRET_NAMES", "").strip() or os.getenv("AWS_SECRET_NAME", "JTSP_secret_manager").strip() or "JTSP_secret_manager"
    aws_secret_bundle_names = [s.strip() for s in aws_secret_bundle_str.split(",") if s.strip()]

    if client:
        # 4a. Read configured AWS Secret Bundle JSON keys if specified
        for bundle_name in aws_secret_bundle_names:
            try:
                resp = client.get_secret_value(SecretId=bundle_name)
                if "SecretString" in resp:
                    bundle_data = json.loads(resp["SecretString"])
                    if isinstance(bundle_data, dict):
                        for key_name in bundle_data.keys():
                            norm_key = key_name.strip().lower()
                            if norm_key not in seen_key_names:
                                seen_key_names.add(norm_key)
                                result.append({
                                    "id": len(result) + 1500,
                                    "key_name": key_name,
                                    "aws_secret_name": f"{bundle_name} -> {key_name}",
                                    "created_at": None,
                                    "updated_at": None,
                                })
            except Exception as e:
                logger.warning(f"[VAULT] Could not load AWS secret bundle ({bundle_name}): {e}")

        # 4b. Discover and expand all individual secrets stored in AWS Secrets Manager
        try:
            paginator = client.get_paginator("list_secrets")
            for page in paginator.paginate():
                for secret_entry in page.get("SecretList", []):
                    s_name = secret_entry.get("Name", "")
                    if not s_name:
                        continue

                    created_ts = secret_entry.get("CreatedDate")
                    updated_ts = secret_entry.get("LastChangedDate") or created_ts
                    created_iso = created_ts.isoformat() if hasattr(created_ts, "isoformat") else None
                    updated_iso = updated_ts.isoformat() if hasattr(updated_ts, "isoformat") else None

                    # Inspect SecretString contents to expand JSON keys
                    is_expanded = False
                    try:
                        sv_resp = client.get_secret_value(SecretId=s_name)
                        if "SecretString" in sv_resp:
                            s_content = sv_resp["SecretString"]
                            try:
                                s_json = json.loads(s_content)
                                if isinstance(s_json, dict):
                                    if "key_name" in s_json and "key_value" in s_json:
                                        kname = s_json["key_name"]
                                        norm_kname = kname.strip().lower()
                                        if norm_kname not in seen_key_names:
                                            seen_key_names.add(norm_kname)
                                            result.append({
                                                "id": len(result) + 2000,
                                                "key_name": kname,
                                                "aws_secret_name": s_name,
                                                "created_at": created_iso,
                                                "updated_at": updated_iso,
                                            })
                                        is_expanded = True
                                    elif "api_key" in s_json and "provider" in s_json:
                                        provider = s_json["provider"].title()
                                        kname = f"{provider} Secret ({s_name})"
                                        norm_kname = kname.strip().lower()
                                        if norm_kname not in seen_key_names:
                                            seen_key_names.add(norm_kname)
                                            result.append({
                                                "id": len(result) + 2000,
                                                "key_name": kname,
                                                "aws_secret_name": s_name,
                                                "created_at": created_iso,
                                                "updated_at": updated_iso,
                                            })
                                        is_expanded = True
                                    else:
                                        for dict_key in s_json.keys():
                                            norm_dkey = dict_key.strip().lower()
                                            if norm_dkey not in seen_key_names:
                                                seen_key_names.add(norm_dkey)
                                                result.append({
                                                    "id": len(result) + 2000,
                                                    "key_name": dict_key,
                                                    "aws_secret_name": f"{s_name} -> {dict_key}",
                                                    "created_at": created_iso,
                                                    "updated_at": updated_iso,
                                                })
                                        is_expanded = True
                            except Exception:
                                pass
                    except Exception as gsv_err:
                        logger.debug(f"[VAULT] Could not inspect value for secret '{s_name}': {gsv_err}")

                    if not is_expanded:
                        display_name = s_name
                        if "/vault/" in s_name:
                            display_name = s_name.split("/vault/")[-1].replace("-", " ").title()
                        elif "/" in s_name:
                            display_name = s_name.split("/")[-1].replace("-", " ").title()

                        norm_name = display_name.strip().lower()
                        norm_sname = s_name.strip().lower()
                        if norm_name not in seen_key_names and norm_sname not in seen_key_names:
                            seen_key_names.add(norm_name)
                            result.append({
                                "id": len(result) + 2000,
                                "key_name": display_name,
                                "aws_secret_name": s_name,
                                "created_at": created_iso,
                                "updated_at": updated_iso,
                            })
        except Exception as le:
            logger.warning(f"[VAULT] Error listing AWS Secrets Manager secrets: {le}")

    # 5. Check local mock storage fallback
    for s_name, s_val in _MOCK_AWS_SECRETS.items():
        try:
            mdata = json.loads(s_val)
            if isinstance(mdata, dict):
                if "key_name" in mdata:
                    kname = mdata["key_name"]
                    if kname.strip().lower() not in seen_key_names:
                        seen_key_names.add(kname.strip().lower())
                        result.append({
                            "id": len(result) + 3000,
                            "key_name": kname,
                            "aws_secret_name": s_name,
                            "created_at": None,
                            "updated_at": None,
                        })
                else:
                    for k in mdata.keys():
                        if k.strip().lower() not in seen_key_names:
                            seen_key_names.add(k.strip().lower())
                            result.append({
                                "id": len(result) + 3000,
                                "key_name": k,
                                "aws_secret_name": s_name,
                                "created_at": None,
                                "updated_at": None,
                            })
        except Exception:
            pass

    # 6. Check standard provider and IAM secret keys via get_secret (AWS Secrets Manager + env)
    from app.tools.secrets_manager import get_secret
    env_keys_to_check = [
        ("ANTHROPIC_API_KEY", "Anthropic API Key"),
        ("OPENAI_API_KEY", "OpenAI API Key"),
        ("SLACK_BOT_TOKEN", "Slack Bot Token"),
        ("GITHUB_PERSONAL_ACCESS_TOKEN", "GitHub Personal Access Token"),
        ("JIRA_API_TOKEN", "Jira API Token"),
        ("CLOUDFLARE_TUNNEL_TOKEN", "Cloudflare Tunnel Token"),
        ("JTS_ADMIN_TOKEN", "JTS Admin Token"),
    ]
    for env_var, label in env_keys_to_check:
        val = get_secret(env_var, "").strip() or os.getenv(env_var, "").strip()
        if val:
            norm_label = label.strip().lower()
            norm_var = env_var.strip().lower()
            if norm_label not in seen_key_names and norm_var not in seen_key_names:
                seen_key_names.add(norm_label)
                result.append({
                    "id": len(result) + 4000,
                    "key_name": label,
                    "aws_secret_name": f"AWS Secrets Manager / IAM: {env_var}",
                    "created_at": None,
                    "updated_at": None,
                })

    # 7. Check registered multi-workspace Slack tokens
    try:
        from app.db.repositories import list_slack_workspaces
        for ws in list_slack_workspaces():
            t_id = ws.get("team_id", "")
            t_name = ws.get("team_name") or t_id
            created_ts = ws.get("created_at")
            created_iso = created_ts.isoformat() if hasattr(created_ts, "isoformat") else None
            ws_label = f"Slack Bot Token ({t_name} - {t_id})"
            if ws_label.strip().lower() not in seen_key_names:
                seen_key_names.add(ws_label.strip().lower())
                result.append({
                    "id": len(result) + 5000,
                    "key_name": ws_label,
                    "aws_secret_name": f"PostgreSQL / Slack Workspace: {t_id}",
                    "created_at": created_iso,
                    "updated_at": created_iso,
                })
    except Exception as ws_ex:
        logger.debug(f"[VAULT] Error listing slack workspaces in vault: {ws_ex}")

    return result


def store_vault_secret(*, key_name: str, key_value: str, name: Optional[str] = None) -> Dict[str, Any]:
    """
    Stores a key-value secret in AWS Secrets Manager using IAM permissions,
    and records key_name + aws_secret_name in PostgreSQL secrets_vault table.
    Invalidates in-memory secret caches so new values are immediately accessible.
    """
    if not key_name or not key_name.strip():
        raise ValueError("key_name is required")
    if not key_value or not key_value.strip():
        raise ValueError("key_value cannot be empty")

    # Enforce uppercase format with underscores and no spaces
    key_name = re.sub(r"\s+", "_", key_name.strip()).upper()
    key_value = key_value.strip()

    slug = _slugify(key_name)
    prefix = os.getenv("AWS_SECRET_PREFIX", "jts-powertool").strip().strip("/")
    secret_name = f"{prefix}/vault/{slug}"

    secret_payload = json.dumps({
        "key_name": key_name,
        "key_value": key_value,
        "updated_at": time.time(),
    })

    aws_success = False
    client = _get_secretsmanager_client()
    aws_secret_bundle_name = os.getenv("AWS_SECRET_NAME", "JTSP_secret_manager").strip() or "JTSP_secret_manager"

    if client:
        try:
            # 1. Update AWS_SECRET_NAME JSON bundle if configured
            if aws_secret_bundle_name:
                try:
                    existing_bundle = {}
                    try:
                        b_resp = client.get_secret_value(SecretId=aws_secret_bundle_name)
                        if "SecretString" in b_resp:
                            existing_bundle = json.loads(b_resp["SecretString"])
                    except Exception:
                        existing_bundle = {}

                    existing_bundle[key_name] = key_value
                    client.put_secret_value(
                        SecretId=aws_secret_bundle_name,
                        SecretString=json.dumps(existing_bundle)
                    )
                    aws_success = True
                    logger.info(f"[VAULT] Updated key '{key_name}' inside AWS secret bundle '{aws_secret_bundle_name}'")
                except Exception as be:
                    logger.warning(f"[VAULT] Could not update AWS secret bundle '{aws_secret_bundle_name}': {be}")

            # 2. Store individual AWS secret (jts-powertool/vault/<slug>)
            try:
                client.create_secret(
                    Name=secret_name,
                    Description=f"JTS-PowerTool Vault Secret: {key_name}",
                    SecretString=secret_payload,
                    Tags=[{"Key": "Application", "Value": "JTS-PowerTool"}, {"Key": "VaultKey", "Value": slug}],
                )
                aws_success = True
            except Exception as ce:
                if "ResourceExists" in str(ce) or "already exists" in str(ce):
                    client.put_secret_value(SecretId=secret_name, SecretString=secret_payload)
                    aws_success = True
                else:
                    logger.warning(f"[VAULT] Could not create AWS secret '{secret_name}': {ce}")
        except Exception as e:
            logger.warning(f"[VAULT] AWS Secrets Manager error ({e}). Using local database persistence.")

    if not aws_success:
        _MOCK_AWS_SECRETS[secret_name] = secret_payload
        if aws_secret_bundle_name:
            try:
                bundle = json.loads(_MOCK_AWS_SECRETS.get(aws_secret_bundle_name, "{}"))
                bundle[key_name] = key_value
                _MOCK_AWS_SECRETS[aws_secret_bundle_name] = json.dumps(bundle)
            except Exception:
                pass

    # 3. Clear cache so the app immediately reflects new secret values
    try:
        from app.tools.secrets_manager import clear_secrets_cache
        clear_secrets_cache()
    except Exception as cex:
        logger.debug(f"[VAULT] Could not clear secrets cache: {cex}")

    # 4. Save metadata to PostgreSQL secrets_vault table
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("ALTER TABLE secrets_vault ADD COLUMN IF NOT EXISTS name VARCHAR(255);")
                conn.commit()
            except Exception:
                conn.rollback()

            cur.execute("""
                INSERT INTO secrets_vault (key_name, aws_secret_name, name, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key_name) DO UPDATE SET
                    aws_secret_name = EXCLUDED.aws_secret_name,
                    name = COALESCE(EXCLUDED.name, secrets_vault.name),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id, key_name, aws_secret_name, name, created_at;
            """, (key_name, secret_name, name or "JTS Admin"))
            row = cur.fetchone()
            conn.commit()

            # 5. If this is a Slack bot token, auto-register workspace in slack_workspaces table
            if "SLACK" in key_name.upper() and ("TOKEN" in key_name.upper() or "BOT" in key_name.upper()):
                try:
                    from app.db.repositories import save_slack_workspace
                    team_suffix = key_name.upper().replace("SLACK_BOT_TOKEN_", "").replace("SLACK_TOKEN_", "").replace("SLACK_BOT_TOKEN", "").strip("_")
                    target_team_id = team_suffix if team_suffix else ("T5ZMF56H5" if "AXCEL" in key_name.upper() else "T02HKMBE09K")
                    target_team_name = "Axcel World" if ("AXCEL" in target_team_id or target_team_id == "T5ZMF56H5") else (
                        "JTS Team" if ("JTS" in target_team_id or target_team_id == "T02HKMBE09K") else target_team_id
                    )
                    save_slack_workspace(team_id=target_team_id, team_name=target_team_name, bot_token=key_value)
                    if "AXCEL" in target_team_id or "AXCEL" in key_name.upper():
                        save_slack_workspace(team_id="T5ZMF56H5", team_name="Axcel World", bot_token=key_value)
                    logger.info(f"[VAULT] Auto-synced Slack workspace {target_team_name} ({target_team_id}) with token from vault.")
                except Exception as ws_err:
                    logger.debug(f"[VAULT] Could not auto-sync workspace from token: {ws_err}")

            if isinstance(row, dict):
                u_name = row.get("name") or name or "JTS Admin"
                c_at = row.get("created_at")
                c_iso = c_at.isoformat() if hasattr(c_at, "isoformat") else (str(c_at) if c_at else None)
                return {
                    "id": row.get("id", 1),
                    "key_name": row.get("key_name", key_name),
                    "aws_secret_name": row.get("aws_secret_name", secret_name),
                    "name": u_name,
                    "full_name": u_name,
                    "created_at": c_iso,
                }
            
            u_name = name or "JTS Admin"
            return {
                "id": 1,
                "key_name": key_name,
                "aws_secret_name": secret_name,
                "name": u_name,
                "full_name": u_name,
                "created_at": None,
            }
    finally:
        conn.close()



def delete_vault_secret(vault_id: int) -> bool:
    """Deletes secret from secrets_vault table and AWS Secrets Manager."""
    conn = get_db_connection()
    secret_name = None
    key_name = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT key_name, aws_secret_name FROM secrets_vault WHERE id = %s;", (vault_id,))
            row = cur.fetchone()
            if row:
                key_name = row.get("key_name")
                secret_name = row.get("aws_secret_name")

            cur.execute("DELETE FROM secrets_vault WHERE id = %s;", (vault_id,))
            conn.commit()
    finally:
        conn.close()

    client = _get_secretsmanager_client()
    aws_secret_bundle_name = os.getenv("AWS_SECRET_NAME", "").strip()

    if client:
        # Delete from AWS bundle if key_name is present
        if aws_secret_bundle_name and key_name:
            try:
                b_resp = client.get_secret_value(SecretId=aws_secret_bundle_name)
                if "SecretString" in b_resp:
                    bundle_data = json.loads(b_resp["SecretString"])
                    if key_name in bundle_data:
                        del bundle_data[key_name]
                        client.put_secret_value(SecretId=aws_secret_bundle_name, SecretString=json.dumps(bundle_data))
            except Exception as be:
                logger.warning(f"[VAULT] Error removing key from AWS bundle: {be}")

        # Delete individual secret
        if secret_name:
            try:
                client.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
            except Exception as e:
                logger.warning(f"[VAULT] Failed to delete AWS secret '{secret_name}': {e}")
    else:
        if secret_name:
            _MOCK_AWS_SECRETS.pop(secret_name, None)

    try:
        from app.tools.secrets_manager import clear_secrets_cache
        clear_secrets_cache()
    except Exception:
        pass

    return True


# --- Client (Folder) Own API Keys ---
# A client folder can bring its own provider key. When set, Claude calls for every
# channel in that folder use the client's key and are NOT billed by JTS.
# When not set, the JTS key is used and usage is billed to the client.

_FOLDER_KEY_CACHE: Dict[tuple, tuple] = {}
FOLDER_KEY_CACHE_TTL_SECONDS = 60
_folder_keys_table_ready = False


def _ensure_folder_keys_table(cur):
    global _folder_keys_table_ready
    if _folder_keys_table_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS folder_api_keys (
            folder_id INTEGER NOT NULL REFERENCES channel_folders(id) ON DELETE CASCADE,
            provider VARCHAR(50) NOT NULL,
            aws_secret_name VARCHAR(512) NOT NULL,
            key_hint VARCHAR(32),
            updated_by VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (folder_id, provider)
        );
        ALTER TABLE folder_api_keys ADD COLUMN IF NOT EXISTS last_error TEXT;
        ALTER TABLE folder_api_keys ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMP WITH TIME ZONE;
    """)
    _folder_keys_table_ready = True


def _mask_key(api_key: str) -> str:
    clean = (api_key or "").strip()
    return f"...{clean[-4:]}" if len(clean) >= 8 else "****"


def _is_missing_aws_credentials(err: Exception) -> bool:
    msg = str(err)
    return "NoCredentials" in type(err).__name__ or "Unable to locate credentials" in msg


def validate_anthropic_key(api_key: str) -> tuple[bool, str]:
    """Checks the key against Anthropic. Only a definite rejection (401/403) counts as invalid."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
        if resp.status_code in (401, 403):
            return False, "Anthropic rejected this API key. Please check the key and try again."
        return True, ""
    except Exception as e:
        logger.warning(f"[FOLDER_KEYS] Could not verify Anthropic key (network): {e}")
        return True, ""


def store_folder_api_key(*, folder_id: int, api_key: str, provider: str = "anthropic", updated_by: str = "admin") -> Dict[str, Any]:
    """Stores a client folder's own provider key in AWS Secrets Manager and records safe metadata only."""
    provider = provider.strip().lower()
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("API key cannot be empty.")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM channel_folders WHERE id = %s;", (folder_id,))
            if not cur.fetchone():
                raise LookupError(f"Folder with id {folder_id} not found.")
            _ensure_folder_keys_table(cur)
            conn.commit()
    finally:
        conn.close()

    prefix = os.getenv("AWS_SECRET_PREFIX", "jts-powertool").strip().strip("/")
    secret_name = f"{prefix}/folders/{folder_id}/{provider}"
    secret_payload = json.dumps({
        "api_key": api_key,
        "provider": provider,
        "folder_id": folder_id,
        "updated_at": time.time(),
        "updated_by": updated_by,
    })

    client = _get_secretsmanager_client()
    stored = False
    if client:
        try:
            try:
                client.create_secret(
                    Name=secret_name,
                    Description=f"JTS-PowerTool client-owned {provider} key for folder {folder_id}",
                    SecretString=secret_payload,
                    Tags=[
                        {"Key": "Application", "Value": "JTS-PowerTool"},
                        {"Key": "FolderId", "Value": str(folder_id)},
                        {"Key": "Provider", "Value": provider},
                    ],
                )
            except Exception as ce:
                if "ResourceExists" in str(ce) or "already exists" in str(ce):
                    client.put_secret_value(SecretId=secret_name, SecretString=secret_payload)
                else:
                    raise
            stored = True
        except Exception as e:
            if not _is_missing_aws_credentials(e):
                logger.error(f"[FOLDER_KEYS] Failed to save secret to AWS Secrets Manager: {e}")
                raise RuntimeError(f"AWS Secrets Manager error: {e}")
    if not stored:
        _MOCK_AWS_SECRETS[secret_name] = secret_payload
        logger.info(f"[FOLDER_KEYS] Saved to in-memory mock AWS Secrets: {secret_name}")

    _FOLDER_KEY_CACHE.pop((folder_id, provider), None)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO folder_api_keys (folder_id, provider, aws_secret_name, key_hint, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (folder_id, provider) DO UPDATE SET
                    aws_secret_name = EXCLUDED.aws_secret_name,
                    key_hint = EXCLUDED.key_hint,
                    updated_by = EXCLUDED.updated_by,
                    last_error = NULL,
                    last_error_at = NULL,
                    updated_at = CURRENT_TIMESTAMP;
            """, (folder_id, provider, secret_name, _mask_key(api_key), updated_by))
            conn.commit()
    finally:
        conn.close()

    return get_folder_api_key_status(folder_id, provider)


def get_folder_api_key_status(folder_id: int, provider: str = "anthropic") -> Dict[str, Any]:
    """Safe metadata only: whether a client key is configured, and a masked hint. Never the key itself."""
    provider = provider.strip().lower()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_folder_keys_table(cur)
            conn.commit()
            cur.execute("""
                SELECT key_hint, updated_by, updated_at, last_error, last_error_at
                FROM folder_api_keys
                WHERE folder_id = %s AND provider = %s;
            """, (folder_id, provider))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return {
            "folder_id": folder_id,
            "provider": provider,
            "configured": False,
            "billing_mode": "jts_billed",
            "key_hint": None,
            "updated_by": None,
            "updated_at": None,
            "key_status": "none",
            "last_error": None,
            "last_error_at": None,
        }
    upd = row.get("updated_at")
    err_at = row.get("last_error_at")
    return {
        "folder_id": folder_id,
        "provider": provider,
        "configured": True,
        "billing_mode": "client_key",
        "key_hint": row.get("key_hint"),
        "updated_by": row.get("updated_by"),
        "updated_at": upd.isoformat() if hasattr(upd, "isoformat") else upd,
        # "failing": the client's key was rejected; messages are answered with the JTS key and billed
        "key_status": "failing" if row.get("last_error") else "ok",
        "last_error": row.get("last_error"),
        "last_error_at": err_at.isoformat() if hasattr(err_at, "isoformat") else err_at,
    }


def set_folder_key_health(folder_id: int, error: Optional[str], provider: str = "anthropic") -> None:
    """error=str marks the client's key as failing; error=None clears it after a successful call."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_folder_keys_table(cur)
            if error:
                cur.execute("""
                    UPDATE folder_api_keys SET last_error = %s, last_error_at = CURRENT_TIMESTAMP
                    WHERE folder_id = %s AND provider = %s;
                """, (error[:500], folder_id, provider))
            else:
                cur.execute("""
                    UPDATE folder_api_keys SET last_error = NULL, last_error_at = NULL
                    WHERE folder_id = %s AND provider = %s AND last_error IS NOT NULL;
                """, (folder_id, provider))
            conn.commit()
    except Exception as e:
        logger.warning(f"[FOLDER_KEYS] Could not update key health for folder {folder_id}: {e}")
    finally:
        conn.close()


def delete_folder_api_key(folder_id: int, provider: str = "anthropic") -> bool:
    """Removes a client's own key; the folder falls back to the (billed) JTS key."""
    provider = provider.strip().lower()
    secret_name = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_folder_keys_table(cur)
            cur.execute(
                "DELETE FROM folder_api_keys WHERE folder_id = %s AND provider = %s RETURNING aws_secret_name;",
                (folder_id, provider),
            )
            row = cur.fetchone()
            conn.commit()
            if row:
                secret_name = row.get("aws_secret_name")
    finally:
        conn.close()

    _FOLDER_KEY_CACHE.pop((folder_id, provider), None)

    if secret_name:
        client = _get_secretsmanager_client()
        if client:
            try:
                client.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
            except Exception as e:
                logger.warning(f"[FOLDER_KEYS] Could not delete secret '{secret_name}' from AWS: {e}")
        _MOCK_AWS_SECRETS.pop(secret_name, None)
    return bool(secret_name)


def get_folder_api_key_value(folder_id: int, provider: str = "anthropic") -> Optional[str]:
    """Returns the client's own key for runtime use (worker only). Never expose via API."""
    provider = provider.strip().lower()
    cached = _FOLDER_KEY_CACHE.get((folder_id, provider))
    if cached and time.time() < cached[1]:
        return cached[0]

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_folder_keys_table(cur)
            conn.commit()
            cur.execute(
                "SELECT aws_secret_name FROM folder_api_keys WHERE folder_id = %s AND provider = %s;",
                (folder_id, provider),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None

    secret_name = row.get("aws_secret_name")
    raw_secret = None
    client = _get_secretsmanager_client()
    if client:
        try:
            resp = client.get_secret_value(SecretId=secret_name)
            raw_secret = resp.get("SecretString")
        except Exception as e:
            if not _is_missing_aws_credentials(e):
                logger.warning(f"[FOLDER_KEYS] Could not fetch secret '{secret_name}' from AWS: {e}")
    if not raw_secret:
        raw_secret = _MOCK_AWS_SECRETS.get(secret_name)
    if not raw_secret:
        return None

    try:
        key_val = json.loads(raw_secret).get("api_key")
    except Exception:
        key_val = raw_secret
    if key_val:
        _FOLDER_KEY_CACHE[(folder_id, provider)] = (key_val, time.time() + FOLDER_KEY_CACHE_TTL_SECONDS)
    return key_val


# Some early channels were stored with a mistyped ID ('8' instead of 'B'). Slack sends the real ID, so treat
# each pair as the same channel when looking up its client.
LEGACY_CHANNEL_ALIASES: Dict[str, str] = {
    "C0BV6S5UJ0P": "C08V6S5UJ0P",
    "C0BMV3EM9PY": "C08MV3EM9PY",
    "D0BSLP9LXUZ": "D08SLP9LXUZ",
}
LEGACY_CHANNEL_ALIASES.update({v: k for k, v in list(LEGACY_CHANNEL_ALIASES.items())})


def channel_id_variants(channel_id: str) -> List[str]:
    """The channel ID plus its legacy alias (upper-case), if it has one."""
    cid = (channel_id or "").strip().upper()
    if not cid:
        return []
    alias = LEGACY_CHANNEL_ALIASES.get(cid)
    return [cid, alias] if alias else [cid]


def get_folder_id_for_channel(channel_id: str) -> Optional[int]:
    if not channel_id:
        return None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT folder_id FROM channel_metadata WHERE UPPER(channel_id) = ANY(%s) AND folder_id IS NOT NULL LIMIT 1;",
                (channel_id_variants(channel_id),),
            )
            row = cur.fetchone()
            return int(row["folder_id"]) if row else None
    except Exception as e:
        logger.debug(f"[FOLDER_KEYS] Could not resolve folder for channel {channel_id}: {e}")
        return None
    finally:
        conn.close()


def resolve_anthropic_key(
    channel_id: str,
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    channel_name: Optional[str] = None,
) -> tuple[Optional[str], str, Optional[int]]:
    """
    Picks the Anthropic key for a channel and says who pays: (key, source, folder_id_if_folder_key)
      1. channel-specific key  -> ("...", "client", None)
      2. client folder's key   -> ("...", "client", folder_id)
      3. none                  -> (None, "jts", None)  caller uses the JTS key and usage is billed
    """
    try:
        key = get_channel_secret_value(
            channel_id, "anthropic",
            workspace_id=workspace_id, workspace_name=workspace_name, channel_name=channel_name,
        )
        if key:
            return key, "client", None
        folder_id = get_folder_id_for_channel(canonical_channel_id(channel_id))
        if folder_id:
            key = get_folder_api_key_value(folder_id, "anthropic")
            if key:
                return key, "client", folder_id
    except Exception as e:
        logger.warning(f"[FOLDER_KEYS] Key resolution failed for channel {channel_id}, using JTS key: {e}")
    return None, "jts", None


INTERNAL_FOLDER_PROVIDERS = {"github_user", "jira_oauth"}  # managed by the GitHub / Jira connections, never shown or edited as a key


def list_folder_api_keys(folder_id: int) -> List[Dict[str, Any]]:
    """Safe metadata for every key a client stored for itself (no values)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_folder_keys_table(cur)
            conn.commit()
            cur.execute(
                """
                SELECT provider, key_hint, updated_by, created_at, updated_at, last_error, last_error_at
                FROM folder_api_keys WHERE folder_id = %s ORDER BY provider;
                """,
                (folder_id,),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    out = []
    for r in rows:
        if r["provider"] in INTERNAL_FOLDER_PROVIDERS:
            continue
        rec = dict(r)
        for k in ("created_at", "updated_at", "last_error_at"):
            if rec.get(k) is not None and hasattr(rec[k], "isoformat"):
                rec[k] = rec[k].isoformat()
        rec["status"] = "failing" if rec.get("last_error") else "ok"
        out.append(rec)
    return out

