# server.py — Production ESP32 AI Voice Assistant Backend Ingestion Service
import asyncio
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Set

from fastapi import FastAPI, Header, HTTPException, Request, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# 1. Initialize FastAPI Application
app = FastAPI(
    title="ESP32 AI Voice Assistant Telemetry Gateway",
    description="REST API server to ingest real-time voice, telemetry, and event payloads from ESP32 XiaoZhi devices with live SSE notifications.",
    version="1.1.0",
)

# 2. CORS Middleware Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "esp32_telemetry.db"

# In-memory list of active SSE subscriber queues for real-time notification broadcasting
subscribers: Set[asyncio.Queue] = set()

# 3. Database Initialization (SQLite with Indexed Fields)
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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

# 4. Incoming Payload Validation Schema
class DevicePayload(BaseModel):
    device_id: str = Field(..., description="Unique Device ID or MAC Address", example="mo-project-c3")
    category: str = Field(..., description="Payload category (user_request, telemetry, alert, general)", example="user_request")
    data: str = Field(..., description="Voice transcript, sensor readings, or JSON payload", example="Turn on air conditioning")
    timestamp: Optional[int] = Field(None, description="Optional Unix timestamp from device")

# 5. Health Check Endpoint
@app.get("/health", summary="Health Check")
def health_check():
    return {
        "status": "healthy",
        "service": "ESP32 Voice Telemetry Gateway",
        "active_sse_subscribers": len(subscribers),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# 6. Real-Time SSE Notification Stream (Server-Sent Events)
@app.get("/api/v1/events/stream", summary="Live Telemetry Event Stream")
async def event_stream(request: Request):
    """Server-Sent Events (SSE) stream to push instant notifications to dashboards whenever ESP32 transmits."""
    queue = asyncio.Queue()
    subscribers.add(queue)

    async def sse_generator():
        try:
            # Send initial connected message
            init_msg = json.dumps({"type": "connected", "message": "Listening for live ESP32 telemetry & voice events..."})
            yield f"data: {init_msg}\n\n"

            while True:
                # Disconnect if client leaves
                if await request.is_disconnected():
                    break
                data = await queue.get()
                yield f"data: {data}\n\n"
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

# 7. POST Ingestion Endpoint (Ingests & Broadcasts Instant Notification)
@app.post("/api/v1/device/data", status_code=status.HTTP_201_CREATED, summary="Ingest Device Payload")
async def ingest_device_data(
    payload: DevicePayload,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown"
    entry_id = str(uuid.uuid4())

    # Optional: Validate Authorization Token if provided
    if authorization and not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Invalid or missing authorization token"}
        )

    # Persist entry to SQLite
    try:
        with sqlite3.connect(DB_PATH) as conn:
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
    
    # Form Notification Event Object
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

    # Broadcast notification in real-time to all connected web dashboards / clients
    event_str = json.dumps(notification_event)
    for q in list(subscribers):
        try:
            q.put_nowait(event_str)
        except Exception:
            subscribers.discard(q)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [201 CREATED] Device:{payload.device_id} | Cat:{payload.category} | IP:{client_ip} | Latency:{duration_ms}ms")
    print(f"       🎙️ Payload Data: {payload.data}")

    return {
        "status": "success",
        "message": "Data received and processed",
        "entry_id": entry_id,
        "device_id": payload.device_id,
        "received_at": timestamp_iso,
        "broadcast_subscribers": len(subscribers),
    }

# 8. GET Query Historical Records Endpoint
@app.get("/api/v1/device/data", summary="Query Device Records")
def query_device_data(
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(50, ge=1, le=500, description="Max entries to return"),
):
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

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    return {
        "count": len(rows),
        "results": [dict(r) for r in rows],
    }

if __name__ == "__main__":
    import uvicorn
    print("Starting ESP32 Voice Assistant Backend Gateway on 0.0.0.0:8000 with Real-Time SSE Notifications...")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
