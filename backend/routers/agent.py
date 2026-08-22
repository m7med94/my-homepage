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

# =========================================================================
# SORA REACT TOOL DECLARATIONS & EXECUTORS
# =========================================================================

SORA_REACT_TOOLS = [
    {
        "name": "push_esp32_notification",
        "description": "Transmit a visual alert, notification message, or reminder to the esp32-2 notification receiver screen over WebSocket.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": {
                    "type": "STRING",
                    "description": "The message text to display on the esp32-2 notification screen."
                },
                "emotion": {
                    "type": "STRING",
                    "description": "Tone/urgency: 'happy', 'notice', 'warning', 'confused'."
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "query_todos",
        "description": "Retrieve active pending tasks or all tasks from Mohammed's SQLite to-do list.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "status": {
                    "type": "STRING",
                    "description": "Filter by status: 'pending' (default) or 'all'."
                }
            }
        }
    },
    {
        "name": "create_todo",
        "description": "Add a new task or reminder to Mohammed's to-do list.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {
                    "type": "STRING",
                    "description": "The task or reminder description."
                },
                "priority": {
                    "type": "STRING",
                    "description": "Priority level: 'high', 'normal', or 'routine'."
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "update_todo",
        "description": "Mark a task as completed or delete it from the to-do list.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task_id_or_name": {
                    "type": "STRING",
                    "description": "The task ID or keyword in the task text to complete or delete."
                },
                "action": {
                    "type": "STRING",
                    "description": "Action to perform: 'complete' or 'delete'."
                }
            },
            "required": ["task_id_or_name", "action"]
        }
    },
    {
        "name": "query_telemetry",
        "description": "Fetch live sensor telemetry records (temperature, humidity, device status, battery) from the database.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device_id": {
                    "type": "STRING",
                    "description": "Optional device filter, e.g. 'esp32-2' or 'mo-project-c3'."
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Number of recent logs to fetch (1-10)."
                }
            }
        }
    },
    {
        "name": "check_server_health",
        "description": "Read live server system diagnostics including RAM usage, CPU core count, disk space, and ESP32 gateway state.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "ping_network_target",
        "description": "Perform an ICMP ping to measure latency and test connectivity to any IP or domain (e.g. 1.1.1.1, google.com).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {
                    "type": "STRING",
                    "description": "The IP address or domain to ping."
                }
            },
            "required": ["target"]
        }
    },
    {
        "name": "manage_sora_memory",
        "description": "Manage Sora's long-term memory: learn a new fact about Mohammed, recall all facts, or forget a specific fact.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "'learn' to store a fact, 'recall' to retrieve all facts, or 'forget' to delete a fact."
                },
                "fact": {
                    "type": "STRING",
                    "description": "The fact text to remember or forget."
                },
                "category": {
                    "type": "STRING",
                    "description": "Category for the memory: 'preferences', 'identity', 'schedule', 'general'."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "get_weather_report",
        "description": "Get current weather conditions and temperature for any city.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {
                    "type": "STRING",
                    "description": "City name, e.g. 'Hannover', 'Cairo', 'Berlin'."
                }
            },
            "required": ["city"]
        }
    }
]

