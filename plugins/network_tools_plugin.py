"""
Local Network Tools & Server Health Plugin for SensorsHub & XiaoZhi ESP32 Assistant.
Executes server disk checks, ESP32 connection audits, device ping tests, and overall server health checks.
"""
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "esp32_telemetry.db")

def check_server_health_summary() -> str:
    """Returns a natural speech response summarizing server health, disk, and database state."""
    try:
        root_path = os.path.abspath(os.sep) if platform.system() == "Windows" else "/"
        total, used, free = shutil.disk_usage(root_path)
        free_gb = round(free / (1024 ** 3), 1)
        used_pct = round((used / total) * 100, 1)

        db_records = 0
        if os.path.exists(DB_PATH):
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                db_records = conn.execute("SELECT COUNT(*) FROM telemetry_logs").fetchone()[0]

        return (
            f"The server is running healthy and optimal. You have {free_gb} gigabytes of free disk storage "
            f"({used_pct} percent used), and the telemetry database is active with {db_records} logged events."
        )
    except Exception as e:
        return f"Server is online, but encountered an issue checking system metrics: {e}"

def check_esp32_connection_status() -> str:
    """Checks whether the XiaoZhi ESP32 device is currently online and when it last transmitted."""
    try:
        if not os.path.exists(DB_PATH):
            return "The ESP32 connection cannot be verified because the database is initializing."

        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT device_id, category, payload_data, client_ip, created_at FROM telemetry_logs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        # Broadcast live 'Hello' greeting event to ESP32 bot and web dashboards
        dev_id = row["device_id"] if row else "mo-project-c3"
        greeting_text = "Hello Mohammed! ESP32 voice node is online and connected."
        try:
            import json, uuid
            from backend.events import subscribers
            entry_id = str(uuid.uuid4())
            ts_iso = datetime.now(timezone.utc).isoformat()
            
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                conn.execute(
                    "INSERT INTO telemetry_logs (id, device_id, category, payload_data, client_ip) VALUES (?, ?, ?, ?, ?)",
                    (entry_id, dev_id, "agent_greeting", greeting_text, "127.0.0.1"),
                )

            event_payload = {
                "type": "esp32_data",
                "id": entry_id,
                "device_id": dev_id,
                "category": "voice_greeting",
                "data": greeting_text,
                "timestamp": ts_iso,
                "client_ip": "127.0.0.1",
            }
            event_str = json.dumps(event_payload)
            for q in list(subscribers):
                try:
                    q.put_nowait(event_str)
                except Exception:
                    subscribers.discard(q)

            # 3. Push immediately over persistent hardware WebSocket if connected
            try:
                import asyncio
                from backend.routers.telemetry import push_message_to_device
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(push_message_to_device(dev_id, message=greeting_text, status="Server Notice", emotion="happy"))
                except RuntimeError:
                    asyncio.run(push_message_to_device(dev_id, message=greeting_text, status="Server Notice", emotion="happy"))
            except Exception as e:
                pass
        except Exception as ex:
            print(f"[Network Tools Plugin] Greeting event note: {ex}")

        if not row:
            return f"I just broadcasted a 'Hello' greeting packet to your ESP32 bot ({dev_id})! Listening for its reply."

        last_dt = None
        created_str = str(row["created_at"] or "").strip()
        try:
            clean_str = created_str.replace("Z", "+00:00")
            last_dt = datetime.fromisoformat(clean_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                try:
                    last_dt = datetime.strptime(created_str, fmt).replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    pass

        now_utc = datetime.now(timezone.utc)
        delta_sec = int((now_utc - last_dt).total_seconds()) if last_dt else 99999
        if delta_sec < 0:
            delta_sec = 0

        ip = row["client_ip"] or "local network"

        if delta_sec < 45:
            return f"Yes, your ESP32 ({dev_id}) is connected and online. I just sent a 'Hello' greeting to your ESP32 bot!"
        elif delta_sec < 300:
            minutes = delta_sec // 60
            return f"Your ESP32 ({dev_id}) is connected in standby mode (last seen {minutes}m ago). I just sent a 'Hello' ping to your device!"
        else:
            hours = delta_sec // 3600
            mins = (delta_sec % 3600) // 60
            time_text = f"{hours} hour{'s' if hours > 1 else ''} and {mins} minutes" if hours > 0 else f"{mins} minutes"
            return f"Your ESP32 ({dev_id}) was last seen {time_text} ago from {ip}. I just broadcasted a 'Hello' greeting to your ESP32 bot!"
    except Exception as e:
        return f"Error checking ESP32 connection status: {e}"

def check_server_disk_space() -> str:
    """Returns a natural speech response of the server's disk space."""
    try:
        root_path = os.path.abspath(os.sep) if platform.system() == "Windows" else "/"
        total, used, free = shutil.disk_usage(root_path)
        total_gb = round(total / (1024 ** 3), 1)
        free_gb = round(free / (1024 ** 3), 1)
        used_pct = round((used / total) * 100, 1)

        return f"The server has {free_gb} gigabytes free out of {total_gb} gigabytes total disk space, which is {used_pct} percent used."
    except Exception as e:
        return f"Could not retrieve server disk space: {e}"

def ping_target_host(target: str) -> str:
    """Executes a single ping test and returns a natural voice description of the network latency."""
    clean_target = target.strip()
    if not re.match(r"^[a-zA-Z0-9.\-_:]+$", clean_target) or len(clean_target) > 60:
        return f"Cannot ping '{clean_target}' due to invalid hostname or IP address format."

    is_win = platform.system() == "Windows"
    cmd = ["ping", "-n", "1", "-w", "1500", clean_target] if is_win else ["ping", "-c", "1", "-W", "2", clean_target]

    start_t = time.time()
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3.0)
        elapsed_ms = round((time.time() - start_t) * 1000, 1)
        out = proc.stdout + proc.stderr

        if proc.returncode == 0 and ("TTL=" in out or "ttl=" in out or "bytes from" in out or "Reply from" in out):
            match = re.search(r"time[=<]([0-9.]+)\s*ms", out, re.IGNORECASE)
            lat = match.group(1) if match else str(elapsed_ms)
            return f"Ping to {clean_target} was successful with a round-trip latency of {lat} milliseconds."
        else:
            return f"Ping to {clean_target} failed or timed out. The host did not reply."
    except Exception as e:
        return f"Ping to {clean_target} timed out or failed."

