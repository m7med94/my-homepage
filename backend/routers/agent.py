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

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.config import (
    DB_PATH,
    MUSIC_DIR,
    PLUGINS_DIR,
    ALLOWED_AUDIO_EXTENSIONS,
    ACTIVE_GEMINI_MODEL,
    require_dashboard_session,
    enforce_rate_limit,
)
from backend.events import subscribers
from backend.routers.music import voice_music_action

router = APIRouter(tags=["Agent & ServerAI"])

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

def execute_server_plugins(instruction: str, context: str = "") -> Optional[str]:
    """Dynamically discovers and executes any custom Python plugins placed in the plugins/ folder."""
    if not os.path.exists(PLUGINS_DIR):
        return None
    for fname in sorted(os.listdir(PLUGINS_DIR)):
        if fname.endswith(".py") and not fname.startswith("__"):
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
                            return res.strip()
            except Exception as e:
                print(f"[Agent Plugin Error] Error executing '{fname}': {e}")
    return None

@router.post("/api/v1/ai/chat", summary="Server-Side ServerAI Chat with Telemetry, Task & Music Awareness")
async def ai_chat(req: ChatRequest, request: Request):
    """High-speed server-side AI chat with Gemini/Groq/OpenAI."""
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
            "reply": "⚠️ Server AI Key not set in `.env`. Please add `GEMINI_API_KEY=\"...\"` to `.env`.",
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
                    "text": f"{system_instruction}\n{telemetry_context}\n{todo_context}\n{music_context}\n\nUser Question: {req.message}"
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