async def execute_sora_tool(tool_name: str, args: dict) -> dict:
    """Executes a registered Sora tool and returns structured observation data."""
    try:
        if tool_name == "push_esp32_notification":
            msg = args.get("message", "Alert from Sora")
            emotion = args.get("emotion", "notice")
            from backend.routers.telemetry import push_message_to_device
            pushed = await push_message_to_device(
                device_id="esp32-2",
                message=msg,
                status="Sora Alert",
                emotion=emotion,
            )
            return {"status": "success", "pushed": pushed, "device_id": "esp32-2", "message": msg}

        elif tool_name == "query_todos":
            status = args.get("status", "pending")
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                sql = "SELECT id, text, priority, completed, created_at FROM todos"
                if status == "pending":
                    sql += " WHERE completed = 0"
                sql += " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC LIMIT 15"
                rows = conn.execute(sql).fetchall()
            return {"status": "success", "count": len(rows), "todos": [dict(r) for r in rows]}

        elif tool_name == "create_todo":
            text = args.get("text", "").strip()
            priority = args.get("priority", "normal")
            if not text:
                return {"status": "error", "message": "Task description is required."}
            new_id = str(uuid.uuid4())
            now_iso = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute("INSERT INTO todos (id, text, priority, completed, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)", (new_id, text, priority, now_iso, now_iso))
            return {"status": "success", "id": new_id, "text": text, "priority": priority}

        elif tool_name == "update_todo":
            task_id = args.get("task_id_or_name", "")
            action = args.get("action", "complete")
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                if action == "complete":
                    cur = conn.execute("UPDATE todos SET completed = 1, updated_at = ? WHERE id = ? OR text LIKE ?", (datetime.now(timezone.utc).isoformat(), task_id, f"%{task_id}%"))
                    return {"status": "success", "action": "completed", "rows_affected": cur.rowcount}
                else:
                    cur = conn.execute("DELETE FROM todos WHERE id = ? OR text LIKE ?", (task_id, f"%{task_id}%"))
                    return {"status": "success", "action": "deleted", "rows_affected": cur.rowcount}

        elif tool_name == "query_telemetry":
            dev_id = args.get("device_id")
            lim = min(max(args.get("limit", 5), 1), 10)
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                if dev_id:
                    rows = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs WHERE device_id = ? ORDER BY created_at DESC LIMIT ?", (dev_id, lim)).fetchall()
                else:
                    rows = conn.execute("SELECT device_id, category, payload_data, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT ?", (lim,)).fetchall()
            return {"status": "success", "records": [dict(r) for r in rows]}

        elif tool_name == "check_server_health":
            from backend.routers.network import get_server_memory_stats
            mem = get_server_memory_stats()
            return {
                "status": "success",
                "ram_total_gb": mem.get("total_gb"),
                "ram_used_gb": mem.get("used_gb"),
                "ram_percent": mem.get("percent_used"),
                "cores": 4,
                "server_time": datetime.now(timezone.utc).isoformat()
            }

        elif tool_name == "ping_network_target":
            tgt = args.get("target", "1.1.1.1")
            from backend.routers.network import execute_icmp_ping
            res = execute_icmp_ping(tgt, count=1, timeout_sec=2)
            return {"status": "success", "target": tgt, "reachable": res.get("reachable", False), "latency_ms": res.get("latency_ms")}

        elif tool_name == "manage_sora_memory":
            act = args.get("action", "recall")
            fact = args.get("fact", "")
            cat = args.get("category", "general")
            if act == "learn" and fact:
                key = f"key_{uuid.uuid4().hex[:6]}"
                res = save_sora_memory(key=key, fact=fact, category=cat)
                return {"status": "success", "learned": res}
            elif act == "forget" and fact:
                res = forget_sora_memory(fact)
                return {"status": "success", "forgotten": res}
            else:
                mems = list_sora_memories()
                return {"status": "success", "memories": mems}

        elif tool_name == "get_weather_report":
            city = args.get("city", "Cairo")
            from plugins.weather_plugin import get_city_coordinates, fetch_weather_report
            coords = get_city_coordinates(city)
            if coords:
                lat, lon, name = coords
                report = fetch_weather_report(lat, lon, name)
                return {"status": "success", "weather": report}
            return {"status": "warning", "message": f"Could not find coordinates for {city}"}

        return {"status": "error", "message": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"status": "error", "message": f"Tool execution failed: {e}"}

