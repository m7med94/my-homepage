# server.py — Production ESP32 AI Voice Assistant Backend & AI Copilot Gateway
import asyncio
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Set, Dict, Any

from fastapi import FastAPI, Header, HTTPException, Request, Query, status, Response, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure music storage directory exists
MUSIC_DIR = os.path.join(os.path.dirname(__file__), "music")
os.makedirs(MUSIC_DIR, exist_ok=True)

def convert_to_esp32_opus(input_path: str) -> Optional[str]:
    """
    Converts any uploaded audio track (MP3, WAV, M4A, etc.) to a high-efficiency
    16kHz mono OGG Opus stream specifically optimized for XiaoZhi ESP32 hardware playback.
    """
    try:
        base_name = os.path.splitext(input_path)[0]
        opus_path = f"{base_name}.ogg"
        
        # If already .ogg, check if valid
        if input_path.lower().endswith(".ogg") and os.path.exists(opus_path):
            return opus_path

        # Run ffmpeg to convert to 16kHz mono OGG Opus (32k bitrate)
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "libopus",
            "-b:a", "32k",
            opus_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
        if res.returncode == 0 and os.path.exists(opus_path):
            print(f"[Audio Converter] Converted '{os.path.basename(input_path)}' to ESP32 OGG Opus stream.")
            return opus_path
        else:
            print(f"[Audio Converter] ffmpeg not available or exited with {res.returncode}")
    except Exception as e:
        print(f"[Audio Converter] Auto-conversion note: {e}")
    return None

# Automatic local .env loader
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
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

# 1. Initialize FastAPI Application
app = FastAPI(
    title="SensorsHub Core & ESP32 AI Voice Assistant Gateway",
    description="High-performance telemetry ingestion, real-time SSE broadcasting, SQLite WAL persistence, Music Streaming, and Server-Side AI Copilot.",
    version="2.1.0",
)

# Mount /music directory for direct browser and ESP32 audio streaming
app.mount("/music", StaticFiles(directory=MUSIC_DIR), name="music")

# 2. Browser origin restrictions
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

DB_PATH = "esp32_telemetry.db"
subscribers: Set[asyncio.Queue] = set()
ACTIVE_GEMINI_MODEL: Optional[str] = "gemini-2.5-flash-lite"
SESSION_TTL_SECONDS = 12 * 60 * 60
sessions: Dict[str, float] = {}
rate_limit_windows: Dict[str, List[float]] = {}

def require_bearer_token(authorization: Optional[str], expected_token: Optional[str]):
    """Require an exact bearer token if expected_token is configured."""
    if not expected_token:
        # Open access when token is not configured
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
        # Open access when DASHBOARD_API_TOKEN is not configured
        return
    session_id = request.cookies.get("sensorshub_session")
    expires_at = sessions.get(session_id or "")
    if not expires_at or expires_at <= time.time():
        if session_id:
            sessions.pop(session_id, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Dashboard authentication required")

def enforce_rate_limit(bucket: str, limit: int, window_seconds: int = 60):
    """Small, process-local guard against abuse; use a shared limiter for multi-worker deployments."""
    now = time.monotonic()
    entries = [entry for entry in rate_limit_windows.get(bucket, []) if now - entry < window_seconds]
    if len(entries) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please retry shortly.",
        )
    entries.append(now)
    rate_limit_windows[bucket] = entries

