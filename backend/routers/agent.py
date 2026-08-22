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
    session_id: Optional[str] = Field("default", max_length=128, description="Session identifier for multi-turn conversational memory")

class AgentDispatchRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=4000, description="User instruction, question, task, or command")
    device_id: Optional[str] = Field("mo-project-c3", max_length=128)
    context: Optional[str] = Field("general", max_length=64)

class SoraMemoryRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=128, description="Short memory identifier or topic")
    fact: str = Field(..., min_length=1, max_length=2000, description="Fact, rule, or preference to remember")
    category: Optional[str] = Field("general", max_length=64, description="Memory category: user_profile, preference, hardware, note")
    importance: Optional[int] = Field(3, ge=1, le=5, description="Importance ranking from 1 to 5")

# =========================================================================================
# SORA PERSISTENT MEMORY & CONVERSATION CONTEXT ENGINE
# =========================================================================================

def get_sora_memory_context() -> str:
    """Retrieves all stored long-term memories for prompt injection."""
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT category, key, fact FROM sora_memory ORDER BY importance DESC, created_at ASC").fetchall()
        if not rows:
            return ""
        return "\n[Sora's Long-Term Memory & User Facts]:\n" + "\n".join(
            [f"- [{r['category']}] {r['fact']}" for r in rows]
        )
    except Exception as e:
        print(f"[Sora Memory] Error fetching context: {e}")
        return ""

def get_recent_chat_turns_context(session_id: str = "default", limit: int = 6) -> str:
    """Retrieves recent conversation turns for conversational continuity."""
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, message FROM sora_chat_turns WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        if not rows:
            return ""
        turns = list(reversed(rows))
        return "\n[Recent Conversation Turns with Mohammed]:\n" + "\n".join(
            [f"{'User' if r['role'] == 'user' else 'Sora'}: {r['message']}" for r in turns]
        )
    except Exception:
        return ""

def record_chat_turn(session_id: str, role: str, message: str):
    """Records a user or Sora turn into conversation history."""
    try:
        turn_id = str(uuid.uuid4())
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.execute(
                "INSERT INTO sora_chat_turns (id, session_id, role, message) VALUES (?, ?, ?, ?)",
                (turn_id, session_id, role, message)
            )
    except Exception:
        pass

def save_sora_memory(key: str, fact: str, category: str = "general", importance: int = 3) -> dict:
    """Saves or updates a memory fact in Sora's long-term memory."""
    mem_id = f"mem_{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute("SELECT id FROM sora_memory WHERE key = ? OR fact = ?", (key, fact)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE sora_memory SET fact = ?, category = ?, importance = ?, updated_at = ? WHERE id = ?",
                    (fact, category, importance, now_iso, existing["id"])
                )
                mem_id = existing["id"]
            else:
                conn.execute(
                    "INSERT INTO sora_memory (id, category, key, fact, importance, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (mem_id, category, key, fact, importance, now_iso, now_iso)
                )
        return {"status": "success", "id": mem_id, "key": key, "fact": fact}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def forget_sora_memory(query: str) -> dict:
    """Deletes matching memory facts."""
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            res = conn.execute("DELETE FROM sora_memory WHERE id = ? OR key LIKE ? OR fact LIKE ?", (query, f"%{query}%", f"%{query}%"))
            return {"status": "success", "deleted": res.rowcount}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def list_sora_memories() -> list:
    """Returns all stored memories in Sora's brain."""
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, category, key, fact, importance, created_at, updated_at FROM sora_memory ORDER BY importance DESC, created_at DESC").fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []

# =========================================================================
# DYNAMIC SERVER-SIDE AGENT PLUGINS
# =========================================================================

def execute_server_plugins(instruction: str, context: str = "") -> Optional[tuple[str, str]]:
    """Dynamically discovers and executes enabled Python plugins placed in the plugins/ folder."""
    if not os.path.exists(PLUGINS_DIR):
        return None
    for fname in sorted(os.listdir(PLUGINS_DIR)):
        if fname.endswith(".py") and not fname.startswith("__") and not fname.startswith("."):
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

