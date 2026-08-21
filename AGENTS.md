# Developer & AI Agent Guide: Adding Voice Tools & Capabilities

> **CRITICAL ARCHITECTURAL RULE FOR FUTURE AGENTS:**
> **DO NOT modify or re-flash the ESP32 C++ firmware to add new tasks, voice commands, or tools.**
> The ESP32 is a **Thin Audio Client** running the standard XiaoZhi firmware.
> All voice tools and capabilities are connected to XiaoZhi Cloud via the **Python Cloud MCP Bridge** in `backend/mcp_bridge.py`.

---

## 🛠️ How to Add a New Voice Tool / Skill

You have two simple ways to add new voice capabilities to the assistant:

### Option A: Add a Cloud MCP Tool in `backend/mcp_bridge.py` (Recommended & Primary)
1. Open `backend/mcp_bridge.py`.
2. Add your tool definition to `get_mcp_tools_list()`:
   ```python
   {
       "name": "my_new_tool",
       "description": "Clear description of what the tool does and when the LLM should invoke it.",
       "inputSchema": {
           "type": "object",
           "properties": {
               "param": {"type": "string", "description": "Parameter explanation"}
           },
           "required": ["param"]
       }
   }
   ```
3. Add the execution handler in `execute_mcp_tool()`:
   ```python
   elif name == "my_new_tool":
       param = arguments.get("param")
       # Execute your custom Python logic, database query, or API call
       return f"Successfully executed tool with {param}."
   ```
4. Restart `server.py`. XiaoZhi Cloud will **instantly register the new tool over the active MCP WebSocket**!

---

### Option B: Drop-in Plugin in `plugins/`
1. Create a new file in `plugins/` (e.g. `plugins/spotify.py`, `plugins/home_assistant.py`).
2. Implement `handle_intent(instruction: str, context: str = "") -> Optional[str]`.
3. The plugin is automatically loaded by the `dispatch_server_ai` tool.

---

## 📂 Architecture Overview
- **ESP32 Microcontroller (`esp32/`)**: Owns only physical hardware (Microphone, ES8311 Audio Codec, OLED Display, Volume, Hardware Sleep).
- **XiaoZhi Cloud Gateway (`wss://api.xiaozhi.me/mcp/`)**: Real-time voice LLM, STT, and Neural TTS pipeline.
- **Backend Hub (`server.py` & `backend/mcp_bridge.py`)**: Owns all tools, databases (SQLite), To-Dos, AI model inference, local network diagnostics, and the Cloud MCP WebSocket connection.
- **Plugins (`plugins/`)**: Drop-in Python scripts for custom capabilities.

---

## 🚀 Deployment & Code Workflow Rule (Git Push -> Pull)

> **CRITICAL DEPLOYMENT RULE FOR FUTURE SESSIONS & AGENTS:**
> 1. Always make edits **locally** in the project repository workspace.
> 2. After making changes/fixes, **commit and push to GitHub** (`git push origin main`).
> 3. Then deploy to the remote server via SSH by pulling the latest commit (`git pull origin main`) and restarting the service.
> 4. Never do direct live code hacking over SSH without syncing through Git.

---

## 🔑 Remote Server SSH Connection Details

- **User**: `m7med_am`
- **Current GCP IP**: `34.58.4.228` (or dynamic GCP external IP)
- **SSH Key Location (Windows)**: `C:\Users\m7med\.ssh\gcp_key`
- **Standard SSH Command**:
  ```powershell
  ssh -i "C:\Users\m7med\.ssh\gcp_key" -o ServerAliveInterval=30 m7med_am@<SERVER_IP>
  ```
- **Single-command Remote Deploy**:
  ```powershell
  ssh -i "C:\Users\m7med\.ssh\gcp_key" -o StrictHostKeyChecking=no m7med_am@<SERVER_IP> "cd ~/my-homepage && git pull origin main && sudo systemctl restart my-homepage || true"
  ```


