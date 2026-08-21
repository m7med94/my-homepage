"""
Wikipedia & Instant Knowledge Search Plugin for SensorsHub & ESP32 Assistant.
Uses the official, free Wikipedia REST API for concise instant summaries.
No API key required.
"""
import json
import re
import urllib.parse
import urllib.request
from typing import Optional

def search_wikipedia_title(query: str) -> Optional[str]:
    """Finds the most relevant Wikipedia page title for a given query."""
    try:
        encoded = urllib.parse.quote(query.strip())
        url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded}&format=json&utf8=1&srlimit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "SensorsHubVoiceAssistant/1.0 (mohammed@sensorshub.local)"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            search_results = data.get("query", {}).get("search", [])
            if search_results:
                return search_results[0].get("title")
    except Exception as e:
        print(f"[Wikipedia Plugin] Search error: {e}")
    return None

def fetch_wikipedia_summary(title: str) -> Optional[str]:
    """Fetches the official extract summary for a Wikipedia page title."""
    try:
        encoded_title = urllib.parse.quote(title.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
        req = urllib.request.Request(url, headers={"User-Agent": "SensorsHubVoiceAssistant/1.0 (mohammed@sensorshub.local)"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            extract = data.get("extract", "").strip()
            if extract:
                # Clean parenthetical citations or pronunciation brackets if present
                clean_extract = re.sub(r"\s*\([^)]*\)", "", extract)
                # Keep first 2-3 sentences for concise voice delivery
                sentences = re.split(r"(?<=[.!?])\s+", clean_extract)
                spoken_text = " ".join(sentences[:2]).strip()
                if len(spoken_text) > 350:
                    spoken_text = spoken_text[:347] + "..."
                return f"According to Wikipedia, {spoken_text}"
    except Exception as e:
        print(f"[Wikipedia Plugin] Summary error: {e}")
    return None

def handle_intent(instruction: str, context: str = "") -> Optional[str]:
    """
    Main plugin entrypoint called by server.py dispatcher.
    Matches queries like 'who was Einstein', 'what is quantum mechanics', 'tell me about pyramids'.
    """
    text = instruction.lower().strip()

    # Ignore queries meant for other system features (todos, weather, sensors, math)
    system_keywords = ["todo", "to-do", "task", "reminder", "sensor", "battery", "weather", "temperature", "humidity"]
    if any(k in text for k in system_keywords) and not text.startswith("wikipedia"):
        return None

    # Ignore pure math calculations (let Gemini or math calculator handle them)
    if re.search(r"\d+\s*[\+\-\*\/xX\^]\s*\d+", text) and not text.startswith("wikipedia"):
        return None

    # Trigger patterns
    match = re.search(
        r"^(?:who\s+(?:was|is)|what\s+(?:is|are|was|were)|tell\s+me\s+about|wikipedia(?:\s+search)?(?:\s+for)?|explain(?:\s+what\s+is)?)\s+(.+)",
        text
    )

    if not match and "wikipedia" not in text and not text.startswith("tell me about"):
        return None

    query = match.group(1).strip() if match else text
    query = re.sub(r"\b(please|can you|briefly|summary of)\b", "", query, flags=re.IGNORECASE).strip()
    query = query.rstrip("?.!")

    if len(query) < 2:
        return None

    # Step 1: Resolve best title via Wikipedia search API
    title = search_wikipedia_title(query)
    if not title:
        title = query.title()

    # Step 2: Fetch summary
    summary = fetch_wikipedia_summary(title)
    if summary:
        return summary

    return f"I couldn't find a Wikipedia article matching '{query}'."
