# server.py — Production ESP32 AI Voice Assistant Backend & AI Copilot Gateway
import asyncio
import json
import os
import secrets
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Set, Dict, Any

from fastapi import FastAPI, Header, HTTPException, Request, Query, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
    description="High-performance telemetry ingestion, real-time SSE broadcasting, SQLite WAL persistence, and Server-Side AI Copilot.",
    version="2.0.0",
)

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

init_db()

# 4. Incoming Payload Validation Schemas
class DevicePayload(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.:@ ]+$", description="Unique Device ID or MAC Address", example="mo-project-c3")
    category: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$", description="Payload category (user_request, telemetry, alert, general)", example="user_request")
    data: str = Field(..., min_length=1, max_length=4096, description="Voice transcript, sensor readings, or JSON payload", example="Turn on air conditioning")
    timestamp: Optional[int] = Field(None, description="Optional Unix timestamp from device")

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="User question or prompt", example="What is the current temperature?")
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
    print(f"       🎙️ Payload Data: {payload.data}")

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

# 11. POST ServerAI Chat Endpoint (Server-Side High-Speed Inference)
@app.post("/api/v1/ai/chat", summary="Server-Side ServerAI Chat with Telemetry Awareness")
async def ai_chat(req: ChatRequest, request: Request):
    global ACTIVE_GEMINI_MODEL
    require_dashboard_session(request)
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(f"ai:{client_ip}", limit=20)
    api_key = os.getenv("AI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")

    telemetry_context = ""
    if req.include_telemetry:
        try:
            with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                logs = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT 6").fetchall()
                if logs:
                    telemetry_context = "\n[Current Live Telemetry in Database]:\n" + "\n".join(
                        [f"- {r['device_id']} [{r['category']}]: {r['payload_data']}" for r in logs]
                    )
        except Exception:
            pass

    system_instruction = (
        "You are SensorsHub ServerAI for Mohammed's smart server and XiaoZhi ESP32 Voice Assistant. "
        "Answer naturally, informatively, and concisely in 1-3 sentences in English. Refer to live telemetry logs when relevant."
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
                    "text": f"{system_instruction}\n{telemetry_context}\n\nUser Question: {req.message}"
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
            {"role": "system", "content": f"{system_instruction}\n{telemetry_context}"},
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
