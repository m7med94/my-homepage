# backend/events.py — Server-Sent Events (SSE) Hub & Real-Time Sync
import asyncio
import json
from typing import Set, Dict, Any

subscribers: Set[asyncio.Queue] = set()

def broadcast_event(event_type: str, data: Dict[str, Any]):
    """Broadcasts a JSON-encoded event payload to all active SSE browser dashboards."""
    payload = {"type": event_type, **data}
    json_str = json.dumps(payload)
    for q in list(subscribers):
        try:
            q.put_nowait(json_str)
        except Exception:
            subscribers.discard(q)