# 3. High-Concurrency SQLite Database Initialization (WAL Mode)
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_logs (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                category TEXT NOT NULL,
                payload_data TEXT NOT NULL,
                client_ip TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_device_id ON telemetry_logs (device_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON telemetry_logs (category);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON telemetry_logs (created_at);")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                priority TEXT DEFAULT 'normal',
                completed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id TEXT PRIMARY KEY,
                playlist_id TEXT NOT NULL,
                track_filename TEXT NOT NULL,
                track_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_playlist_id ON playlist_tracks (playlist_id);")

        # Seed sample todos if table is freshly created and empty
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM todos")
        if cur.fetchone()[0] == 0:
            sample_todos = [
                ("1", "Check XiaoZhi ESP32 battery level & charging dock", "high", 0),
                ("2", "Calibrate living room DHT22 temperature sensor", "normal", 0),
                ("3", "Verify automated SQLite WAL backup schedule", "routine", 1),
            ]
            conn.executemany(
                "INSERT INTO todos (id, text, priority, completed) VALUES (?, ?, ?, ?)",
                sample_todos,
            )

        # Seed a default favorites playlist if empty
        cur.execute("SELECT COUNT(*) FROM playlists")
        if cur.fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO playlists (id, name, description) VALUES (?, ?, ?)",
                ("favs", "Favorites", "Top rotation tracks & ambient audio")
            )

init_db()

# 4. Incoming Payload Validation Schemas
class DevicePayload(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.:@ ]+$", description="Unique Device ID or MAC Address")
    category: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$", description="Payload category (user_request, telemetry, alert, general)")
    data: str = Field(..., min_length=1, max_length=4096, description="Voice transcript, sensor readings, or JSON payload")
    timestamp: Optional[int] = Field(None, description="Optional Unix timestamp from device")

class TodoCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000, description="Task description")
    priority: Optional[str] = Field("normal", description="Priority level: high, normal, routine")

class TodoUpdate(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=1000)
    priority: Optional[str] = Field(None)
    completed: Optional[bool] = Field(None)

class PlaylistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, description="Playlist title")
    description: Optional[str] = Field("", max_length=500)

class PlaylistAddTrack(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="User question or prompt")
    include_telemetry: Optional[bool] = Field(True, description="Whether to include live sensor context")

# 5. Dashboard session exchange. The dashboard token is never persisted in browser storage.
@app.post("/api/v1/auth/session", status_code=status.HTTP_204_NO_CONTENT, summary="Create Dashboard Session")
def create_dashboard_session(response: Response, authorization: Optional[str] = Header(None)):
    require_bearer_token(authorization, os.getenv("DASHBOARD_API_TOKEN"))
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    sessions[session_id] = now + SESSION_TTL_SECONDS
    # Opportunistically discard expired sessions.
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

