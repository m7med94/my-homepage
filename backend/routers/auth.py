# backend/routers/auth.py — Authentication & Health Status Routes
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Header, Response, status

from backend.config import (
    require_bearer_token,
    sessions,
    SESSION_TTL_SECONDS,
    ACTIVE_GEMINI_MODEL,
)
from backend.events import subscribers
from backend.audio import is_ffmpeg_available

router = APIRouter(tags=["Auth & Health"])

@router.post("/api/v1/auth/session", status_code=status.HTTP_204_NO_CONTENT, summary="Create Dashboard Session")
def create_dashboard_session(response: Response, authorization: Optional[str] = Header(None)):
    """Exchange bearer token for a secure dashboard session cookie."""
    require_bearer_token(authorization, os.getenv("DASHBOARD_API_TOKEN"))
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    sessions[session_id] = now + SESSION_TTL_SECONDS
    for key, expires_at in list(sessions.items()):
        if expires_at <= now:
            sessions.pop(key, None)
    response.set_cookie(
        key="sensorshub_session",
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
    )
    return response

@router.get("/health", summary="Health Check")
def health_check():
    """System health check and live status metrics."""
    api_key_configured = bool(
        os.getenv("AI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")
    )
    return {
        "status": "healthy",
        "service": "ESP32 Voice Telemetry Gateway",
        "active_sse_subscribers": len(subscribers),
        "ai_server_key_configured": api_key_configured,
        "active_gemini_model": ACTIVE_GEMINI_MODEL,
        "ffmpeg_available": is_ffmpeg_available(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