@router.post("/api/v1/agent/dispatch", summary="Universal Server-Side Agent Dispatcher")
async def dispatch_agent_instruction(req: AgentDispatchRequest, request: Request):
    """
    Central server-side agent hub for ESP32 and web clients.
    Processes tasks, to-dos, music playback, telemetry queries, and general AI reasoning.
    """
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(f"agent:{client_ip}", limit=60)
    inst = req.instruction.strip()
    lower_inst = inst.lower()

    # 1. Check custom server plugins first
    plugin_result = execute_server_plugins(inst, req.context or "")
    if plugin_result:
        return {
            "status": "success",
            "action": "plugin_execution",
            "reply": plugin_result,
            "summary": plugin_result
        }

    # 2. To-Do & Task Management Intents
    # A) Add task
    add_match = re.search(r"(?:add\s+(?:task|to-?do|item|reminder)?\s*:?\s*|remind\s+me\s+to\s+)(.+)", lower_inst)
    if add_match or lower_inst.startswith("add ") or "add to my todo" in lower_inst or "add to my list" in lower_inst:
        task_text = add_match.group(1).strip() if add_match else inst
        task_text = re.sub(r"\s+to\s+(?:my\s+)?(?:to-?do\s+list|tasks|list)\b.*", "", task_text, flags=re.IGNORECASE).strip()
        priority = "high" if "high priority" in lower_inst or "urgent" in lower_inst or "important" in lower_inst else "normal"
        task_text = re.sub(r"\s+with\s+(?:high|normal|routine)\s+priority.*", "", task_text, flags=re.IGNORECASE).strip()
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
        return {
            "status": "success",
            "action": "todo_add",
            "reply": reply_msg,
            "summary": reply_msg,
            "data": {"id": todo_id, "text": task_text, "priority": priority}
        }

    # B) Get / List tasks
    if any(k in lower_inst for k in ["what is my todo", "what are my tasks", "what do i have to do", "list my tasks", "show todos", "get todo", "my reminders", "check tasks"]):
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT text, priority FROM todos WHERE completed = 0 ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC").fetchall()
        if rows:
            items_text = ", ".join([f"{i+1}. {r['text']}" for i, r in enumerate(rows)])
            reply_msg = f"You have {len(rows)} pending task{'s' if len(rows) > 1 else ''}: {items_text}."
        else:
            reply_msg = "You have no pending tasks on your to-do list."
        return {
            "status": "success",
            "action": "todo_list",
            "reply": reply_msg,
            "summary": reply_msg,
            "data": {"count": len(rows), "items": [dict(r) for r in rows]}
        }

    # C) Complete task
    if any(lower_inst.startswith(k) for k in ["complete task", "finish task", "mark done", "mark task done", "done with"]):
        task_query = re.sub(r"^(complete\s+task|finish\s+task|mark\s+done|mark\s+task\s+done|done\s+with)\s*", "", lower_inst).strip()
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
                return {"status": "success", "action": "todo_complete", "reply": reply_msg, "summary": reply_msg}

    # D) Delete / Remove task
    if any(k in lower_inst for k in ["delete task", "remove task", "delete item", "remove item", "delete to-do", "remove to-do", "delete from my todo", "remove from my todo", "delete from todo", "remove from todo"]) or (lower_inst.startswith("delete ") and ("task" in lower_inst or "todo" in lower_inst or "item" in lower_inst or "milk" in lower_inst or "bread" in lower_inst)) or lower_inst.startswith("delete "):
        del_match = re.search(r"^(?:delete|remove)\s+(?:task|item|to-?do)?\s*(?:named|called)?\s*:?\s*(.+)", lower_inst)
        task_query = del_match.group(1).strip() if del_match else lower_inst
        task_query = re.sub(r"\s+from\s+(?:my\s+)?(?:to-?do\s+list|tasks|list)\b.*", "", task_query, flags=re.IGNORECASE).strip()
        task_query = re.sub(r"^(?:task|item|to-?do)\s*", "", task_query, flags=re.IGNORECASE).strip()
        
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
                    return {"status": "success", "action": "todo_delete", "reply": reply_msg, "summary": reply_msg, "data": {"id": row["id"], "text": row["text"]}}
                else:
                    reply_msg = f"Could not find any task matching '{task_query}' to delete."
                    return {"status": "warning", "action": "todo_delete", "reply": reply_msg, "summary": reply_msg}

    # 3. Music Vault Intents
    if any(k in lower_inst for k in ["play music", "play song", "play track", "play playlist", "put on music", "play something"]):
        query = re.sub(r"^(play\s+music|play\s+song|play\s+track|play\s+playlist|put\s+on\s+music|play\s+something)\s*", "", lower_inst).strip()
        music_res = voice_music_action(query=query if query else "random", action="play")
        return {
            "status": "success",
            "action": "music_play",
            "reply": music_res.get("tts_message", "Starting music playback."),
            "summary": music_res.get("tts_message", "Starting music playback."),
            "data": music_res
        }

    if any(k in lower_inst for k in ["what music do you have", "list music", "what songs do i have", "list playlists", "show music"]):
        music_res = voice_music_action(query="", action="list")
        return {
            "status": "success",
            "action": "music_list",
            "reply": music_res.get("tts_message", "Here is your music library."),
            "summary": music_res.get("tts_message", "Here is your music library."),
            "data": music_res
        }

    # 4. Sensor & Telemetry Queries
    if any(k in lower_inst for k in ["sensor data", "temperature", "humidity", "telemetry", "device status", "sensor status", "battery status"]):
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            reply_msg = f"Latest telemetry from {row['device_id']}: {row['category']} is {row['payload_data']}."
        else:
            reply_msg = "No recent sensor telemetry records found in the database."
        return {
            "status": "success",
            "action": "telemetry_query",
            "reply": reply_msg,
            "summary": reply_msg
        }

    # 5. General AI Inference (Gemini / AI Model with full telemetry & task context)
    ai_resp = await ai_chat(ChatRequest(message=inst, include_telemetry=True), request)
    reply_text = ai_resp.get("reply", "I processed your request.")
    return {
        "status": "success",
        "action": "ai_inference",
        "reply": reply_text,
        "summary": reply_text
    }