async def run_sora_react_loop(
    prompt: str,
    session_id: str = "default",
    client_ip: str = "internal",
    max_steps: int = 5
) -> dict:
    """
    Sora ReAct (Reasoning + Acting) autonomous agent engine.
    Iteratively plans, selects tools, executes actions, observes results, and synthesizes final answers.
    """
    global ACTIVE_GEMINI_MODEL
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("AI_API_KEY")

    if not api_key:
        return {
            "status": "warning",
            "reply": "⚠️ Gemini API Key not configured in environment. Please configure GEMINI_API_KEY.",
            "steps": []
        }

    sess_id = session_id or "default"
    record_chat_turn(sess_id, "user", prompt)

    memory_context = get_sora_memory_context()
    recent_history = get_recent_chat_turns_context(sess_id, limit=4)

    system_instruction = (
        "You are Sora, Mohammed's personal autonomous Server AI Agent with ReAct (Reasoning + Acting) capability. "
        "You manage server operations, real-time ESP32-2 notification pushes, SQLite to-do tasks, live sensor telemetry, "
        "server diagnostics, long-term memory, and network tools. "
        "When Mohammed asks you to perform actions, check statuses, or solve multi-step requests, call the appropriate tools. "
        "After observing tool results, provide a warm, concise, and helpful final response (1-3 sentences) as Sora.\n"
        f"{memory_context}\n"
        f"{recent_history}"
    )

    contents = [
        {
            "role": "user",
            "parts": [{"text": prompt}]
        }
    ]

    steps_taken = []
    final_reply = ""
    active_model = ACTIVE_GEMINI_MODEL or "gemini-2.5-flash"

    candidate_models = [
        active_model,
        "gemini-2.5-flash",
        "gemini-flash-latest",
        "gemini-2.5-pro",
        "gemini-2.5-flash-lite",
    ]
    seen = set()
    model_queue = [m for m in candidate_models if not (m in seen or seen.add(m))]

    for step_num in range(max_steps):
        req_payload = {
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": contents,
            "tools": [
                {
                    "functionDeclarations": SORA_REACT_TOOLS
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 600
            }
        }
        req_data = json.dumps(req_payload).encode("utf-8")

        response_json = None
        used_model = None

        for model_candidate in model_queue:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_candidate}:generateContent?key={api_key}"
            req_obj = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                def do_call():
                    with urllib.request.urlopen(req_obj, timeout=15) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                response_json = await asyncio.to_thread(do_call)
                used_model = model_candidate
                ACTIVE_GEMINI_MODEL = model_candidate
                break
            except urllib.error.HTTPError as he:
                err_body = he.read().decode("utf-8", errors="ignore")
                print(f"[Sora ReAct HTTP {he.code}] Model: {model_candidate} | Error: {err_body[:100]}")
                if he.code in (400, 404):
                    continue
                break
            except Exception as e:
                print(f"[Sora ReAct Error] Model: {model_candidate} | Error: {e}")
                continue

        if not response_json or "candidates" not in response_json or not response_json["candidates"]:
            break

        cand = response_json["candidates"][0]
        content_obj = cand.get("content", {})
        parts = content_obj.get("parts", [])

        # Check if model produced function call(s)
        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not function_calls:
            text_parts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought", False)]
            if not text_parts:
                text_parts = [p.get("text", "") for p in parts if "text" in p]
            final_reply = "".join(text_parts).strip()
            break

        # Append model message with functionCall to contents history
        contents.append({
            "role": "model",
            "parts": parts
        })

        # Execute each function call
        for fc in function_calls:
            fn_name = fc.get("name", "")
            fn_args = fc.get("args", {})
            step_record = {
                "step": step_num + 1,
                "tool": fn_name,
                "args": fn_args,
            }

            tool_result = await execute_sora_tool(fn_name, fn_args)
            step_record["output"] = tool_result
            steps_taken.append(step_record)

            # Append functionResponse to contents
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": fn_name,
                        "response": tool_result
                    }
                }]
            })

    if not final_reply:
        if steps_taken:
            last_tool = steps_taken[-1]["tool"]
            final_reply = f"I've executed {len(steps_taken)} actions using {last_tool}."
        else:
            final_reply = "I've processed your instruction."

    record_chat_turn(sess_id, "model", final_reply)
    return {
        "status": "success",
        "reply": final_reply,
        "steps": steps_taken,
        "model": ACTIVE_GEMINI_MODEL or "gemini-2.5-flash"
    }

async def ai_chat_core(req: ChatRequest, client_ip: str = "internal", session_id: str = "default") -> dict:
    """ServerAI chat powered by Sora ReAct engine."""
    sess_id = req.session_id or session_id or "default"
    return await run_sora_react_loop(prompt=req.message, session_id=sess_id, client_ip=client_ip)

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

    # 3. Task Notification Push (Send To-Do List to esp32-2 Notification Receiver)
    if any(k in lower_inst for k in [
        "send my todo", "send my to-do", "send my tasks", "send my list",
        "push my todo", "push my to-do", "push my tasks", "push my list",
        "send todo to esp32", "send tasks to esp32", "send to-do to esp32", "send todos to esp32",
        "show todo on esp32", "show tasks on esp32", "display todo on esp32", "display tasks on esp32",
        "send todo list to esp32", "send task list to esp32", "notify my tasks", "notify my todo",
        "send my to do list to the esp32", "send my todo list to the esp32", "send to do list to esp32"
    ]):
        with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT text, priority FROM todos WHERE completed = 0 ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC").fetchall()
        if rows:
            formatted_tasks = " | ".join([f"{i+1}. {r['text']}" for i, r in enumerate(rows)])
            alert_text = f"Tasks ({len(rows)}): {formatted_tasks}"
            from backend.routers.telemetry import push_message_to_device
            pushed = await push_message_to_device(
                device_id="esp32-2",
                message=alert_text,
                status="Pending Tasks",
                emotion="notice",
            )
            reply_msg = f"I've transmitted your {len(rows)} pending task{'s' if len(rows) > 1 else ''} directly to your esp32-2 notification screen: '{formatted_tasks}'."
        else:
            reply_msg = "You have no pending tasks on your to-do list to send."

        record_chat_turn(device_id or "default", "model", reply_msg)
        return record_agent_log(
            action="todo_push_to_device",
            reply=reply_msg,
            extra_data={"count": len(rows) if rows else 0, "device_id": "esp32-2", "pushed": pushed if rows else False}
        )

    # 4. Hardware Notification & Alert Intents (Push directly to esp32-2)
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

    # 6. Sora Autonomous ReAct Agent Loop (Multi-Step Planning & Dynamic Tool Execution)
    react_resp = await run_sora_react_loop(prompt=inst, session_id=device_id or "default", client_ip=client_ip)
    reply_text = react_resp.get("reply", "I processed your request.")
    steps = react_resp.get("steps", [])
    action_name = "react_agent" if steps else "ai_inference"
    return record_agent_log(
        action=action_name,
        reply=reply_text,
        extra_data={"steps": steps, "model": react_resp.get("model")}
    )

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

