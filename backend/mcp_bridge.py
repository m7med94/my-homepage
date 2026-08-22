# backend/mcp_bridge.py — XiaoZhi Cloud MCP WebSocket Bridge
import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import DB_PATH, PLUGINS_DIR

logger = logging.getLogger("XiaoZhiMCP")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [XiaoZhi MCP] %(message)s")

DEFAULT_MCP_URL = "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjEwNDYxNTEsImFnZW50SWQiOjIyNDg3NzAsImVuZHBvaW50SWQiOiJhZ2VudF8yMjQ4NzcwIiwicHVycG9zZSI6Im1jcC1lbmRwb2ludCIsImlhdCI6MTc4NzAxODAzMywiZXhwIjoxODE4NTc1NjMzfQ.JeXHL2NU1Ytm0TgBYI9F2guj4ONjyyb9dU31dVyadKWGnQuNjT8ATrjPueiXLOr3CkC53Vrhj4f-KRVEltB-4Q"

# =========================================================================================
# GATEWAY ACTIVE / STANDBY MANAGER
# =========================================================================================

class GatewayManager:
    """Manages dynamic activation and standby states for XiaoZhi Cloud MCP and ESP32 gateway."""
    def __init__(self):
        self.is_active = True
        self.mcp_connected = False
        self.mcp_task: Optional[asyncio.Task] = None
        self.active_ws = None

    def get_status(self) -> dict:
        return {
            "active": self.is_active,
            "mcp_connected": self.mcp_connected,
            "status_text": "ONLINE" if (self.is_active and self.mcp_connected) else ("STANDBY" if not self.is_active else "CONNECTING")
        }

    async def set_active(self, active: bool, mcp_url: Optional[str] = None) -> dict:
        self.is_active = active
        from backend.events import broadcast_event

        target_url = mcp_url or os.getenv("XIAOZHI_MCP_URL", DEFAULT_MCP_URL)
        if not active:
            self.mcp_connected = False
            if self.active_ws:
                try:
                    await self.active_ws.close()
                except Exception:
                    pass
                self.active_ws = None
            if self.mcp_task and not self.mcp_task.done():
                self.mcp_task.cancel()
                self.mcp_task = None
            logger.info("[XiaoZhi Gateway] Switched to STANDBY mode (MCP WebSocket disconnected).")
        else:
            if not self.mcp_task or self.mcp_task.done():
                logger.info("[XiaoZhi Gateway] Switched to ACTIVE mode (Connecting to Cloud MCP)...")
                self.mcp_task = asyncio.create_task(run_xiaozhi_mcp_bridge(target_url))

        status_dict = self.get_status()
        broadcast_event("gateway_status", status_dict)
        return status_dict

gateway_manager = GatewayManager()

# =========================================================================================
# TOOL DEFINITIONS FOR XIAOZHI CLOUD LLM
# =========================================================================================

def get_mcp_tools_list() -> List[Dict[str, Any]]:
    return [
        {
            "name": "check_server_connection",
            "description": "Check if Sora (Mohammed's Server AI agent) and the central server are connected and healthy, and say a friendly greeting from Sora.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "string", "description": "Optional device identifier"}
                }
            }
        },
        {
            "name": "get_server_diagnostics",
            "description": "Get real-time Linux server system stats including RAM usage, CPU status, and local network latency.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "manage_todos",
            "description": "Manage Mohammed's to-do list: view pending tasks, add a new task, or mark a task as completed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "complete", "delete"],
                        "description": "The action to perform on the to-do list"
                    },
                    "text": {
                        "type": "string",
                        "description": "The task content (required for 'add')"
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["high", "normal", "routine"],
                        "description": "Priority level of the task (default: normal)"
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task identifier or search keyword (for 'complete' or 'delete')"
                    }
                },
                "required": ["action"]
            }
        },
        {
            "name": "ping_network_target",
            "description": "Ping any IP or host (e.g. 1.1.1.1, google.com) to measure network latency and check connectivity.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Hostname or IP address to ping"}
                },
                "required": ["target"]
            }
        },
        {
            "name": "get_weather",
            "description": "Get live real-time weather, temperature, humidity, wind, and forecast for any city in the world (e.g. Cairo, Munich, London, Tokyo).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "The city name to check weather for"}
                },
                "required": ["city"]
            }
        },
        {
            "name": "search_wikipedia",
            "description": "Look up factual summaries and encyclopedia articles from Wikipedia on any person, science concept, historical event, or topic.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "The search topic or entity"}
                },
                "required": ["topic"]
            }
        },
        {
            "name": "get_smart_tip",
            "description": "Get an intelligent developer, coding, or productivity tip of the day.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "dispatch_server_ai",
            "description": "Ask Sora (Mohammed's Server AI Agent) to perform any task on the server, answer questions, query telemetry or to-dos, and execute server actions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "The question or instruction for Sora"}
                },
                "required": ["instruction"]
            }
        },
        {
            "name": "send_device_alert",
            "description": "Ask Sora to send an instant visual/spoken notification alert to Mohammed's notification ESP32 display (esp32-2).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The alert message text to display and announce on the notification ESP32"},
                    "title": {"type": "string", "description": "Short title or header for the alert"},
                    "emotion": {"type": "string", "enum": ["happy", "notice", "warning", "confused"], "description": "Alert emotion tone"}
                },
                "required": ["message"]
            }
        },
        {
            "name": "manage_sora_memory",
            "description": "Teach Sora new facts/preferences, recall stored memories, or forget facts from Sora's long-term memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["remember", "recall", "forget", "list"],
                        "description": "Action: 'remember' to learn a fact, 'recall' or 'list' to view memories, 'forget' to delete a fact"
                    },
                    "fact": {
                        "type": "string",
                        "description": "The fact or note to remember or forget"
                    },
                    "key": {
                        "type": "string",
                        "description": "Optional short identifier for the memory"
                    }
                },
                "required": ["action"]
            }
        }
    ]

