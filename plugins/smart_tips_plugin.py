"""
Sample Dynamic Server-Side Plugin for SensorsHub & ESP32 Assistant.
Add any custom Python logic here. It executes on the server without modifying the ESP32!
"""
from typing import Optional

def handle_intent(instruction: str, context: str = "") -> Optional[str]:
    text = instruction.lower().strip()
    
    if "system architecture" in text or "how do you work" in text:
        return "I am powered by a thin ESP32 hardware client connected to a Python server-side agent hub with SQLite and Gemini AI."
    
    if "battery tip" in text or "battery advice" in text:
        return "Tip: For maximum lifespan of lithium batteries, avoid keeping the device at 100% or 0% charge for prolonged periods."

    return None
