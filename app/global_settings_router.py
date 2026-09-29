import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth_router import get_user_context
from app.db.repositories import (
    get_global_system_settings,
    update_global_system_settings,
)
from app.log_stream import emit_telemetry

logger = logging.getLogger(__name__)

global_settings_router = APIRouter(prefix="/api/global-settings", tags=["Global System Settings"])


class GlobalSettingsResponse(BaseModel):
    timezone: str = Field(default="UTC", description="System primary timezone (e.g. UTC, Europe/London, Asia/Karachi)")
    date_format: str = Field(default="YYYY-MM-DD", description="Date display format")
    time_format: str = Field(default="12h", description="Time clock format (12h or 24h)")
    show_seconds: bool = Field(default=True, description="Whether to display seconds in timestamps")
    auto_dst: bool = Field(default=True, description="Daylight Saving Time auto adjustment")
    sync_alerts: bool = Field(default=True, description="Sync timezone tags with Slack & Telegram alerts")
    updated_at: Optional[str] = Field(default=None, description="ISO timestamp of last update")
    updated_by: Optional[str] = Field(default="admin", description="Admin username who last modified settings")


class UpdateGlobalSettingsRequest(BaseModel):
    timezone: Optional[str] = Field(None, description="System primary timezone (e.g. UTC, Europe/London, Asia/Karachi)")
    date_format: Optional[str] = Field(None, description="Date display format (YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY, DD MMM YYYY)")
    time_format: Optional[str] = Field(None, description="Time clock format (12h or 24h)")
    show_seconds: Optional[bool] = Field(None, description="Display seconds in timestamps")
    auto_dst: Optional[bool] = Field(None, description="Daylight Saving Time auto adjustment")
    sync_alerts: Optional[bool] = Field(None, description="Sync timezone tags with outgoing alert webhooks")


@global_settings_router.get("", response_model=GlobalSettingsResponse, summary="Get universal global settings")
@global_settings_router.get("/", response_model=GlobalSettingsResponse, include_in_schema=False)
def get_settings(request: Request):
    """
    Fetches the current global timezone, timestamp formatting, and alert clock configurations.
    Accessible to system users to ensure unified date/time representation.
    """
    try:
        data = get_global_system_settings()
        return GlobalSettingsResponse(**data)
    except Exception as e:
        logger.error(f"[GLOBAL_SETTINGS] Failed to get global settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve global settings: {str(e)}",
        )


@global_settings_router.put("", response_model=GlobalSettingsResponse, summary="Update universal global settings (Admin only)")
@global_settings_router.put("/", response_model=GlobalSettingsResponse, include_in_schema=False)
@global_settings_router.post("", response_model=GlobalSettingsResponse, summary="Update universal global settings (Admin only)")
@global_settings_router.post("/", response_model=GlobalSettingsResponse, include_in_schema=False)
def update_settings(payload: UpdateGlobalSettingsRequest, request: Request):
    """
    Updates the system-wide timezone and date/timestamp formatting configuration.
    Strictly restricted to JTS Master Admin (Client Admins cannot modify global system configurations).
    """
    caller = get_user_context(request, ignore_simulation=True)
    actual_role = caller.get("actual_role") or caller.get("role") or ""

    if actual_role not in ("jts_admin", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only JTS Master Admin has permission to modify global system settings.",
        )

    admin_user = caller.get("username") or "admin"

    try:
        updated = update_global_system_settings(
            timezone=payload.timezone,
            date_format=payload.date_format,
            time_format=payload.time_format,
            show_seconds=payload.show_seconds,
            auto_dst=payload.auto_dst,
            sync_alerts=payload.sync_alerts,
            updated_by=admin_user,
        )

        emit_telemetry(
            action="GLOBAL_SETTINGS_UPDATED",
            category="SYSTEM",
            level="INFO",
            user_id=admin_user,
            message=f"Admin '{admin_user}' updated global settings: tz={updated.get('timezone')}, date_fmt={updated.get('date_format')}, time_fmt={updated.get('time_format')}, sec={updated.get('show_seconds')}, dst={updated.get('auto_dst')}, sync={updated.get('sync_alerts')}",
        )

        return GlobalSettingsResponse(**updated)
    except Exception as e:
        logger.error(f"[GLOBAL_SETTINGS] Failed to update global settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update global settings: {str(e)}",
        )


@global_settings_router.post("/reset", response_model=GlobalSettingsResponse, summary="Reset global settings to defaults (Admin only)")
@global_settings_router.post("/reset/", response_model=GlobalSettingsResponse, include_in_schema=False)
def reset_settings(request: Request):
    """
    Resets all global system settings to default values (UTC, YYYY-MM-DD, 12h, show_seconds=True, auto_dst=True, sync_alerts=True).
    Strictly restricted to JTS Master Admin.
    """
    caller = get_user_context(request, ignore_simulation=True)
    actual_role = caller.get("actual_role") or caller.get("role") or ""

    if actual_role not in ("jts_admin", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Only JTS Master Admin has permission to reset global system settings.",
        )

    admin_user = caller.get("username") or "admin"

    try:
        reset_data = update_global_system_settings(
            timezone="UTC",
            date_format="YYYY-MM-DD",
            time_format="12h",
            show_seconds=True,
            auto_dst=True,
            sync_alerts=True,
            updated_by=admin_user,
        )

        emit_telemetry(
            action="GLOBAL_SETTINGS_RESET",
            category="SYSTEM",
            level="INFO",
            user_id=admin_user,
            message=f"Admin '{admin_user}' reset global settings to defaults (UTC / 12h / YYYY-MM-DD).",
        )

        return GlobalSettingsResponse(**reset_data)
    except Exception as e:
        logger.error(f"[GLOBAL_SETTINGS] Failed to reset global settings: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset global settings: {str(e)}",
        )