# 6. Health Check Endpoint
@app.get("/health", summary="Health Check")
def health_check():
    api_key_configured = bool(
        os.getenv("AI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")
    )
    return {
        "status": "healthy",
        "service": "ESP32 Voice Telemetry Gateway",
        "active_sse_subscribers": len(subscribers),
        "ai_server_key_configured": api_key_configured,
        "active_gemini_model": ACTIVE_GEMINI_MODEL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# 7. Real-Time SSE Notification Stream (Server-Sent Events)
@app.get("/api/v1/events/stream", summary="Live Telemetry Event Stream")
async def event_stream(request: Request):
    """Server-Sent Events (SSE) stream to push instant notifications to dashboards whenever ESP32 transmits."""
    require_dashboard_session(request)
    queue = asyncio.Queue(maxsize=100)
    subscribers.add(queue)

    async def sse_generator():
        try:
            init_msg = json.dumps({"type": "connected", "message": "Listening for live ESP32 telemetry & voice events..."})
            yield f"data: {init_msg}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Keep proxies alive and re-check client disconnection regularly.
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            subscribers.discard(queue)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

# 8. POST Ingestion Endpoint (Stores to SQLite & Broadcasts Real-Time SSE Notification)
@app.post("/api/v1/device/data", status_code=status.HTTP_201_CREATED, summary="Ingest Device Payload")
async def ingest_device_data(
    payload: DevicePayload,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown"
    entry_id = str(uuid.uuid4())

    require_bearer_token(authorization, os.getenv("DEVICE_API_TOKEN"))
    enforce_rate_limit(f"device:{client_ip}", limit=120)

    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.execute(
                "INSERT INTO telemetry_logs (id, device_id, category, payload_data, client_ip) VALUES (?, ?, ?, ?, ?)",
                (entry_id, payload.device_id, payload.category, payload.data, client_ip),
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"status": "error", "message": f"Database write failed: {str(e)}"}
        )

    duration_ms = round((time.time() - start_time) * 1000, 2)
    timestamp_iso = datetime.now(timezone.utc).isoformat()
    
    notification_event = {
        "type": "esp32_data",
        "id": entry_id,
        "device_id": payload.device_id,
        "category": payload.category,
        "data": payload.data,
        "client_ip": client_ip,
        "timestamp": timestamp_iso,
        "latency_ms": duration_ms
    }

    event_str = json.dumps(notification_event)
    for q in list(subscribers):
        try:
            q.put_nowait(event_str)
        except asyncio.QueueFull:
            # Slow consumers must reconnect rather than retaining an unbounded event backlog.
            subscribers.discard(q)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [201 INGEST] Device:{payload.device_id} | Cat:{payload.category} | IP:{client_ip} | Latency:{duration_ms}ms")
    print(f"       [Payload Data]: {payload.data}")

    return {
        "status": "success",
        "message": "Data received and processed",
        "entry_id": entry_id,
        "device_id": payload.device_id,
        "received_at": timestamp_iso,
        "broadcast_subscribers": len(subscribers),
    }

# 9. GET Query Records Endpoint (Natural-Language Voice Summary + List)
@app.get("/api/v1/device/data", summary="Query Device Records & Voice Summary")
def query_device_data(
    request: Request,
    category: Optional[str] = Query(None, description="Filter by category"),
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    limit: int = Query(1, ge=1, le=500, description="Number of recent records"),
):
    require_dashboard_session(request)
    query = "SELECT id, device_id, category, payload_data, client_ip, created_at FROM telemetry_logs WHERE 1=1"
    params = []

    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    formatted_data = []
    for r in rows:
        formatted_data.append({
            "id": r["id"],
            "device_id": r["device_id"],
            "category": r["category"],
            "payload": r["payload_data"],
            "payload_data": r["payload_data"],
            "client_ip": r["client_ip"],
            "created_at": r["created_at"],
        })

    if formatted_data:
        latest = formatted_data[0]
        cat_name = latest["category"].replace("_", " ")
        summary = f"Latest {cat_name} is {latest['payload']} recorded from {latest['device_id']}"
    else:
        summary = "No telemetry data found for the requested criteria"

    return {
        "status": "success",
        "count": len(formatted_data),
        "data": formatted_data,
        "results": formatted_data,
        "summary": summary,
    }

# 10. GET Telemetry Stats Summary
@app.get("/api/v1/device/stats", summary="Telemetry Fleet Statistics")
def get_device_stats(request: Request):
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        total_records = conn.execute("SELECT COUNT(*) AS total FROM telemetry_logs").fetchone()["total"]
        unique_devices = [r["device_id"] for r in conn.execute("SELECT DISTINCT device_id FROM telemetry_logs").fetchall()]
        categories = [dict(r) for r in conn.execute("SELECT category, COUNT(*) as count FROM telemetry_logs GROUP BY category").fetchall()]
        latest_entry = conn.execute("SELECT created_at, device_id, payload_data FROM telemetry_logs ORDER BY created_at DESC LIMIT 1").fetchone()

    return {
        "status": "success",
        "total_records": total_records,
        "unique_devices": unique_devices,
        "categories": categories,
        "latest_transmission": dict(latest_entry) if latest_entry else None,
    }

# 11. To-Do List Management Endpoints (ESP32 Voice & Dashboard Synced)
@app.get("/api/v1/todos", summary="Get To-Do List & Voice Summary")
def get_todos(
    request: Request,
    completed: Optional[bool] = Query(None, description="Filter by completion status"),
):
    require_dashboard_session(request)
    query = "SELECT id, text, priority, completed, created_at, updated_at FROM todos WHERE 1=1"
    params = []
    if completed is not None:
        query += " AND completed = ?"
        params.append(1 if completed else 0)
    query += " ORDER BY completed ASC, CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC"

    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    todos = []
    pending_items = []
    for r in rows:
        is_done = bool(r["completed"])
        item = {
            "id": r["id"],
            "text": r["text"],
            "priority": r["priority"],
            "completed": is_done,
            "createdAt": r["created_at"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        todos.append(item)
        if not is_done:
            p_tag = f" ({r['priority']} priority)" if r["priority"] != "normal" else ""
            pending_items.append(f"{r['text']}{p_tag}")

    # Generate a voice-friendly natural language summary for XiaoZhi ESP32 / ServerAI
    if pending_items:
        count = len(pending_items)
        tasks_spoken = ", ".join([f"{idx+1}. {txt}" for idx, txt in enumerate(pending_items)])
        voice_summary = f"You have {count} pending task{'s' if count > 1 else ''}: {tasks_spoken}."
    else:
        voice_summary = "Your to-do list is completely clear. You have no pending tasks."

    return {
        "status": "success",
        "count": len(todos),
        "pending_count": len(pending_items),
        "summary": voice_summary,
        "voice_summary": voice_summary,
        "todos": todos,
        "data": todos,
    }

@app.post("/api/v1/todos", status_code=status.HTTP_201_CREATED, summary="Create To-Do Item")
def create_todo(payload: TodoCreate, request: Request):
    require_dashboard_session(request)
    todo_id = str(uuid.uuid4())
    priority = payload.priority.lower() if payload.priority else "normal"
    if priority not in ("high", "normal", "routine"):
        priority = "normal"

    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute(
            "INSERT INTO todos (id, text, priority, completed, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
            (todo_id, payload.text.strip(), priority, now_iso, now_iso),
        )

    # Broadcast notification to active SSE listeners
    notification = {
        "type": "todo_created",
        "id": todo_id,
        "text": payload.text.strip(),
        "priority": priority,
        "timestamp": now_iso,
    }
    for q in list(subscribers):
        try:
            q.put_nowait(json.dumps(notification))
        except Exception:
            subscribers.discard(q)

    return {
        "status": "success",
        "message": "Task added to to-do list",
        "id": todo_id,
        "todo": {
            "id": todo_id,
            "text": payload.text.strip(),
            "priority": priority,
            "completed": False,
            "createdAt": now_iso,
        },
    }

@app.patch("/api/v1/todos/{todo_id}", summary="Update or Toggle To-Do Item")
def update_todo(todo_id: str, payload: TodoUpdate, request: Request):
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not cur:
            raise HTTPException(status_code=404, detail="To-do item not found")

        updates = []
        params = []
        if payload.text is not None:
            updates.append("text = ?")
            params.append(payload.text.strip())
        if payload.priority is not None:
            updates.append("priority = ?")
            params.append(payload.priority.lower())
        if payload.completed is not None:
            updates.append("completed = ?")
            params.append(1 if payload.completed else 0)

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(todo_id)

        conn.execute(f"UPDATE todos SET {', '.join(updates)} WHERE id = ?", params)

    return {"status": "success", "message": "Task updated successfully"}

@app.delete("/api/v1/todos/{todo_id}", summary="Delete To-Do Item")
def delete_todo(todo_id: str, request: Request):
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        res = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="To-do item not found")

    return {"status": "success", "message": "Task deleted successfully"}

# 12. Music & Audio File Management Endpoints (ESP32 & Web Streaming)
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".opus"}

@app.get("/api/v1/music", summary="List Uploaded Music & Audio Tracks")
def list_music_files(request: Request):
    require_dashboard_session(request)
    tracks = []
    if os.path.exists(MUSIC_DIR):
        for fname in sorted(os.listdir(MUSIC_DIR)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in ALLOWED_AUDIO_EXTENSIONS:
                fpath = os.path.join(MUSIC_DIR, fname)
                try:
                    fstat = os.stat(fpath)
                    tracks.append({
                        "filename": fname,
                        "title": os.path.splitext(fname)[0],
                        "extension": ext.replace(".", "").upper(),
                        "size_bytes": fstat.st_size,
                        "size_mb": round(fstat.st_size / (1024 * 1024), 2),
                        "url": f"/music/{urllib.parse.quote(fname)}",
                        "created_at": datetime.fromtimestamp(fstat.st_mtime, timezone.utc).isoformat(),
                    })
                except Exception:
                    pass

    return {
        "status": "success",
        "count": len(tracks),
        "tracks": tracks,
    }

@app.post("/api/v1/music/upload", status_code=status.HTTP_201_CREATED, summary="Upload Music File (Single Part)")
async def upload_music_file(
    request: Request,
    file: UploadFile = File(...),
):
    require_dashboard_session(request)
    
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected")
        
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Allowed audio types: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}"
        )

    # Sanitize filename
    safe_filename = "".join(c for c in file.filename if c.isalnum() or c in "._- ").strip()
    if not safe_filename:
        safe_filename = f"track_{uuid.uuid4().hex[:8]}{ext}"

    dest_path = os.path.join(MUSIC_DIR, safe_filename)
    
    # Stream file to disk efficiently in chunks
    try:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file to disk: {str(e)}")
    finally:
        await file.close()

    file_size_bytes = os.path.getsize(dest_path)
    file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Automatically generate 16kHz mono OGG Opus for ESP32 hardware streaming
    convert_to_esp32_opus(dest_path)

    # Broadcast notification to active SSE listeners
    notification = {
        "type": "music_uploaded",
        "filename": safe_filename,
        "size_mb": file_size_mb,
        "timestamp": now_iso,
    }
    for q in list(subscribers):
        try:
            q.put_nowait(json.dumps(notification))
        except Exception:
            subscribers.discard(q)

    return {
        "status": "success",
        "message": f"Track '{safe_filename}' uploaded successfully",
        "track": {
            "filename": safe_filename,
            "title": os.path.splitext(safe_filename)[0],
            "extension": ext.replace(".", "").upper(),
            "size_mb": file_size_mb,
            "url": f"/music/{urllib.parse.quote(safe_filename)}",
            "created_at": now_iso,
        }
    }