# =========================================================================================
# TOOL EXECUTION HANDLERS
# =========================================================================================

async def execute_mcp_tool(name: str, arguments: Dict[str, Any]) -> str:
    """Executes the tool requested by XiaoZhi Cloud LLM and returns the text result."""
    logger.info(f"Executing cloud tool: '{name}' with args: {arguments}")
    
    if name == "check_server_connection":
        return "Hello Mohammed! I am Sora, your personal Server AI Agent. The central server is online, healthy, and all systems are running smoothly."

    elif name == "get_server_diagnostics":
        try:
            from backend.routers.network import get_server_memory_stats
            mem = get_server_memory_stats()
            return f"Server Status: Active. RAM: {mem['used_gb']}GB / {mem['total_gb']}GB ({mem['percent_used']}% used, {mem['free_gb']}GB available). 4 Cores Online."
        except Exception as e:
            return f"Server diagnostics: Active. Error reading memory: {e}"

    elif name == "manage_todos":
        action = arguments.get("action", "list")
        if action == "list":
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT id, text, priority, completed FROM todos WHERE completed = 0 ORDER BY created_at DESC").fetchall()
            if not rows:
                return "Your to-do list is currently empty! You have no pending tasks."
            items = [f"{r['text']} (priority: {r['priority']})" for r in rows]
            return f"You have {len(items)} pending task(s): " + ", ".join(items)

        elif action == "add":
            text = arguments.get("text", "").strip()
            if not text:
                return "Please provide a description for the task."
            priority = arguments.get("priority", "normal")
            import uuid
            new_id = str(uuid.uuid4())[:8]
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute("INSERT INTO todos (id, text, priority, completed) VALUES (?, ?, ?, 0)", (new_id, text, priority))
            return f"Added '{text}' to your to-do list with {priority} priority."

        elif action in ["complete", "delete"]:
            task_id = arguments.get("task_id", "")
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                if action == "complete":
                    conn.execute("UPDATE todos SET completed = 1 WHERE id = ? OR text LIKE ?", (task_id, f"%{task_id}%"))
                    return f"Marked task '{task_id}' as completed."
                else:
                    conn.execute("DELETE FROM todos WHERE id = ? OR text LIKE ?", (task_id, f"%{task_id}%"))
                    return f"Deleted task '{task_id}' from your list."
        return "Unknown to-do action."

    elif name == "ping_network_target":
        target = arguments.get("target", "127.0.0.1")
        try:
            from backend.routers.network import execute_icmp_ping
            res = execute_icmp_ping(target, count=1, timeout_sec=2)
            if res["reachable"]:
                return f"Ping to {target} successful: Round-trip latency is {res['latency_ms']} ms."
            else:
                return f"Ping to {target} failed: Target is unreachable ({res.get('status')})."
        except Exception as e:
            return f"Error pinging {target}: {e}"

    elif name == "get_weather":
        city = arguments.get("city", "Cairo").strip()
        try:
            from plugins.weather_plugin import get_city_coordinates, fetch_weather_report
            coords = get_city_coordinates(city)
            if coords:
                lat, lon, resolved_city, country = coords
                report = fetch_weather_report(lat, lon, resolved_city, country)
                if report:
                    return report
            return f"Sorry, could not find weather details for {city}."
        except Exception as e:
            return f"Weather lookup error: {e}"

    elif name == "search_wikipedia":
        topic = arguments.get("topic", "").strip()
        if not topic:
            return "Please provide a topic to search on Wikipedia."
        try:
            from plugins.wikipedia_plugin import handle_intent as wiki_intent
            res = wiki_intent(f"who is {topic}") or wiki_intent(f"what is {topic}") or wiki_intent(topic)
            return res or f"No Wikipedia summary found for {topic}."
        except Exception as e:
            return f"Wikipedia search error: {e}"

    elif name == "get_smart_tip":
        try:
            from plugins.smart_tips_plugin import handle_intent as tip_intent
            return tip_intent("smart tip") or "Always keep your Python packages up to date and write clean docstrings!"
        except Exception as e:
            return "Error retrieving smart tip."

    elif name == "dispatch_server_ai":
        instruction = arguments.get("instruction", "")
        if not instruction:
            return "Please provide an instruction or question."
        try:
            from backend.routers.agent import process_agent_instruction_core
            resp = await process_agent_instruction_core(instruction=instruction, device_id="xiaozhi_mcp_cloud", context="cloud_mcp", client_ip="cloud-mcp")
            return resp.get("reply") or resp.get("summary") or "Query executed successfully."
        except Exception as e:
            return f"Server AI error: {e}"

    elif name == "send_device_alert":
        msg = arguments.get("message", "")
        title = arguments.get("title", "Voice Notice")
        emotion = arguments.get("emotion", "notice")
        if not msg:
            return "Please provide an alert message text."
        try:
            from backend.routers.telemetry import push_message_to_device
            await push_message_to_device(
                device_id="esp32-2",
                message=msg,
                status=title,
                emotion=emotion,
            )
        except Exception as e:
            return f"Error transmitting alert to ESP32: {e}"

    elif name == "manage_sora_memory":
        action = arguments.get("action", "recall")
        fact = arguments.get("fact", "").strip()
        key = arguments.get("key", "").strip()
        from backend.routers.agent import save_sora_memory, forget_sora_memory, list_sora_memories
        if action == "remember":
            if not fact:
                return "Please provide the fact you want Sora to remember."
            key_name = key or re.sub(r"[^a-zA-Z0-9_]", "_", fact[:24]).strip("_").lower() or "user_note"
            save_sora_memory(key=key_name, fact=fact, category="user_note", importance=4)
            return f"I have saved that to my long-term memory, Mohammed: '{fact}'."
        elif action in ("recall", "list"):
            mems = list_sora_memories()
            if mems:
                facts = [m["fact"] for m in mems[:5]]
                return "Here is what I remember: " + "; ".join(facts) + "."
            return "My long-term memory is currently empty. Tell me 'Sora, remember that...' to store facts."
        elif action == "forget":
            target = fact or key
            if not target:
                return "Please specify what memory you would like me to forget."
            res = forget_sora_memory(target)
            if res.get("deleted", 0) > 0:
                return f"I have deleted '{target}' from my memory."
            return f"No memory matching '{target}' was found."

    return f"Tool '{name}' is not recognized."