async def ai_chat_core(req: ChatRequest, client_ip: str = "internal", session_id: str = "default") -> dict:
    """Core Gemini AI chat logic with Sora long-term memory, conversation history & telemetry context."""
    global ACTIVE_GEMINI_MODEL
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("AI_API_KEY")

    sess_id = req.session_id or session_id or "default"
    record_chat_turn(sess_id, "user", req.message)

    memory_context = get_sora_memory_context()
    recent_history = get_recent_chat_turns_context(sess_id, limit=6)

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
        "You execute server tasks, query real-time sensor telemetry, manage to-do tasks, recall learned memories, check system diagnostics, and push notifications to esp32-2. "
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
                "text": f"{system_instruction}\n{memory_context}\n{telemetry_context}\n{todo_context}\n{recent_history}\n\nUser Question: {req.message}"
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

    # 2. Sora Long-Term Memory Intents (Remember -> Recall -> Forget)
    # A) Remember new fact
    rem_match = re.search(r"(?:remember\s+that\s+|remember\s*:?\s*|learn\s+that\s+|save\s+memory\s*:?\s*)(.+)", lower_inst)
    if rem_match or lower_inst.startswith("remember ") or lower_inst.startswith("sora, remember ") or lower_inst.startswith("sora remember "):
        fact_text = rem_match.group(1).strip() if rem_match else inst
        fact_text = re.sub(r"^sora,?\s*", "", fact_text, flags=re.IGNORECASE).strip()
        fact_text = re.sub(r"^(?:please\s+)?remember\s+(?:that\s+)?", "", fact_text, flags=re.IGNORECASE).strip()
        fact_text = fact_text.strip(" \"':“”’‘")
        if fact_text:
            key_name = re.sub(r"[^a-zA-Z0-9_]", "_", fact_text[:24]).strip("_").lower() or "user_note"
            save_sora_memory(key=key_name, fact=fact_text, category="user_note", importance=4)
            reply_msg = f"I've committed that to my long-term memory, Mohammed: '{fact_text}'."
            record_chat_turn(device_id or "default", "model", reply_msg)
            return record_agent_log(action="memory_save", reply=reply_msg, extra_data={"fact": fact_text})

    # B) Recall / List memories
    if any(k in lower_inst for k in ["what do you remember", "what is in your memory", "list your memory", "show your memory", "recall memory", "what do you know about me", "tell me what you remember"]):
        mems = list_sora_memories()
        if mems:
            facts = [m["fact"] for m in mems[:5]]
            reply_msg = f"Here is what I remember in my core memory, Mohammed: " + "; ".join(facts) + "."
        else:
            reply_msg = "My long-term memory is currently fresh. Tell me 'Sora, remember that...' to teach me facts!"
        record_chat_turn(device_id or "default", "model", reply_msg)
        return record_agent_log(action="memory_recall", reply=reply_msg, extra_data={"count": len(mems), "memories": mems})

    # C) Forget memory
    forg_match = re.search(r"(?:forget\s+that\s+|forget\s+:?\s*|delete\s+memory\s*:?\s*)(.+)", lower_inst)
    if forg_match or lower_inst.startswith("forget "):
        target_fact = forg_match.group(1).strip() if forg_match else inst
        target_fact = re.sub(r"^sora,?\s*", "", target_fact, flags=re.IGNORECASE).strip()
        target_fact = re.sub(r"^(?:please\s+)?forget\s+(?:that\s+)?", "", target_fact, flags=re.IGNORECASE).strip()
        res = forget_sora_memory(target_fact)
        if res.get("deleted", 0) > 0:
            reply_msg = f"I have deleted '{target_fact}' from my memory."
        else:
            reply_msg = f"I couldn't find any stored memory matching '{target_fact}'."
        record_chat_turn(device_id or "default", "model", reply_msg)
        return record_agent_log(action="memory_forget", reply=reply_msg)

    # 3. Hardware Notification & Alert Intents (Push directly to esp32-2)
    notify_triggers = [
        "send notification", "push notification", "send alert", "push alert",
        "send a notification", "push an alert", "send an alert", "push a notification",
        "notify esp32", "alert esp32", "notify device", "alert device",
        "display on esp32", "show on esp32", "message esp32", "tell esp32",
        "notify me on esp32", "send to esp32", "send alert to esp32", "send notification to esp32"
    ]
    if any(k in lower_inst for k in notify_triggers):
        clean_msg = re.sub(r"^sora,?\s*", "", inst, flags=re.IGNORECASE).strip()
        clean_msg = re.sub(
            r"^(?:please\s+)?(?:send|push|transmit|dispatch|post)\s+(?:a\s+|an\s+)?(?:notification|alert|message)?\s*(?:to\s+(?:the\s+)?(?:esp32(?:-2)?|device|display|screen|receiver|hardware))?\s*:?\s*(?:saying|that|with)?\s*:?\s*",
            "",
            clean_msg,
            flags=re.IGNORECASE
        ).strip()
        clean_msg = clean_msg.strip(" \"':“”’‘")
        if not clean_msg:
            clean_msg = "Notice from Sora"

        # Determine emotion tone
        emotion = "happy"
        if any(w in lower_inst for w in ["warning", "urgent", "danger", "alert", "error", "critical"]):
            emotion = "warning"
        elif any(w in lower_inst for w in ["question", "confused", "what", "why"]):
            emotion = "confused"
        elif any(w in lower_inst for w in ["notice", "info", "update", "reminder"]):
            emotion = "notice"

        from backend.routers.telemetry import push_message_to_device
        pushed = await push_message_to_device(
            device_id="esp32-2",
            message=clean_msg,
            status="Sora Notice",
            emotion=emotion,
        )
        reply_msg = f"I've transmitted the alert to your esp32-2 notification receiver: '{clean_msg}'."
        record_chat_turn(device_id or "default", "model", reply_msg)
        return record_agent_log(
            action="device_notify",
            reply=reply_msg,
            extra_data={"message": clean_msg, "device_id": "esp32-2", "emotion": emotion, "pushed": pushed}
        )

    # 4. To-Do & Task Management Intents (Order: Delete -> Complete -> Add -> List)

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

    # 5. Sensor & Telemetry Queries
    if any(k in lower_inst for k in ["sensor data", "temperature", "humidity", "telemetry", "device status", "sensor status", "battery status"]):
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            reply_msg = f"Latest telemetry from {row['device_id']}: {row['category']} is {row['payload_data']}."
        else:
            reply_msg = "No recent sensor telemetry records found in the database."
        return record_agent_log(action="telemetry_query", reply=reply_msg)

    # 6. General AI Inference (Gemini / AI Model with Sora memory & multi-turn history)
    ai_resp = await ai_chat_core(ChatRequest(message=inst, include_telemetry=True, session_id=device_id or "default"), client_ip=client_ip, session_id=device_id or "default")
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

