# backend/config.py — Configuration, Security & Rate Limiting
import os
import secrets
import time
from typing import Dict, List, Optional
from fastapi import HTTPException, Request, status

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "esp32_telemetry.db")
MUSIC_DIR = os.path.join(BASE_DIR, "music")
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")

os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(PLUGINS_DIR, exist_ok=True)

# Allowed audio formats for music streaming and uploading
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}

# Upload security limits
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

def validate_audio_magic_bytes(header_bytes: bytes, filename: str) -> bool:
    """
    Validates the magic byte signatures for uploaded audio files to protect
    against malicious executables or scripts disguised with audio file extensions.
    """
    if not header_bytes or len(header_bytes) < 4:
        return False
    ext = os.path.splitext(filename)[1].lower()
    # MP3: ID3 header or sync word frame
    if ext == ".mp3":
        return header_bytes.startswith(b"ID3") or (header_bytes[0] == 0xFF and (header_bytes[1] & 0xE0) == 0xE0)
    # WAV: RIFF container
    if ext == ".wav":
        return header_bytes.startswith(b"RIFF")
    # OGG / OPUS: OggS container
    if ext in {".ogg", ".opus"}:
        return header_bytes.startswith(b"OggS")
    # FLAC: fLaC magic header
    if ext == ".flac":
        return header_bytes.startswith(b"fLaC")
    # M4A / AAC: ftyp MP4 container or ADTS sync frames
    if ext in {".m4a", ".aac"}:
        return b"ftyp" in header_bytes[:16] or (header_bytes[0] == 0xFF and (header_bytes[1] & 0xF6) == 0xF0) or header_bytes.startswith(b"ID3")
    return True

# Automatic local .env loader
def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        os.environ[key.strip()] = val.strip().strip('"').strip("'")
        except Exception:
            pass

load_env()

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
    if origin.strip()
]

SESSION_TTL_SECONDS = 12 * 60 * 60
sessions: Dict[str, float] = {}
rate_limit_windows: Dict[str, List[float]] = {}
ACTIVE_GEMINI_MODEL: Optional[str] = "gemini-2.5-flash-lite"

def require_bearer_token(authorization: Optional[str], expected_token: Optional[str]):
    """Require an exact bearer token if expected_token is configured."""
    if not expected_token:
        return
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    scheme, _, supplied_token = authorization.partition(" ")
    if scheme != "Bearer" or not secrets.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

def require_dashboard_session(request: Request):
    """Require a dashboard session cookie only if DASHBOARD_API_TOKEN is set in environment."""
    dashboard_token = os.getenv("DASHBOARD_API_TOKEN")
    if not dashboard_token:
        return
    session_id = request.cookies.get("sensorshub_session")
    expires_at = sessions.get(session_id or "")
    if not expires_at or expires_at <= time.time():
        if session_id:
            sessions.pop(session_id, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Dashboard authentication required")

def enforce_rate_limit(bucket: str, limit: int, window_seconds: int = 60):
    """Small process-local guard against abuse."""
    now = time.monotonic()
    entries = [entry for entry in rate_limit_windows.get(bucket, []) if now - entry < window_seconds]
    if len(entries) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please retry shortly.",
        )
    entries.append(now)
    rate_limit_windows[bucket] = entries
