# backend/routers/telemetry.py — Device Telemetry Ingestion & Query Routes
import asyncio
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict
from fastapi import APIRouter, Header, HTTPException, Request, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config import (
    DB_PATH,
    require_bearer_token,
    require_dashboard_session,
    enforce_rate_limit,
)
from backend.events import subscribers

router = APIRouter(tags=["Telemetry & Events"])

# Active WebSocket connections from ESP32 hardware clients (device_id -> WebSocket)
connected_device_sockets: Dict[str, WebSocket] = {}

async def push_message_to_device(device_id: str, text: str, emotion: str = "happy", audio_url: Optional[str] = None) -> bool:
    """Instantly pushes a JSON command/speech packet to the connected ESP32 WebSocket client."""
    ws = connected_device_sockets.get(device_id)
    if not ws:
        # Fallback to any connected ESP32 if device_id is generic
        if connected_device_sockets:
            ws = next(iter(connected_device_sockets.values()))
    
    if ws:
        try:
            payload = {
                "action": "speak",
                "text": text,
                "emotion": emotion,
                "audio_url": audio_url,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await ws.send_text(json.dumps(payload))
            print(f"[Device WebSocket] Pushed message to {device_id}: {text}")
            return True
        except Exception as e:
            print(f"[Device WebSocket] Error pushing to {device_id}: {e}")
            connected_device_sockets.pop(device_id, None)
    return False

class DevicePayload(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.:@ ]+$", description="Unique Device ID or MAC Address")
    category: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$", description="Log or Sensor category")
    data: str = Field(..., min_length=1, max_length=4000, description="Raw or formatted telemetry payload string")

@router.get("/api/v1/events/stream", summary="Live Telemetry Event Stream")
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

@router.post("/api/v1/device/data", status_code=status.HTTP_201_CREATED, summary="Ingest Device Payload")
async def ingest_device_data(
    payload: DevicePayload,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Stores telemetry data from ESP32 into SQLite and broadcasts real-time SSE event to web dashboard."""
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

            # If device is syncing its MQTT cloud credentials
            if payload.category == "mqtt_registration":
                try:
                    meta = json.loads(payload.data)
                    conn.execute("""
                        INSERT INTO device_mqtt_credentials (device_id, endpoint, client_id, username, password, publish_topic, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(device_id) DO UPDATE SET
                            endpoint=excluded.endpoint,
                            client_id=excluded.client_id,
                            username=excluded.username,
                            password=excluded.password,
                            publish_topic=excluded.publish_topic,
                            updated_at=CURRENT_TIMESTAMP;
                    """, (
                        payload.device_id,
                        meta.get("endpoint", "mqtt.xiaozhi.me:8883"),
                        meta.get("client_id", ""),
                        meta.get("username", ""),
                        meta.get("password", ""),
                        meta.get("publish_topic", "")
                    ))
                    print(f"[MQTT Auto-Registration] Successfully registered device {payload.device_id} for direct cloud push!")
                except Exception as ex:
                    print(f"[MQTT Auto-Registration Error]: {ex}")
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

@router.get("/api/v1/device/data", summary="Query Device Records & Voice Summary")
def query_device_data(
    request: Request,
    category: Optional[str] = Query(None, description="Filter by category"),
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    limit: int = Query(1, ge=1, le=500, description="Number of recent records"),
):
    """Fetches device records with natural voice summary."""
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

@router.delete("/api/v1/device/data", summary="Clear Device Telemetry Records")
def clear_device_data(
    request: Request,
    category: Optional[str] = Query(None, description="Filter by category"),
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
):
    """Clears all or filtered telemetry logs."""
    require_dashboard_session(request)
    query = "DELETE FROM telemetry_logs WHERE 1=1"
    params = []

    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    if category:
        query += " AND category = ?"
        params.append(category)

    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        res = conn.execute(query, params)
        deleted_count = res.rowcount

    return {
        "status": "success",
        "message": f"Successfully deleted {deleted_count} record(s)",
        "deleted_count": deleted_count,
    }

@router.delete("/api/v1/device/data/{entry_id}", summary="Delete Single Device Telemetry Record")
def delete_single_device_data(entry_id: str, request: Request):
    """Deletes a single telemetry record by ID."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        res = conn.execute("DELETE FROM telemetry_logs WHERE id = ?", (entry_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Telemetry record not found")

    return {
        "status": "success",
        "message": "Record deleted successfully",
        "entry_id": entry_id,
    }

@router.get("/api/v1/device/stats", summary="Telemetry Fleet Statistics")
def get_device_stats(request: Request):
    """Aggregates fleet counts, unique devices, and latest transmission."""
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

@router.websocket("/api/v1/device/ws")
async def device_websocket_endpoint(websocket: WebSocket, device_id: str = "mo-project-c3"):
    """
    Persistent full-duplex WebSocket channel for ESP32 hardware clients.
    Enables server-to-device instant push for alerts, spoken text, and commands.
    """
    await websocket.accept()
    connected_device_sockets[device_id] = websocket
    print(f"[Device WebSocket] ESP32 '{device_id}' connected to persistent push channel.")

    # Record active state in SQLite
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute(
                "INSERT INTO telemetry_logs (id, device_id, category, payload_data, client_ip) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), device_id, "websocket_connect", "ESP32 WebSocket Client Connected", websocket.client.host if websocket.client else "unknown"),
            )
    except Exception:
        pass

    try:
        # Send initial confirmation frame
        welcome_frame = {
            "type": "welcome",
            "device_id": device_id,
            "status": "connected",
            "server_time": datetime.now(timezone.utc).isoformat()
        }
        await websocket.send_text(json.dumps(welcome_frame))

        while True:
            # Keep socket alive and receive any uplink heartbeats / messages from ESP32
            data = await websocket.receive_text()
            if data:
                try:
                    payload = json.loads(data)
                    if payload.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()}))
                except Exception:
                    pass
    except WebSocketDisconnect:
        print(f"[Device WebSocket] ESP32 '{device_id}' disconnected gracefully.")
    except Exception as e:
        print(f"[Device WebSocket] ESP32 '{device_id}' socket closed: {e}")
    finally:
        if connected_device_sockets.get(device_id) == websocket:
            connected_device_sockets.pop(device_id, None)
