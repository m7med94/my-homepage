# server.py — Production ESP32 AI Voice Assistant Backend & AI Chat Ingestion Service
import asyncio
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Set

from fastapi import FastAPI, Header, HTTPException, Request, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# Automatic local .env loader
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip().strip('"').strip("'")

load_env()

# 1. Initialize FastAPI Application
app = FastAPI(
    title="ESP32 AI Voice Assistant Telemetry Gateway",
    description="REST API server to ingest real-time voice, telemetry, and event payloads from ESP32 XiaoZhi devices with live SSE notifications and Server-Side AI Copilot.",
    version="1.3.0",
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
subscribers: Set[asyncio.Queue] = set()
ACTIVE_GEMINI_MODEL: Optional[str] = None

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

# 4. Incoming Payload Validation Schemas
class DevicePayload(BaseModel):
    device_id: str = Field(..., description="Unique Device ID or MAC Address", example="mo-project-c3")
    category: str = Field(..., description="Payload category (user_request, telemetry, alert, general)", example="user_request")
    data: str = Field(..., description="Voice transcript, sensor readings, or JSON payload", example="Turn on air conditioning")
    timestamp: Optional[int] = Field(None, description="Optional Unix timestamp from device")

class ChatRequest(BaseModel):
    message: str = Field(..., description="User question or prompt", example="What is the current temperature?")
    include_telemetry: Optional[bool] = Field(True, description="Whether to include live sensor context")

# 5. Health Check Endpoint
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
        "cached_gemini_model": ACTIVE_GEMINI_MODEL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# 6. Real-Time SSE Notification Stream
@app.get("/api/v1/events/stream", summary="Live Telemetry Event Stream")
async def event_stream(request: Request):
    queue = asyncio.Queue()
    subscribers.add(queue)

    async def sse_generator():
        try:
            init_msg = json.dumps({"type": "connected", "message": "Listening for live ESP32 telemetry & voice events..."})
            yield f"data: {init_msg}\n\n"

            while True:
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

# 7. POST Ingestion Endpoint
@app.post("/api/v1/device/data", status_code=status.HTTP_201_CREATED, summary="Ingest Device Payload")
async def ingest_device_data(
    payload: DevicePayload,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown"
    entry_id = str(uuid.uuid4())

    if authorization and not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Invalid or missing authorization token"}
        )

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

# 8. GET Query Records Endpoint
@app.get("/api/v1/device/data", summary="Query Device Records & Voice Summary")
def query_device_data(
    category: Optional[str] = Query(None, description="Filter by category"),
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    limit: int = Query(1, ge=1, le=500, description="Number of recent records"),
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

# 9. POST AI Assistant Chat Endpoint
@app.post("/api/v1/ai/chat", summary="Server-Side AI Chat with Telemetry Awareness")
async def ai_chat(req: ChatRequest):
    global ACTIVE_GEMINI_MODEL
    api_key = os.getenv("AI_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY")

    telemetry_context = ""
    if req.include_telemetry:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                logs = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT 5").fetchall()
                if logs:
                    telemetry_context = "\n[Current Live Telemetry in Database]:\n" + "\n".join(
                        [f"- {r['device_id']} [{r['category']}]: {r['payload_data']}" for r in logs]
                    )
        except Exception:
            pass

    system_instruction = (
        "You are SensorsHub AI Copilot for Mohammed's smart server and XiaoZhi ESP32 Voice Assistant. "
        "Answer naturally, helpful, and concisely in 1-3 sentences in English. Refer to live telemetry logs when relevant."
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
        # Build standard Gemini request
        payload = {
            "contents": [{
                "parts": [{
                    "text": f"{system_instruction}\n{telemetry_context}\n\nUser Question: {req.message}"
                }]
            }],
            "generationConfig": {
                "temperature": 0.6,
                "maxOutputTokens": 450
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
                        full_text = "".join([p.get("text", "") for p in cand["content"]["parts"] if "text" in p])
                        if full_text.strip():
                            return full_text.strip()
                if "error" in res_body:
                    raise Exception(res_body["error"].get("message", "Unknown error"))
            return None

        # First, fetch available models directly from Google for this key
        discovered_models = []
        try:
            list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            list_req = urllib.request.Request(list_url, method="GET")
            with urllib.request.urlopen(list_req, timeout=10) as list_res:
                list_data = json.loads(list_res.read().decode("utf-8"))
                for m in list_data.get("models", []):
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        discovered_models.append(m["name"].replace("models/", ""))
        except urllib.error.HTTPError as he:
            err_msg = he.read().decode("utf-8")
            return {"status": "error", "reply": f"Google Gemini Key Error ({he.code}): {err_msg}"}
        except Exception as e:
            return {"status": "error", "reply": f"Connection to Google AI failed: {str(e)}"}

        candidate_models = discovered_models or ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"]
        
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

    # OpenAI / Groq fallback
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
    print("Starting ESP32 Voice Assistant Backend Gateway & Server-Side AI Copilot on 0.0.0.0:8000...")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
