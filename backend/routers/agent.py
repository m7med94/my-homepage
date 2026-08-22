# backend/routers/agent.py — ServerAI Chat, Universal Agent Dispatcher & Dynamic Plugins
import asyncio
import importlib.util
import json
import os
import re
import sqlite3
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query, status
from pydantic import BaseModel, Field

from backend.config import (
    DB_PATH,
    PLUGINS_DIR,
    ACTIVE_GEMINI_MODEL,
    is_plugin_enabled,
    require_dashboard_session,
    enforce_rate_limit,
)
from backend.events import subscribers
from collections import deque

router = APIRouter(tags=["Agent & ServerAI"])

# In-memory queue of outbound messages queued for ESP32 hardware client polling
_pending_esp32_messages = deque(maxlen=50)

def queue_message_for_esp32(text: str, device_id: str = "mo-project-c3", audio_url: Optional[str] = None):
    """Enqueues a message for the ESP32 bot to poll, display, and read aloud."""
    _pending_esp32_messages.append({
        "id": str(uuid.uuid4()),
        "device_id": device_id,
        "text": text,
        "audio_url": audio_url,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="User question or prompt")
    include_telemetry: Optional[bool] = Field(True, description="Whether to include live sensor context")

class AgentDispatchRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=4000, description="User instruction, question, task, or command")
    device_id: Optional[str] = Field("mo-project-c3", max_length=128)
    context: Optional[str] = Field("general", max_length=64)

# =========================================================================================
# DYNAMIC SERVER-SIDE AGENT PLUGINS
# =========================================================================================
# 🤖 [FOR FUTURE AGENTS & DEVELOPERS — HOW TO ADD NEW VOICE TOOLS / CAPABILITIES]:
#
# DO NOT code or re-flash the ESP32! The ESP32 is a thin client that routes all instructions here.
#
# To add a new tool or voice capability:
#   OPTION A (Preferred - Modular Plugin):
#     Create a new Python file in the `plugins/` directory (e.g. `plugins/weather.py`, `plugins/home_assistant.py`).
#     Define:
#       def handle_intent(instruction: str, context: str = "") -> Optional[str]:
#           if "your keyword or trigger" in instruction.lower():
#               # Execute your custom logic, database query, or API call
#               return "Text response that the ESP32 will speak to the user"
#           return None
#
#   OPTION B (Direct Handler):
#     Add a new intent branch inside `dispatch_agent_instruction()` below.
# =========================================================================================

def execute_server_plugins(instruction: str, context: str = "") -> Optional[tuple[str, str]]:
    """Dynamically discovers and executes enabled Python plugins placed in the plugins/ folder."""
    if not os.path.exists(PLUGINS_DIR):
        return None
    for fname in sorted(os.listdir(PLUGINS_DIR)):
        if fname.endswith(".py") and not fname.startswith("__") and not fname.startswith("."):
            # Check plugin whitelist configuration
            if not is_plugin_enabled(fname):
                continue

            fpath = os.path.join(PLUGINS_DIR, fname)
            try:
                mod_name = f"plugin_{os.path.splitext(fname)[0]}"
                spec = importlib.util.spec_from_file_location(mod_name, fpath)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "handle_intent") and callable(mod.handle_intent):
                        res = mod.handle_intent(instruction, context)
                        if res and isinstance(res, str):
                            print(f"[Agent Plugin] Executed '{fname}' for instruction: {instruction[:40]}")
                            return res.strip(), fname
            except Exception as e:
                print(f"[Agent Plugin Error] Error executing '{fname}': {e}")
    return None

async def ai_chat_core(req: ChatRequest, client_ip: str = "internal") -> dict:
    """Core Gemini AI chat logic with telemetry & task context."""
    global ACTIVE_GEMINI_MODEL
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("AI_API_KEY")

    telemetry_context = ""
    todo_context = ""
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
        except Exception:
            pass

    system_instruction = (
        "You are Sora, Mohammed's personal intelligent Server AI Agent and core brain for SensorsHub and the XiaoZhi ESP32 Voice Assistant (mo-project-c3). "
        "You execute server tasks, query real-time sensor telemetry, manage to-do tasks, check system diagnostics, and push notifications to esp32-2. "
        "Answer naturally, warmly, and concisely in 1-3 sentences in English as Sora."
    )

    if not api_key:
        return {
            "status": "warning",
            "reply": "⚠️ Gemini API Key not configured in environment. Please add GEMINI_API_KEY to your server configuration.",
        }

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

