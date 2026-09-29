import json
import logging
import os
from typing import Dict, Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_CACHED_SECRETS: Optional[Dict[str, str]] = None
_INDIVIDUAL_CACHE: Dict[str, Optional[str]] = {}
_BOTO3_CLIENT = None


def _get_secretsmanager_client():
    """Returns a shared, cached boto3 Secrets Manager client to prevent repeated IAM role lookups."""
    global _BOTO3_CLIENT
    if _BOTO3_CLIENT is not None:
        return _BOTO3_CLIENT
    try:
        import boto3
        region_name = os.getenv("AWS_REGION", "us-east-2").strip() or "us-east-2"
        session = boto3.session.Session()
        _BOTO3_CLIENT = session.client(service_name="secretsmanager", region_name=region_name)
        return _BOTO3_CLIENT
    except ImportError:
        logger.debug("boto3 is not installed; skipping AWS Secrets Manager lookup.")
        return None
    except Exception as e:
        logger.warning(f"Could not create boto3 Secrets Manager client: {e}")
        return None


def clear_secrets_cache() -> None:
    """Invalidates the in-memory secrets cache so newly saved keys are reloaded from AWS Secrets Manager."""
    global _CACHED_SECRETS, _INDIVIDUAL_CACHE
    _CACHED_SECRETS = None
    _INDIVIDUAL_CACHE.clear()



def _load_aws_secrets() -> Dict[str, str]:
    """
    Attempts to fetch secrets from AWS Secrets Manager if AWS_SECRET_NAME is set.
    Returns an empty dict if AWS is not configured or on failure.
    """
    global _CACHED_SECRETS
    if _CACHED_SECRETS is not None:
        return _CACHED_SECRETS

    secret_name = os.getenv("AWS_SECRET_NAME", "JTSP_secret_manager").strip()
    if not secret_name:
        _CACHED_SECRETS = {}
        return _CACHED_SECRETS

    client = _get_secretsmanager_client()
    if client is None:
        _CACHED_SECRETS = {}
        return _CACHED_SECRETS

    try:
        response = client.get_secret_value(SecretId=secret_name)
        if "SecretString" in response:
            _CACHED_SECRETS = json.loads(response["SecretString"])
            logger.info(f"Successfully loaded secrets from AWS Secrets Manager: {secret_name}")
            return _CACHED_SECRETS
    except Exception as e:
        logger.warning(f"Could not load secrets from AWS Secrets Manager ({secret_name}): {e}")

    _CACHED_SECRETS = {}
    return _CACHED_SECRETS


def get_secret(key: str, default: str = "") -> str:
    """
    Retrieves a configuration secret.
    1. Checks AWS Secrets Manager bundle (default: JTSP_secret_manager in us-east-2)
    2. Checks in-memory cache of individual secrets (including negative misses)
    3. Checks individual AWS Secrets Manager path (jts-powertool/vault/<slug>)
    4. Checks environment variables
    5. Returns default if not found
    """
    if not key:
        return default

    # 1. Check AWS Secrets Manager bundle (JTSP_secret_manager)
    secret_name = os.getenv("AWS_SECRET_NAME", "JTSP_secret_manager").strip()
    if secret_name:
        aws_secrets = _load_aws_secrets()
        if key in aws_secrets and aws_secrets[key]:
            return str(aws_secrets[key]).strip()

    # 2. Check in-memory cache for individual secrets (hits & negative misses)
    if key in _INDIVIDUAL_CACHE:
        val = _INDIVIDUAL_CACHE[key]
        if val is not None:
            return val
        env_val = os.getenv(key)
        if env_val is not None and env_val.strip():
            return env_val.strip()
        return default

    # 3. Check individual AWS secret (e.g. jts-powertool/vault/<slug>)
    client = _get_secretsmanager_client()
    if client is not None:
        try:
            import re
            slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", key.strip().lower())
            prefix = os.getenv("AWS_SECRET_PREFIX", "jts-powertool").strip().strip("/")
            indiv_name = f"{prefix}/vault/{slug}"

            resp = client.get_secret_value(SecretId=indiv_name)
            if "SecretString" in resp:
                try:
                    parsed = json.loads(resp["SecretString"])
                    if isinstance(parsed, dict) and "key_value" in parsed:
                        val = str(parsed["key_value"]).strip()
                        if val:
                            _INDIVIDUAL_CACHE[key] = val
                            return val
                except Exception:
                    val = str(resp["SecretString"]).strip()
                    if val:
                        _INDIVIDUAL_CACHE[key] = val
                        return val
            _INDIVIDUAL_CACHE[key] = None
        except Exception:
            _INDIVIDUAL_CACHE[key] = None
    else:
        _INDIVIDUAL_CACHE[key] = None

    # 4. Fallback to os.getenv
    val = os.getenv(key)
    if val is not None and val.strip():
        return val.strip()

    return default


