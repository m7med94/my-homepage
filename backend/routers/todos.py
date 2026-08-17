# backend/routers/todos.py — To-Do List Management Routes
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query, status
from pydantic import BaseModel, Field

from backend.config import DB_PATH, require_dashboard_session
from backend.events import subscribers

router = APIRouter(prefix="/api/v1/todos", tags=["Todos"])

class TodoCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000, description="Task description")
    priority: Optional[str] = Field("normal", description="Priority level: high, normal, routine")

class TodoUpdate(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=1000)
    priority: Optional[str] = Field(None)
    completed: Optional[bool] = Field(None)

@router.get("", summary="Get To-Do List & Voice Summary")
def get_todos(
    request: Request,
    completed: Optional[bool] = Query(None, description="Filter by completion status"),
):
    """Returns to-do items and a natural voice summary for ESP32 and web dashboard."""
    require_dashboard_session(request)
    query = "SELECT id, text, priority, completed, created_at, updated_at FROM todos WHERE 1=1"
    params = []
    if completed is not None:
        query += " AND completed = ?"
        params.append(1 if completed else 0)
    query += " ORDER BY completed ASC, CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at DESC"

    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()

    todos = []
    pending_items = []
    for r in rows:
        is_done = bool(r["completed"])
        item = {
            "id": r["id"],
            "text": r["text"],
            "priority": r["priority"],
            "completed": is_done,
            "createdAt": r["created_at"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        todos.append(item)
        if not is_done:
            p_tag = f" ({r['priority']} priority)" if r["priority"] != "normal" else ""
            pending_items.append(f"{r['text']}{p_tag}")

    if pending_items:
        count = len(pending_items)
        tasks_spoken = ", ".join([f"{idx+1}. {txt}" for idx, txt in enumerate(pending_items)])
        voice_summary = f"You have {count} pending task{'s' if count > 1 else ''}: {tasks_spoken}."
    else:
        voice_summary = "Your to-do list is completely clear. You have no pending tasks."

    return {
        "status": "success",
        "count": len(todos),
        "pending_count": len(pending_items),
        "summary": voice_summary,
        "voice_summary": voice_summary,
        "todos": todos,
        "data": todos,
    }

@router.post("", status_code=status.HTTP_201_CREATED, summary="Create To-Do Item")
def create_todo(payload: TodoCreate, request: Request):
    """Creates a new to-do task and notifies active SSE clients."""
    require_dashboard_session(request)
    todo_id = str(uuid.uuid4())
    priority = payload.priority.lower() if payload.priority else "normal"
    if priority not in ("high", "normal", "routine"):
        priority = "normal"

    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute(
            "INSERT INTO todos (id, text, priority, completed, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
            (todo_id, payload.text.strip(), priority, now_iso, now_iso),
        )

    notification = {
        "type": "todo_created",
        "id": todo_id,
        "text": payload.text.strip(),
        "priority": priority,
        "timestamp": now_iso,
    }
    for q in list(subscribers):
        try:
            q.put_nowait(json.dumps(notification))
        except Exception:
            subscribers.discard(q)

    return {
        "status": "success",
        "message": "Task added to to-do list",
        "id": todo_id,
        "todo": {
            "id": todo_id,
            "text": payload.text.strip(),
            "priority": priority,
            "completed": False,
            "createdAt": now_iso,
        },
    }

@router.patch("/{todo_id}", summary="Update or Toggle To-Do Item")
def update_todo(todo_id: str, payload: TodoUpdate, request: Request):
    """Updates to-do text, priority, or completion status with SSE sync."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if not cur:
            raise HTTPException(status_code=404, detail="To-do item not found")

        updates = []
        params = []
        if payload.text is not None:
            updates.append("text = ?")
            params.append(payload.text.strip())
        if payload.priority is not None:
            updates.append("priority = ?")
            params.append(payload.priority.lower())
        if payload.completed is not None:
            updates.append("completed = ?")
            params.append(1 if payload.completed else 0)

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(todo_id)

        conn.execute(f"UPDATE todos SET {', '.join(updates)} WHERE id = ?", params)

    now_iso = datetime.now(timezone.utc).isoformat()
    notification = {
        "type": "todo_updated",
        "id": todo_id,
        "timestamp": now_iso,
    }
    for q in list(subscribers):
        try:
            q.put_nowait(json.dumps(notification))
        except Exception:
            subscribers.discard(q)

    return {"status": "success", "message": "Task updated successfully"}

@router.delete("", summary="Delete Multiple or Completed To-Do Items")
def clear_multiple_todos(
    request: Request,
    completed: Optional[bool] = Query(None, description="If true, deletes only completed tasks"),
):
    """Bulk deletes completed or all tasks."""
    require_dashboard_session(request)
    query = "DELETE FROM todos WHERE 1=1"
    params = []
    if completed is not None:
        query += " AND completed = ?"
        params.append(1 if completed else 0)

    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        res = conn.execute(query, params)
        deleted_count = res.rowcount

    now_iso = datetime.now(timezone.utc).isoformat()
    notification = {
        "type": "todo_deleted",
        "deleted_count": deleted_count,
        "timestamp": now_iso,
    }
    for q in list(subscribers):
        try:
            q.put_nowait(json.dumps(notification))
        except Exception:
            subscribers.discard(q)

    return {
        "status": "success",
        "message": f"Successfully deleted {deleted_count} task(s)",
        "deleted_count": deleted_count,
    }

@router.delete("/{todo_id}", summary="Delete To-Do Item")
def delete_todo(todo_id: str, request: Request):
    """Deletes a single to-do item by ID."""
    require_dashboard_session(request)
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        res = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="To-do item not found")

    now_iso = datetime.now(timezone.utc).isoformat()
    notification = {
        "type": "todo_deleted",
        "id": todo_id,
        "timestamp": now_iso,
    }
    for q in list(subscribers):
        try:
            q.put_nowait(json.dumps(notification))
        except Exception:
            subscribers.discard(q)

    return {"status": "success", "message": "Task deleted successfully", "id": todo_id}