# Global reference to active XiaoZhi Cloud WebSocket
active_mcp_ws = None

async def push_cloud_notification(method: str, params: Optional[Dict[str, Any]] = None) -> bool:
    """Sends a server-initiated JSON-RPC 2.0 notification to XiaoZhi Cloud."""
    global active_mcp_ws
    if not active_mcp_ws:
        logger.warning("Cannot push notification: XiaoZhi Cloud MCP WebSocket is not connected.")
        return False
    try:
        msg = {
            "jsonrpc": "2.0",
            "method": method
        }
        if params is not None:
            msg["params"] = params
        await active_mcp_ws.send(json.dumps(msg))
        logger.info(f"Pushed server notification '{method}' to XiaoZhi Cloud.")
        return True
    except Exception as e:
        logger.error(f"Failed to push notification to XiaoZhi Cloud: {e}")
        return False

async def notify_tools_changed() -> bool:
    """Notifies XiaoZhi Cloud that tools have been dynamically added or updated."""
    return await push_cloud_notification("notifications/tools/list_changed")

async def push_cloud_broadcast(text: str) -> bool:
    """Pushes a server text notification directly into XiaoZhi Cloud."""
    return await push_cloud_notification("notifications/message", {"text": text, "timestamp": time.time()})

# =========================================================================================
# WEBSOCKET MCP CLIENT RUNNER
# =========================================================================================