def get_slack_bot_token(team_id: Optional[str] = None) -> str:
    """
    Retrieves the Slack Bot Token for a specific workspace (team_id).
    1. Checks PostgreSQL slack_workspaces table for team_id (and alias/name match)
    2. Checks AWS Secrets Manager / env for specific SLACK_BOT_TOKEN_{team_id} and aliases
    3. Dynamically scans all AWS secrets and environment variables for matching workspace tokens
    4. Automatically persists discovered tokens back to PostgreSQL slack_workspaces
    5. Falls back to default SLACK_BOT_TOKEN
    """
    clean_team = (team_id or "").strip().upper()

    # 1. Database lookup first
    if clean_team:
        try:
            from app.db.repositories import get_slack_workspace_token
            db_token = get_slack_workspace_token(team_id.strip())
            if db_token and db_token.strip() and len(db_token.strip()) > 10:
                return db_token.strip()
        except Exception as dbe:
            logger.debug(f"DB lookup for slack workspace token {clean_team} failed: {dbe}")

    # 2. Check direct candidates in AWS Secrets Manager / environment
    if clean_team:
        candidates = [
            f"SLACK_BOT_TOKEN_{clean_team}",
            f"{clean_team}_SLACK_BOT_TOKEN",
            f"SLACK_{clean_team}_BOT_TOKEN",
            f"SLACK_TOKEN_{clean_team}",
            f"{clean_team}_TOKEN",
            f"{clean_team}_BOT_TOKEN",
        ]
        if "AXCEL" in clean_team or clean_team in ("T5ZMF56H5", "T01AXCELWORLD"):
            candidates.extend([
                "SLACK_BOT_TOKEN_T5ZMF56H5",
                "SLACK_BOT_TOKEN_AXCELWORLD",
                "SLACK_BOT_TOKEN_AXCEL_WORLD",
                "SLACK_BOT_TOKEN_AXCEL",
                "SLACK_BOT_TOKEN_T01AXCELWORLD",
                "SLACK_AXCEL_WORLD_BOT_TOKEN",
                "SLACK_AXCEL_BOT_TOKEN",
                "AXCEL_WORLD_SLACK_BOT_TOKEN",
                "AXCEL_SLACK_BOT_TOKEN",
                "AXCEL_WORLD_BOT_TOKEN",
                "AXCEL_BOT_TOKEN",
                "AXCEL_TOKEN",
                "SLACK_TOKEN_AXCEL_WORLD",
                "SLACK_TOKEN_AXCEL",
            ])
        elif "JTS" in clean_team or clean_team in ("T02HKMBE09K", "T02JTSTEAM"):
            candidates.extend([
                "SLACK_BOT_TOKEN_T02HKMBE09K",
                "SLACK_BOT_TOKEN_JTSTEAM",
                "SLACK_BOT_TOKEN_JTS_TEAM",
                "SLACK_BOT_TOKEN_JTS",
                "SLACK_BOT_TOKEN_T02JTSTEAM",
                "SLACK_JTS_TEAM_BOT_TOKEN",
                "SLACK_JTS_BOT_TOKEN",
                "JTS_TEAM_SLACK_BOT_TOKEN",
                "JTS_SLACK_BOT_TOKEN",
                "JTS_TEAM_BOT_TOKEN",
                "JTS_BOT_TOKEN",
                "JTS_TOKEN",
                "SLACK_TOKEN_JTS_TEAM",
                "SLACK_TOKEN_JTS",
            ])

        # Fast pass: check if candidate is already in loaded aws_secrets or os.environ
        aws_secrets = _load_aws_secrets()
        for cand in candidates:
            tok = (aws_secrets.get(cand) or os.getenv(cand) or "").strip()
            if tok and len(tok) > 10:
                _persist_discovered_token(team_id.strip(), tok)
                return tok

        # Fallback pass: check individual secret paths (cached with negative misses)
        for cand in candidates:
            tok = get_secret(cand, "").strip()
            if tok and len(tok) > 10:
                _persist_discovered_token(team_id.strip(), tok)
                return tok

        # 3. Dynamic search across all keys in AWS secrets and environment
        aws_secrets = _load_aws_secrets()
        all_sources = [(k, str(v).strip()) for k, v in aws_secrets.items()]
        all_sources.extend([(k, str(v).strip()) for k, v in os.environ.items()])

        for k, v in all_sources:
            if not v or not v.startswith("xoxb-"):
                continue
            k_upper = k.upper()
            if clean_team in k_upper:
                _persist_discovered_token(team_id.strip(), v)
                return v
            if ("AXCEL" in clean_team or clean_team in ("T5ZMF56H5", "T01AXCELWORLD")) and "AXCEL" in k_upper:
                _persist_discovered_token(team_id.strip(), v)
                return v
            if ("JTS" in clean_team or clean_team in ("T02HKMBE09K", "T02JTSTEAM")) and "JTS" in k_upper:
                _persist_discovered_token(team_id.strip(), v)
                return v

    # 4. Fallback to default SLACK_BOT_TOKEN
    fallback_tok = get_secret("SLACK_BOT_TOKEN", "").strip() or os.getenv("SLACK_BOT_TOKEN", "").strip()
    return fallback_tok


def _persist_discovered_token(team_id: str, token: str) -> None:
    """Helper to auto-persist a discovered workspace token to PostgreSQL slack_workspaces table."""
    if not team_id or not token or len(token) <= 10:
        return
    try:
        from app.db.repositories import save_slack_workspace
        team_name = "Axcel World" if ("AXCEL" in team_id.upper() or team_id.upper() == "T5ZMF56H5") else (
            "JTS Team" if ("JTS" in team_id.upper() or team_id.upper() == "T02HKMBE09K") else team_id
        )
        save_slack_workspace(team_id=team_id, team_name=team_name, bot_token=token)
    except Exception as e:
        logger.debug(f"Could not auto-persist discovered token for {team_id}: {e}")