@router.post("/api/v1/ai/chat", summary="Server-Side ServerAI Chat with Telemetry, Task & Music Awareness")
async def ai_chat(req: ChatRequest, request: Request):
    """High-speed server-side AI chat powered exclusively by Google Gemini."""
    require_dashboard_session(request)
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(f"ai:{client_ip}", limit=20)
    return await ai_chat_core(req, client_ip=client_ip)

async def process_agent_instruction_core(instruction: str, device_id: str = "mo-project-c3", context: str = "general", client_ip: str = "internal", request_obj: Optional[Request] = None) -> dict:
    """
    Core server-side agent execution logic.
    Processes tasks, to-dos, telemetry queries, and general AI reasoning.
    Logs every dispatch event for full transparency and telemetry auditing.
    """
    import time
    start_time = time.time()
    log_id = str(uuid.uuid4())
    inst = instruction.strip()
    lower_inst = inst.lower()

    def record_agent_log(action: str, reply: str, plugin_name: Optional[str] = None, extra_data: Optional[dict] = None, status_str: str = "success"):
        duration_ms = round((time.time() - start_time) * 1000, 2)
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
                conn.execute(
                    "INSERT INTO agent_dispatch_logs (id, device_id, instruction, action, reply, plugin_name, latency_ms, client_ip, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (log_id, device_id or "mo-project-c3", inst, action, reply, plugin_name, duration_ms, client_ip, now_iso)
                )
        except Exception as e:
            print(f"[Agent Log DB Error] {e}")

        # Broadcast SSE notification to real-time dashboards
        log_event = {
            "type": "agent_dispatch_log",
            "id": log_id,
            "device_id": device_id or "mo-project-c3",
            "instruction": inst,
            "action": action,
            "reply": reply,
            "plugin_name": plugin_name,
            "latency_ms": duration_ms,
            "client_ip": client_ip,
            "timestamp": now_iso,
        }
        event_str = json.dumps(log_event)
        for q in list(subscribers):
            try:
                q.put_nowait(event_str)
            except Exception:
                subscribers.discard(q)

        res_payload = {
            "status": status_str,
            "action": action,
            "reply": reply,
            "summary": reply,
            "log_id": log_id,
            "latency_ms": duration_ms
        }
        if extra_data:
            res_payload["data"] = extra_data
        return res_payload

    # 1. Check custom server plugins first
    plugin_res = execute_server_plugins(inst, context or "")
    if plugin_res:
        plugin_result, plugin_name = plugin_res
        return record_agent_log(action="plugin_execution", reply=plugin_result, plugin_name=plugin_name)

    # 2. To-Do & Task Management Intents (Order: Delete -> Complete -> Add -> List)

    # A) Delete / Remove task
    if any(k in lower_inst for k in ["delete all", "clear all tasks", "clear my todo", "clear to-do", "remove all tasks", "delete all items"]):
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            cur = conn.execute("DELETE FROM todos")
            del_count = cur.rowcount
            now_iso = datetime.now(timezone.utc).isoformat()
            notification = {"type": "todo_deleted", "all": True, "timestamp": now_iso}
            for q in list(subscribers):
                try: q.put_nowait(json.dumps(notification))
                except Exception: subscribers.discard(q)
            reply_msg = f"Cleared all {del_count} items from your to-do list."
            return record_agent_log(action="todo_delete", reply=reply_msg)

    if lower_inst.startswith("delete ") or lower_inst.startswith("remove ") or "delete from" in lower_inst or "remove from" in lower_inst or "delete task" in lower_inst or "remove task" in lower_inst:
        task_query = re.sub(r"^(?:delete|remove)\s+(?:the\s+|a\s+)?(?:to-?do\s+list\s+item:?|to-?do\s+item:?|task:?|item:?|to-?do:?|reminder:?)?\s*(?:named|called)?\s*:?\s*", "", lower_inst, flags=re.IGNORECASE).strip()
        task_query = re.sub(r"\s+from\s+(?:my\s+)?(?:to-?do\s+list|tasks|list)\b.*", "", task_query, flags=re.IGNORECASE).strip()
        task_query = re.sub(r"^(?:the\s+|a\s+)?(?:to-?do\s+list\s+item:?|to-?do\s+item:?|task:?|item:?)\s*", "", task_query, flags=re.IGNORECASE).strip()
        task_query = task_query.strip(" \"':“”’‘")
        
        if task_query:
            with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT id, text FROM todos WHERE text LIKE ? ORDER BY created_at DESC LIMIT 1", (f"%{task_query}%",)).fetchone()
                if row:
                    conn.execute("DELETE FROM todos WHERE id = ?", (row["id"],))
                    now_iso = datetime.now(timezone.utc).isoformat()
                    notification = {"type": "todo_deleted", "id": row["id"], "text": row["text"], "timestamp": now_iso}
                    for q in list(subscribers):
                        try: q.put_nowait(json.dumps(notification))
                        except Exception: subscribers.discard(q)
                    reply_msg = f"Deleted '{row['text']}' from your to-do list."
                    return record_agent_log(
                        action="todo_delete",
                        reply=reply_msg,
                        extra_data={"id": row["id"], "text": row["text"]}
                    )
                else:
                    reply_msg = f"Could not find any task matching '{task_query}' to delete."
                    return record_agent_log(action="todo_delete", reply=reply_msg, status_str="warning")

    # B) Complete / Mark task done
    if lower_inst.startswith("complete") or lower_inst.startswith("finish") or lower_inst.startswith("mark ") or " as done" in lower_inst or " done" in lower_inst:
        task_query = re.sub(r"^(?:complete|finish|mark)\s+(?:the\s+|a\s+)?(?:task|item|reminder|to-?do)?\s*", "", lower_inst, flags=re.IGNORECASE).strip()
        task_query = re.sub(r"\s+(?:as\s+)?(?:completed|done|finished)\b.*", "", task_query, flags=re.IGNORECASE).strip()
        task_query = re.sub(r"^(?:the\s+|a\s+)?(?:task|item|reminder|to-?do)\s*", "", task_query, flags=re.IGNORECASE).strip()
        task_query = task_query.strip(" \"':“”’‘")

        if task_query:
            with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT id, text FROM todos WHERE completed = 0 AND text LIKE ? LIMIT 1", (f"%{task_query}%",)).fetchone()
                if row:
                    conn.execute("UPDATE todos SET completed = 1, updated_at = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), row["id"]))
                    notification = {"type": "todo_updated", "id": row["id"], "timestamp": datetime.now(timezone.utc).isoformat()}
                    for q in list(subscribers):
                        try: q.put_nowait(json.dumps(notification))
                        except Exception: subscribers.discard(q)
                    reply_msg = f"Marked task '{row['text']}' as completed."
                    return record_agent_log(action="todo_complete", reply=reply_msg)
                else:
                    reply_msg = f"Could not find any pending task matching '{task_query}' to complete."
                    return record_agent_log(action="todo_complete", reply=reply_msg, status_str="warning")

    # C) Add task
    add_match = re.search(r"(?:add\s+(?:task|to-?do|item|reminder)?\s*:?\s*|remind\s+me\s+to\s+)(.+)", lower_inst)
    if add_match or lower_inst.startswith("add ") or "add to my todo" in lower_inst or "add to my list" in lower_inst:
        task_text = add_match.group(1).strip() if add_match else inst
        task_text = re.sub(r"\s+to\s+(?:my\s+)?(?:to-?do\s+list|tasks|list)\b.*", "", task_text, flags=re.IGNORECASE).strip()
        priority = "high" if "high priority" in lower_inst or "urgent" in lower_inst or "important" in lower_inst else "normal"
        task_text = re.sub(r"\s+with\s+(?:high|normal|routine)\s+priority.*", "", task_text, flags=re.IGNORECASE).strip()
        task_text = re.sub(r"^(?:the\s+|a\s+)?(?:task|item|reminder|to-?do)\s*", "", task_text, flags=re.IGNORECASE).strip()
        task_text = task_text.strip(" \"':“”’‘")
        if not task_text:
            task_text = inst

        todo_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.execute(
                "INSERT INTO todos (id, text, priority, completed, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
                (todo_id, task_text, priority, now_iso, now_iso),
            )
        notification = {"type": "todo_created", "id": todo_id, "text": task_text, "priority": priority, "timestamp": now_iso}
        for q in list(subscribers):
            try: q.put_nowait(json.dumps(notification))
            except Exception: subscribers.discard(q)

        reply_msg = f"Added '{task_text}' to your to-do list with {priority} priority."
        return record_agent_log(
            action="todo_add",
            reply=reply_msg,
            extra_data={"id": todo_id, "text": task_text, "priority": priority}
        )

    # D) Get / List tasks
    if any(lower_inst.startswith(k) for k in ["what is my", "what's my", "what are my", "what do i have", "list my", "list todo", "list tasks", "show my", "show todo", "show tasks", "get my", "get todo", "get tasks", "check tasks", "my tasks", "my todo"]):
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT text, priority FROM todos WHERE completed = 0 ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC").fetchall()
        if rows:
            items_text = ", ".join([f"{i+1}. {r['text']}" for i, r in enumerate(rows)])
            reply_msg = f"You have {len(rows)} pending task{'s' if len(rows) > 1 else ''}: {items_text}."
        else:
            reply_msg = "You have no pending tasks on your to-do list."
        return record_agent_log(
            action="todo_list",
            reply=reply_msg,
            extra_data={"count": len(rows), "items": [dict(r) for r in rows]}
        )

    # 3. Sensor & Telemetry Queries
    if any(k in lower_inst for k in ["sensor data", "temperature", "humidity", "telemetry", "device status", "sensor status", "battery status"]):
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            reply_msg = f"Latest telemetry from {row['device_id']}: {row['category']} is {row['payload_data']}."
        else:
            reply_msg = "No recent sensor telemetry records found in the database."
        return record_agent_log(action="telemetry_query", reply=reply_msg)

    # 4. General AI Inference (Gemini / AI Model with full telemetry & task context)
    ai_resp = await ai_chat_core(ChatRequest(message=inst, include_telemetry=True), client_ip=client_ip)
    reply_text = ai_resp.get("reply", "I processed your request.")
    return record_agent_log(action="ai_inference", reply=reply_text)

@router.post("/api/v1/agent/dispatch", summary="Universal Server-Side Agent Dispatcher")
async def dispatch_agent_instruction(req: AgentDispatchRequest, request: Request):
    """
    Central server-side agent hub for ESP32 and web clients.
    Processes tasks, to-dos, telemetry queries, and general AI reasoning.
    Logs every dispatch event for full transparency and telemetry auditing.
    """
    client_ip = request.client.host if request and request.client else "unknown"
    enforce_rate_limit(f"agent:{client_ip}", limit=60)
    return await process_agent_instruction_core(
        instruction=req.instruction,
        device_id=req.device_id or "mo-project-c3",
        context=req.context or "general",
        client_ip=client_ip,
        request_obj=request
    )

# =========================================================================================
# AGENT LOGGING & AUDIT ENDPOINTS
# =========================================================================================

@router.get("/api/v1/agent/logs", summary="Query Server Agent Dispatch & Execution Logs")
def get_agent_logs(
    request: Request,
    device_id: Optional[str] = Query(None, description="Filter by device ID"),
    action: Optional[str] = Query(None, description="Filter by action"),
    limit: int = Query(50, ge=1, le=500, description="Number of log records"),
):
    """Query recent Server Agent execution history, latency, and actions."""
    require_dashboard_session(request)
    query = "SELECT id, device_id, instruction, action, reply, plugin_name, latency_ms, client_ip, created_at FROM agent_dispatch_logs WHERE 1=1"
    params = []
    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    if action:
        query += " AND action = ?"
        params.append(action)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    logs = [dict(r) for r in rows]
    return {
        "status": "success",
        "count": len(logs),
        "logs": logs
    }

@router.delete("/api/v1/agent/logs", summary="Clear Server Agent Dispatch Logs")
def clear_agent_logs(request: Request):
    """Clears all Server Agent dispatch log records."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        res = conn.execute("DELETE FROM agent_dispatch_logs")
        deleted_count = res.rowcount
    return {
        "status": "success",
        "message": f"Successfully deleted {deleted_count} agent log record(s)",
        "deleted_count": deleted_count
    }

@router.delete("/api/v1/agent/logs/{log_id}", summary="Delete Single Agent Log Record")
def delete_single_agent_log(log_id: str, request: Request):
    """Deletes a single Server Agent log record by ID."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        res = conn.execute("DELETE FROM agent_dispatch_logs WHERE id = ?", (log_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Agent log record not found")
    return {
        "status": "success",
        "message": "Agent log record deleted successfully",
        "log_id": log_id
    }

# =========================================================================================
# HARDWARE OUTBOUND MESSAGE DISPATCH
# =========================================================================================

@router.get("/api/v1/agent/messages/pending", summary="Fetch Pending Messages for ESP32 Hardware")
def get_pending_esp32_message(device_id: str = Query("mo-project-c3")):
    """Pops the next pending message queued for the ESP32 bot to speak or display."""
    if _pending_esp32_messages:
        for _ in range(len(_pending_esp32_messages)):
            msg = _pending_esp32_messages.popleft()
            if not msg.get("device_id") or msg.get("device_id") == device_id or device_id == "all":
                return {
                    "status": "success",
                    "has_message": True,
                    "id": msg["id"],
                    "message": msg["text"],
                    "audio_url": msg.get("audio_url"),
                    "created_at": msg["created_at"],
                }
            else:
                _pending_esp32_messages.append(msg)
                
    return {
        "status": "success",
        "has_message": False,
        "message": None,
    }