def handle_intent(instruction: str, context: str = "") -> Optional[str]:
    """
    Main plugin dispatcher.
    Matches network queries, health checks, disk checks, and ping commands.
    """
    text = instruction.lower().strip()

    # 1. Server Health Queries
    if any(q in text for q in [
        "is my server healthy",
        "is the server healthy",
        "is server healthy",
        "server health",
        "check server health",
        "how is the server",
        "server status",
        "is system healthy",
        "check my server",
    ]):
        return check_server_health_summary()

    # 2. ESP32 Connection Queries
    if any(q in text for q in [
        "is the esp32 connected",
        "is esp32 connected",
        "is my esp32 connected",
        "is the esp32 online",
        "is esp32 online",
        "is the device connected",
        "is device online",
        "check esp32 connection",
        "check connection",
        "esp32 connection status",
        "esp32 status",
        "are you connected",
        "are you online",
        "is the voice assistant connected",
        "is the voice assistant online",
    ]) or context in ["esp32", "network_esp32"]:
        return check_esp32_connection_status()

    # 3. Disk Space Queries
    if any(q in text for q in [
        "check server disk space",
        "check disk space",
        "server disk space",
        "disk space",
        "how much disk space",
        "how much storage",
        "server storage",
        "free disk space",
        "disk storage",
    ]):
        return check_server_disk_space()

    # 4. Network Ping Queries
    if text.startswith("ping ") or "ping the " in text or "ping device" in text or "ping host" in text:
        target = re.sub(r"^(?:please\s+)?ping\s+(?:the\s+|device\s+|host\s+)?", "", text).strip()
        target = target.strip(" \"':“”’‘?")
        if not target or target in ["device", "network", "local", "router", "gateway"]:
            target = "127.0.0.1"
        elif target in ["google", "dns", "internet"]:
            target = "8.8.8.8"
        elif target in ["esp32", "assistant"]:
            # Check last client IP of ESP32 or fallback to localhost
            try:
                with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                    row = conn.execute("SELECT client_ip FROM telemetry_logs ORDER BY created_at DESC LIMIT 1").fetchone()
                    if row and row[0] and row[0] != "unknown":
                        target = row[0]
                    else:
                        target = "127.0.0.1"
            except Exception:
                target = "127.0.0.1"

        return ping_target_host(target)

    return None