@app.post("/api/v1/music/upload-chunk", summary="Upload Music Chunk (Bypasses Proxy Limits)")
async def upload_music_chunk(
    request: Request,
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
):
    require_dashboard_session(request)
    
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format '{ext}'")

    clean_id = "".join(c for c in upload_id if c.isalnum() or c in "-_")[:64]
    safe_filename = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    if not safe_filename:
        safe_filename = f"track_{uuid.uuid4().hex[:8]}{ext}"

    temp_path = os.path.join(MUSIC_DIR, f".tmp_{clean_id}_{safe_filename}")
    
    try:
        # Append chunk to temp file
        mode = "wb" if chunk_index == 0 else "ab"
        with open(temp_path, mode) as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write chunk {chunk_index}: {str(e)}")
    finally:
        await file.close()

    # If this was the last chunk, finalize the file
    if chunk_index + 1 >= total_chunks:
        dest_path = os.path.join(MUSIC_DIR, safe_filename)
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        os.rename(temp_path, dest_path)

        file_size_bytes = os.path.getsize(dest_path)
        file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
        now_iso = datetime.now(timezone.utc).isoformat()

        # Automatically generate 16kHz mono OGG Opus for ESP32 hardware streaming
        convert_to_esp32_opus(dest_path)

        # Broadcast SSE notification
        notification = {
            "type": "music_uploaded",
            "filename": safe_filename,
            "size_mb": file_size_mb,
            "timestamp": now_iso,
        }
        for q in list(subscribers):
            try:
                q.put_nowait(json.dumps(notification))
            except Exception:
                subscribers.discard(q)

        return {
            "status": "success",
            "completed": True,
            "message": f"Track '{safe_filename}' uploaded successfully ({file_size_mb} MB)",
            "track": {
                "filename": safe_filename,
                "title": os.path.splitext(safe_filename)[0],
                "extension": ext.replace(".", "").upper(),
                "size_mb": file_size_mb,
                "url": f"/music/{urllib.parse.quote(safe_filename)}",
                "created_at": now_iso,
            }
        }

    return {
        "status": "success",
        "completed": False,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
    }