async def run_xiaozhi_mcp_bridge(mcp_ws_url: str = DEFAULT_MCP_URL):
    """
    Maintains a persistent, auto-reconnecting JSON-RPC 2.0 WebSocket client connection
    to XiaoZhi Cloud MCP Endpoint while gateway_manager is active.
    """
    global active_mcp_ws
    import websockets
    from backend.events import broadcast_event

    logger.info(f"Connecting to XiaoZhi Cloud MCP Endpoint at: {mcp_ws_url[:50]}...")

    while gateway_manager.is_active:
        try:
            async with websockets.connect(
                mcp_ws_url,
                ping_interval=20,
                ping_timeout=20,
                max_size=10 * 1024 * 1024
            ) as ws:
                active_mcp_ws = ws
                gateway_manager.active_ws = ws
                gateway_manager.mcp_connected = True
                logger.info("Successfully CONNECTED to XiaoZhi Cloud MCP Bridge! Status: ONLINE (Green)")
                broadcast_event("gateway_status", gateway_manager.get_status())

                async for message in ws:
                    if not gateway_manager.is_active:
                        break
                    try:
                        data = json.loads(message)
                        msg_id = data.get("id")
                        method = data.get("method")

                        # 1. MCP Initialization
                        if method == "initialize":
                            response = {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {
                                    "protocolVersion": "2024-11-05",
                                    "capabilities": {
                                        "tools": {
                                            "listChanged": False
                                        }
                                    },
                                    "serverInfo": {
                                        "name": "SensorsHub-CentralServer",
                                        "version": "2.5.0"
                                    }
                                }
                            }
                            await ws.send(json.dumps(response))
                            logger.info("Sent MCP initialize response to XiaoZhi Cloud.")

                        elif method == "notifications/initialized":
                            logger.info("XiaoZhi Cloud confirmed MCP initialization complete.")

                        elif method == "ping":
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}}))

                        # 2. List Available Tools
                        elif method == "tools/list":
                            tools = get_mcp_tools_list()
                            response = {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {
                                    "tools": tools
                                }
                            }
                            await ws.send(json.dumps(response))
                            logger.info(f"Advertised {len(tools)} server tools to XiaoZhi Cloud LLM.")

                        # 3. Call Tool
                        elif method == "tools/call":
                            params = data.get("params", {})
                            tool_name = params.get("name")
                            tool_args = params.get("arguments", {})
                            
                            result_text = await execute_mcp_tool(tool_name, tool_args)
                            
                            response = {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": str(result_text)
                                        }
                                    ],
                                    "isError": False
                                }
                            }
                            await ws.send(json.dumps(response))
                            logger.info(f"Dispatched tool result for '{tool_name}' back to cloud.")

                        # Fallback for unrecognized methods
                        elif msg_id is not None:
                            logger.warning(f"Unhandled MCP method '{method}'. Returning empty result.")
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}}))

                    except json.JSONDecodeError:
                        logger.error(f"Received non-JSON message: {message}")
                    except Exception as ex:
                        logger.error(f"Error processing MCP message: {ex}", exc_info=True)
                        if msg_id is not None:
                            await ws.send(json.dumps({
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "error": {"code": -32603, "message": str(ex)}
                            }))

        except asyncio.CancelledError:
            logger.info("XiaoZhi MCP Bridge task cancelled.")
            break
        except Exception as e:
            gateway_manager.mcp_connected = False
            broadcast_event("gateway_status", gateway_manager.get_status())
            if not gateway_manager.is_active:
                break
            logger.warning(f"XiaoZhi MCP WebSocket disconnected: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
        finally:
            gateway_manager.mcp_connected = False
            gateway_manager.active_ws = None
            broadcast_event("gateway_status", gateway_manager.get_status())

if __name__ == "__main__":
    url = os.getenv("XIAOZHI_MCP_URL", DEFAULT_MCP_URL)
    asyncio.run(run_xiaozhi_mcp_bridge(url))
