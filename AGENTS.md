# Developer & AI Agent Guide: Adding Voice Tools & Capabilities

> **CRITICAL ARCHITECTURAL RULE FOR FUTURE AGENTS:**
> **DO NOT modify or re-flash the ESP32 C++ firmware to add new tasks, voice commands, or tools.until the user is asked for**
> The ESP32 is designed as a **Thin Hardware Client** that relays all voice requests to the Python server at `POST /api/v1/agent/dispatch`.

---

## 🛠️ How to Add a New Voice Tool / Skill

You have two simple ways to add new voice capabilities to the assistant:

### Option A: Modular Python Plugin in `plugins/` (Recommended)
1. Create a new file in the `plugins/` folder (e.g. `plugins/weather.py`, `plugins/spotify.py`, `plugins/home_assistant.py`).
2. Implement the `handle_intent` function:
   ```python
   from typing import Optional

   def handle_intent(instruction: str, context: str = "") -> Optional[str]:
       text = instruction.lower().strip()
       
       # Check if the voice prompt matches your tool's keywords
       if "weather in" in text:
           city = text.split("weather in")[-1].strip()
           # Call weather API or perform custom logic here
           return f"The weather in {city} is sunny and 24 degrees celsius."
           
       return None  # Return None if not handled, so other plugins or Gemini can process it
   ```
3. Restart `server.py`. The plugin is **automatically discovered and executed**.

---

### Option B: Add Directly to `server.py`
Inside `dispatch_agent_instruction()` in `server.py`, add a new intent condition:
```python
if "turn on the light" in lower_inst:
    # Trigger Home Assistant or IoT webhook
    return {"status": "success", "action": "iot_light_on", "reply": "Turning on the lights now."}
```

---

## 📂 Architecture Overview
- **ESP32 Microcontroller (`esp32/`)**: Owns only physical hardware (Microphone, ES8311 Audio Codec, OLED Display, Volume, Hardware Sleep).
- **Backend Hub (`server.py`)**: Owns all databases (SQLite), business logic, To-Dos, music vault, AI model inference (Gemini / Groq / OpenAI), and the plugin registry.
- **Plugins (`plugins/`)**: Drop-in Python scripts for custom user capabilities and agent tools.