@app.delete("/api/v1/music/{filename}", summary="Delete Music File")
def delete_music_file(filename: str, request: Request):
    require_dashboard_session(request)
    
    # Path traversal protection
    clean_name = os.path.basename(filename)
    target_path = os.path.join(MUSIC_DIR, clean_name)
    
    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Audio file not found")
        
    try:
        os.remove(target_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

    # Clean up playlist references to deleted track
    try:
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.execute("DELETE FROM playlist_tracks WHERE track_filename = ?", (clean_name,))
    except Exception:
        pass

    return {"status": "success", "message": f"Track '{clean_name}' deleted successfully"}

# 13. Playlist Management Endpoints
@app.get("/api/v1/playlists", summary="List All Playlists")
def list_playlists(request: Request):
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        p_rows = conn.execute("SELECT id, name, description, created_at FROM playlists ORDER BY name ASC").fetchall()
        
        playlists = []
        for p in p_rows:
            t_rows = conn.execute(
                "SELECT track_filename, track_order FROM playlist_tracks WHERE playlist_id = ? ORDER BY track_order ASC",
                (p["id"],)
            ).fetchall()
            
            tracks = []
            for t in t_rows:
                fname = t["track_filename"]
                fpath = os.path.join(MUSIC_DIR, fname)
                if os.path.exists(fpath):
                    tracks.append({
                        "filename": fname,
                        "title": os.path.splitext(fname)[0],
                        "url": f"/music/{urllib.parse.quote(fname)}"
                    })

            playlists.append({
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "created_at": p["created_at"],
                "track_count": len(tracks),
                "tracks": tracks
            })

    return {"status": "success", "count": len(playlists), "playlists": playlists}

@app.post("/api/v1/playlists", status_code=status.HTTP_201_CREATED, summary="Create Playlist")
def create_playlist(payload: PlaylistCreate, request: Request):
    require_dashboard_session(request)
    p_id = "pl_" + uuid.uuid4().hex[:10]
    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute(
            "INSERT INTO playlists (id, name, description, created_at) VALUES (?, ?, ?, ?)",
            (p_id, payload.name.strip(), payload.description.strip(), now_iso)
        )
    return {"status": "success", "id": p_id, "name": payload.name.strip()}

@app.post("/api/v1/playlists/{playlist_id}/tracks", summary="Add Track to Playlist")
def add_track_to_playlist(playlist_id: str, payload: PlaylistAddTrack, request: Request):
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        # Verify playlist exists
        p = conn.execute("SELECT id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if not p:
            raise HTTPException(status_code=404, detail="Playlist not found")
            
        clean_name = os.path.basename(payload.filename)
        # Verify track exists on disk
        if not os.path.exists(os.path.join(MUSIC_DIR, clean_name)):
            raise HTTPException(status_code=404, detail="Audio file not found on server")

        entry_id = uuid.uuid4().hex[:12]
        cur_count = conn.execute("SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO playlist_tracks (id, playlist_id, track_filename, track_order) VALUES (?, ?, ?, ?)",
            (entry_id, playlist_id, clean_name, cur_count)
        )
    return {"status": "success", "message": f"Added '{clean_name}' to playlist"}

@app.delete("/api/v1/playlists/{playlist_id}/tracks/{filename}", summary="Remove Track from Playlist")
def remove_track_from_playlist(playlist_id: str, filename: str, request: Request):
    require_dashboard_session(request)
    clean_name = os.path.basename(filename)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_filename = ?",
            (playlist_id, clean_name)
        )
    return {"status": "success", "message": f"Removed '{clean_name}' from playlist"}

@app.delete("/api/v1/playlists/{playlist_id}", summary="Delete Playlist")
def delete_playlist(playlist_id: str, request: Request):
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        res = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Playlist not found")
    return {"status": "success", "message": "Playlist deleted"}

# 14. Voice Assistant Music Resolver (For XiaoZhi ESP32 & ServerAI)
@app.get("/api/v1/music/voice-action", summary="Voice-Triggered Music Resolver for ESP32")
def resolve_voice_music(
    request: Request,
    query: Optional[str] = Query("", description="Song title, playlist, or keyword (e.g., 'relax', 'rock', 'random')"),
    action: Optional[str] = Query("play", description="Action: play, list, random"),
):
    require_dashboard_session(request)
    
    # Scan current library
    available_tracks = []
    if os.path.exists(MUSIC_DIR):
        for f in sorted(os.listdir(MUSIC_DIR)):
            if os.path.splitext(f)[1].lower() in ALLOWED_AUDIO_EXTENSIONS:
                available_tracks.append({
                    "filename": f,
                    "title": os.path.splitext(f)[0],
                    "url": f"/music/{urllib.parse.quote(f)}"
                })

    if not available_tracks:
        return {
            "status": "warning",
            "found": False,
            "summary": "Your music vault is currently empty. Please upload some songs to your server first.",
            "voice_summary": "Your music vault is currently empty. Please upload some songs to your server first.",
        }

    q = (query or "").strip().lower()

    if action == "list" or "what" in q or "list" in q or "songs" in q:
        titles = [t["title"] for t in available_tracks[:5]]
        count = len(available_tracks)
        summary = f"You have {count} track{'s' if count > 1 else ''} in your library, including: {', '.join(titles)}."
        return {
            "status": "success",
            "found": True,
            "summary": summary,
            "voice_summary": summary,
            "tracks": available_tracks
        }

    # If asking for random or no specific query
    def get_track_payload(t, act="play", prefix=""):
        base = os.path.splitext(t["filename"])[0]
        ogg_candidate = f"{base}.ogg"
        if os.path.exists(os.path.join(MUSIC_DIR, ogg_candidate)):
            esp32_url = f"/music/{urllib.parse.quote(ogg_candidate)}"
        else:
            esp32_url = t["url"]
        
        msg = f"{prefix}Playing {t['title']}." if prefix else f"Playing {t['title']}."
        return {
            "status": "success",
            "found": True,
            "action": act,
            "summary": msg,
            "voice_summary": msg,
            "track": t,
            "url": t["url"],
            "esp32_url": esp32_url,
            "filename": t["filename"]
        }

    if not q or "random" in q or "anything" in q or "shuffle" in q or q == "music":
        selected = available_tracks[secrets.randbelow(len(available_tracks))]
        return get_track_payload(selected, "play")

    # Match playlist name first
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        p_match = conn.execute("SELECT id, name FROM playlists WHERE lower(name) LIKE ?", (f"%{q}%",)).fetchone()
        if p_match:
            t_rows = conn.execute("SELECT track_filename FROM playlist_tracks WHERE playlist_id = ? ORDER BY track_order ASC", (p_match["id"],)).fetchall()
            valid_p_tracks = [t["track_filename"] for t in t_rows if os.path.exists(os.path.join(MUSIC_DIR, t["track_filename"]))]
            if valid_p_tracks:
                first_song = valid_p_tracks[0]
                target_t = {"filename": first_song, "title": os.path.splitext(first_song)[0], "url": f"/music/{urllib.parse.quote(first_song)}"}
                return get_track_payload(target_t, "play_playlist", f"Playing {p_match['name']} playlist starting with ")

    # Fuzzy match track titles
    matched = None
    for t in available_tracks:
        if q in t["title"].lower() or t["title"].lower() in q:
            matched = t
            break

    if not matched:
        matched = available_tracks[0]
        return get_track_payload(matched, "play", f"Could not find an exact match for {query}. ")

    return get_track_payload(matched, "play")

# 15. POST ServerAI Chat Endpoint (Server-Side High-Speed Inference)
@app.post("/api/v1/ai/chat", summary="Server-Side ServerAI Chat with Telemetry, Task & Music Awareness")
async def ai_chat(req: ChatRequest, request: Request):
    global ACTIVE_GEMINI_MODEL
    require_dashboard_session(request)
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(f"ai:{client_ip}", limit=20)
    api_key = os.getenv("AI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")

    telemetry_context = ""
    todo_context = ""
    music_context = ""
    if req.include_telemetry:
        try:
            with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                logs = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT 6").fetchall()
                if logs:
                    telemetry_context = "\n[Current Live Telemetry in Database]:\n" + "\n".join(
                        [f"- {r['device_id']} [{r['category']}]: {r['payload_data']}" for r in logs]
                    )

                pending_todos = conn.execute("SELECT text, priority FROM todos WHERE completed = 0 ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END").fetchall()
                if pending_todos:
                    todo_context = "\n[Current User To-Do List & Pending Tasks]:\n" + "\n".join(
                        [f"- {t['text']} (Priority: {t['priority']})" for t in pending_todos]
                    )
                else:
                    todo_context = "\n[Current User To-Do List]: No pending tasks."

            # Music files context
            if os.path.exists(MUSIC_DIR):
                m_files = [os.path.splitext(f)[0] for f in sorted(os.listdir(MUSIC_DIR)) if os.path.splitext(f)[1].lower() in ALLOWED_AUDIO_EXTENSIONS]
                if m_files:
                    music_context = f"\n[Available Music Library on Server]: {', '.join(m_files[:10])}"
        except Exception:
            pass

    system_instruction = (
        "You are SensorsHub ServerAI for Mohammed's smart server and XiaoZhi ESP32 Voice Assistant. "
        "Answer naturally, informatively, and concisely in 1-3 sentences in English. Refer to live telemetry logs, to-do items, and music library when asked."
    )

    if not api_key:
        return {
            "status": "warning",
            "reply": "⚠️ Server AI Key not set in `.env`. Please add `GEMINI_API_KEY=\"...\"` to `/home/m7med_am/my-homepage/.env`.",
        }

    is_gemini = (
        api_key.startswith("AIza")
        or api_key.startswith("AQ.")
        or os.getenv("GEMINI_API_KEY") is not None
        or (os.getenv("AI_API_KEY") and len(api_key) in (39, 52, 53))
    )

    if is_gemini:
        payload = {
            "contents": [{
                "parts": [{
                    "text": f"{system_instruction}\n{telemetry_context}\n{todo_context}\n\nUser Question: {req.message}"
                }]
            }],
            "generationConfig": {
                "temperature": 0.5,
                "maxOutputTokens": 400
            }
        }
        req_data = json.dumps(payload).encode("utf-8")

        def run_gemini(model_name: str):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            req_obj = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req_obj, timeout=15) as response:
                res_body = json.loads(response.read().decode("utf-8"))
                if "candidates" in res_body and res_body["candidates"]:
                    cand = res_body["candidates"][0]
                    if "content" in cand and "parts" in cand["content"]:
                        real_parts = [p.get("text", "") for p in cand["content"]["parts"] if "text" in p and not p.get("thought", False)]
                        if not real_parts:
                            real_parts = [p.get("text", "") for p in cand["content"]["parts"] if "text" in p]
                        full_text = "".join(real_parts).strip()
                        if full_text:
                            return full_text
                if "error" in res_body:
                    raise Exception(res_body["error"].get("message", "Unknown error"))
            return None

        # Try active cached model first
        if ACTIVE_GEMINI_MODEL:
            try:
                ans = await asyncio.to_thread(run_gemini, ACTIVE_GEMINI_MODEL)
                if ans:
                    return {"status": "success", "reply": ans, "model": ACTIVE_GEMINI_MODEL}
            except Exception:
                ACTIVE_GEMINI_MODEL = None

        candidate_models = [
            "gemini-2.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-2.5-flash",
            "gemini-flash-latest",
            "gemini-2.5-pro",
        ]

        last_err = ""
        for m in candidate_models:
            try:
                ans = await asyncio.to_thread(run_gemini, m)
                if ans:
                    ACTIVE_GEMINI_MODEL = m
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [AI SUCCESS] Model: {m} | Question: {req.message[:30]}")
                    return {"status": "success", "reply": ans, "model": m}
            except urllib.error.HTTPError as he:
                last_err = he.read().decode("utf-8")
                if he.code in (400, 404):
                    continue
                return {"status": "error", "reply": f"Gemini API Error ({he.code}): {last_err}"}
            except Exception as e:
                last_err = str(e)
                continue

        return {"status": "error", "reply": f"Gemini Gateway Error: {last_err}"}

    # OpenAI / Groq Compatible Fallback
    openai_url = "https://api.groq.com/openai/v1/chat/completions" if (os.getenv("GROQ_API_KEY") or api_key.startswith("gsk_")) else "https://api.openai.com/v1/chat/completions"
    model = "llama-3.1-8b-instant" if (os.getenv("GROQ_API_KEY") or api_key.startswith("gsk_")) else "gpt-4o-mini"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": f"{system_instruction}\n{telemetry_context}\n{todo_context}"},
            {"role": "user", "content": req.message}
        ]
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req_obj = urllib.request.Request(
            openai_url,
            data=req_data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST"
        )
        with urllib.request.urlopen(req_obj, timeout=15) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            reply = res_body["choices"][0]["message"]["content"]
            return {"status": "success", "reply": reply.strip()}
    except Exception as e:
        return {"status": "error", "reply": f"AI Service Error: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"Starting SensorsHub & ESP32 Gateway on 0.0.0.0:{port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
