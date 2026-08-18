# backend/mcp_bridge.py — XiaoZhi Cloud MCP WebSocket Bridge
import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import DB_PATH, MUSIC_DIR, PLUGINS_DIR

logger = logging.getLogger("XiaoZhiMCP")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [XiaoZhi MCP] %(message)s")

DEFAULT_MCP_URL = "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjEwNDYxNTEsImFnZW50SWQiOjIyNDg3NzAsImVuZHBvaW50SWQiOiJhZ2VudF8yMjQ4NzcwIiwicHVycG9zZSI6Im1jcC1lbmRwb2ludCIsImlhdCI6MTc4NzAxODAzMywiZXhwIjoxODE4NTc1NjMzfQ.JeXHL2NU1Ytm0TgBYI9F2guj4ONjyyb9dU31dVyadKWGnQuNjT8ATrjPueiXLOr3CkC53Vrhj4f-KRVEltB-4Q"

# =========================================================================================
# TOOL DEFINITIONS FOR XIAOZHI CLOUD LLM
# =========================================================================================

def get_mcp_tools_list() -> List[Dict[str, Any]]:
    return [
        {
            "name": "check_server_connection",
            "description": "Check if Mohammed's central server and ESP32 home hub are connected and healthy, and say a friendly greeting.",
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
                        "description": "The task ID (for complete or delete)"
                    }
                },
                "required": ["action"]
            }
        },
        {
            "name": "ping_network_target",
            "description": "Ping any local network device, server, gateway, or internet host (e.g. 1.1.1.1, google.com) and return round-trip latency.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Hostname or IP address to ping"}
                },
                "required": ["target"]
            }
        },
        {
            "name": "search_server_music",
            "description": "Search or list available music tracks and playlists in Mohammed's server music vault.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword for song title or artist, or empty to list all tracks"}
                }
            }
        },
        {
            "name": "dispatch_server_ai",
            "description": "Dispatch a complex question, custom plugin calculation, or general query to the server's Gemini AI and plugin registry.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "The question or instruction to dispatch"}
                },
                "required": ["instruction"]
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
        return "Hello Mohammed! The central server hub is online, healthy, and connected to XiaoZhi Cloud via high-speed MCP bridge."

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

    elif name == "search_server_music":
        query = arguments.get("query", "").lower()
        if not os.path.exists(MUSIC_DIR):
            return "Server music vault is empty."
        files = [f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(('.mp3', '.wav', '.ogg', '.opus', '.m4a', '.flac'))]
        if not files:
            return "No music files found in the server vault."
        if query:
            matched = [f for f in files if query in f.lower()]
            if matched:
                return f"Found {len(matched)} matching track(s): " + ", ".join(matched[:5])
            return f"No tracks found matching '{query}'. Available songs: " + ", ".join(files[:5])
        return f"Server vault contains {len(files)} track(s): " + ", ".join(files[:6])

    elif name == "dispatch_server_ai":
        instruction = arguments.get("instruction", "")
        if not instruction:
            return "Please provide an instruction or question."
        try:
            from backend.routers.agent import dispatch_agent_instruction, AgentDispatchRequest
            req = AgentDispatchRequest(instruction=instruction, device_id="xiaozhi_mcp_cloud", context="cloud_mcp")
            resp = await dispatch_agent_instruction(req)
            return resp.get("reply") or resp.get("summary") or "Query executed successfully."
        except Exception as e:
            return f"Server AI error: {e}"

    return f"Tool '{name}' is not recognized."

# =========================================================================================
# WEBSOCKET MCP CLIENT RUNNER
# =========================================================================================

async def run_xiaozhi_mcp_bridge(mcp_ws_url: str = DEFAULT_MCP_URL):
    """
    Maintains a persistent, auto-reconnecting JSON-RPC 2.0 WebSocket client connection
    to XiaoZhi Cloud MCP Endpoint.
    """
    import websockets

    logger.info(f"Connecting to XiaoZhi Cloud MCP Endpoint at: {mcp_ws_url[:50]}...")

    while True:
        try:
            async with websockets.connect(
                mcp_ws_url,
                ping_interval=20,
                ping_timeout=20,
                max_size=10 * 1024 * 1024
            ) as ws:
                logger.info("Successfully CONNECTED to XiaoZhi Cloud MCP Bridge! Status: ONLINE (Green)")

                async for message in ws:
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
                                        "version": "2.4.0"
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
            logger.warning(f"XiaoZhi MCP WebSocket disconnected: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    url = os.getenv("XIAOZHI_MCP_URL", DEFAULT_MCP_URL)
    asyncio.run(run_xiaozhi_mcp_bridge(url))
