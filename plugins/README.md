# Server-Side Agent Plugins Directory

Any `.py` file placed in this `plugins/` directory is automatically discovered and loaded by `server.py`.

### How to Add a New Tool / Capability
1. Create a `.py` file in this folder (e.g. `plugins/weather.py` or `plugins/home_assistant.py`).
2. Define a function named `handle_intent(instruction: str, context: str = "") -> Optional[str]`:
   - Return a `str` with the answer/speech response if your plugin handles the instruction.
   - Return `None` if your plugin does not match the instruction (so other plugins or Gemini can process it).

### Example Plugin (`plugins/example.py`):
```python
def handle_intent(instruction: str, context: str = "") -> str | None:
    text = instruction.lower()
    if "ping" in text or "are you online" in text:
        return "The server agent is fully operational and responsive."
    return None
```
No compiling, no C++ coding, and zero ESP32 flashing required!