# =========================================================================================
# SORA LONG-TERM MEMORY & CONVERSATION HISTORY REST API
# =========================================================================================

@router.get("/api/v1/agent/memory", summary="Get All Stored Sora Memories")
def get_sora_memories(request: Request):
    """Returns all long-term memory facts stored in Sora's brain."""
    require_dashboard_session(request)
    memories = list_sora_memories()
    return {
        "status": "success",
        "count": len(memories),
        "memories": memories
    }

@router.post("/api/v1/agent/memory", summary="Teach Sora a New Memory Fact")
def add_sora_memory(req: SoraMemoryRequest, request: Request):
    """Teaches Sora a new permanent fact or preference."""
    require_dashboard_session(request)
    res = save_sora_memory(
        key=req.key,
        fact=req.fact,
        category=req.category or "general",
        importance=req.importance or 3
    )
    return res

@router.delete("/api/v1/agent/memory/{mem_id}", summary="Delete a Stored Memory from Sora")
def delete_sora_memory(mem_id: str, request: Request):
    """Deletes a specific memory fact from Sora's brain."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
        res = conn.execute("DELETE FROM sora_memory WHERE id = ?", (mem_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Memory record not found")
    return {"status": "success", "message": f"Deleted memory '{mem_id}'"}

@router.get("/api/v1/agent/history", summary="Get Multi-Turn Chat History")
def get_chat_history(request: Request, session_id: str = Query("default"), limit: int = Query(20, ge=1, le=100)):
    """Retrieves recent conversation turns for a specific session."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, session_id, role, message, created_at FROM sora_chat_turns WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
    return {
        "status": "success",
        "session_id": session_id,
        "count": len(rows),
        "history": [dict(r) for r in reversed(rows)]
    }

@router.delete("/api/v1/agent/history", summary="Clear Multi-Turn Chat History")
def clear_chat_history(request: Request, session_id: Optional[str] = Query(None)):
    """Clears conversation history for a session or globally."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
        if session_id:
            res = conn.execute("DELETE FROM sora_chat_turns WHERE session_id = ?", (session_id,))
        else:
            res = conn.execute("DELETE FROM sora_chat_turns")
        deleted = res.rowcount
    return {"status": "success", "message": f"Cleared {deleted} conversation turn(s)"}

