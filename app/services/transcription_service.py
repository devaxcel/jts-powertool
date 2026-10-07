"""Voice messages: turn audio into text so the assistant treats it like a typed message.

Two ways to transcribe, chosen with the TRANSCRIPTION_PROVIDER setting:
  - "openai" (default): OpenAI's speech-to-text API. Needs an OPENAI_API_KEY (add it on the API Keys page).
  - "local": Whisper running on this server through the `faster-whisper` package (no audio leaves the server).
    Install it with `pip install faster-whisper`; TRANSCRIPTION_LOCAL_MODEL picks the size (default "small").

Both detect the spoken language by themselves, so English and other languages work without a setting.
Nothing here ever raises: a failure becomes a short notice for the user.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Optional, Tuple

import httpx

from app.tools.secrets_manager import get_secret

logger = logging.getLogger("transcription")

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # the API limit; Slack clips are far smaller
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".oga", ".opus", ".flac", ".aac", ".webm", ".mp4", ".mpeg", ".mpga", ".amr")
# Audio comes through Slack as audio/*, or as video/mp4 / video/webm for recorded clips.
_VIDEO_AUDIO_TYPES = {"video/mp4", "video/webm", "video/ogg"}
_OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
_LOCAL_MODEL = None


def is_audio_file(name: str, mimetype: str = "", filetype: str = "") -> bool:
    """True for voice messages and audio uploads (not for ordinary videos, which stay unreadable attachments)."""
    mimetype = (mimetype or "").lower()
    filetype = (filetype or "").lower()
    name = (name or "").lower()
    if mimetype.startswith("audio/"):
        return True
    if filetype in {"mp3", "m4a", "wav", "ogg", "opus", "flac", "aac", "amr", "mpga"}:
        return True
    # Slack's own voice clips are named like "audio_message.mp4" / "Audio Recording.webm"
    if mimetype in _VIDEO_AUDIO_TYPES and ("audio" in name or "voice" in name):
        return True
    return name.endswith(AUDIO_EXTENSIONS) and not mimetype.startswith("video/")


def _provider() -> str:
    return (get_secret("TRANSCRIPTION_PROVIDER", "openai") or "openai").strip().lower()


async def _transcribe_openai(data: bytes, name: str) -> Tuple[Optional[str], Optional[str]]:
    key = get_secret("OPENAI_API_KEY", "")
    if not key:
        return None, "Voice messages need an OpenAI key. A JTS admin can add OPENAI_API_KEY on the API Keys page (or set TRANSCRIPTION_PROVIDER=local)."
    model = get_secret("TRANSCRIPTION_MODEL", "whisper-1") or "whisper-1"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                _OPENAI_URL,
                headers={"Authorization": f"Bearer {key}"},
                data={"model": model, "response_format": "json"},
                files={"file": (name or "voice.m4a", data)},
            )
        if resp.status_code != 200:
            logger.warning("OpenAI transcription failed: HTTP %s", resp.status_code)
            return None, f"The voice message could not be transcribed (speech service returned HTTP {resp.status_code})."
        return (resp.json().get("text") or "").strip(), None
    except Exception as e:  # network, timeout, bad JSON
        logger.warning("OpenAI transcription error: %s", e)
        return None, "The voice message could not be transcribed right now. Please try again."


def _transcribe_local_sync(data: bytes, name: str) -> Tuple[Optional[str], Optional[str]]:
    global _LOCAL_MODEL
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None, "Local transcription isn't installed on the server (pip install faster-whisper)."
    try:
        if _LOCAL_MODEL is None:
            size = os.getenv("TRANSCRIPTION_LOCAL_MODEL", "small")
            _LOCAL_MODEL = WhisperModel(size, device="cpu", compute_type="int8")
        suffix = os.path.splitext(name or "")[1] or ".m4a"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        try:
            segments, _info = _LOCAL_MODEL.transcribe(path)
            return " ".join(s.text.strip() for s in segments).strip(), None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception as e:
        logger.warning("Local transcription error: %s", e)
        return None, "The voice message could not be transcribed right now. Please try again."


async def transcribe_audio(data: bytes, name: str = "voice.m4a") -> Tuple[Optional[str], Optional[str]]:
    """Returns (text, None) on success or (None, short_reason_for_the_user)."""
    if not data:
        return None, "The voice message was empty."
    if len(data) > MAX_AUDIO_BYTES:
        return None, "The voice message is longer than 25 MB and could not be transcribed."
    if _provider() == "local":
        text, err = await asyncio.to_thread(_transcribe_local_sync, data, name)
    else:
        text, err = await _transcribe_openai(data, name)
    if err:
        return None, err
    if not text:
        return None, "I couldn't hear any speech in that voice message."
    return text, None
