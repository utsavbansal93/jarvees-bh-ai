"""
Jarvees — FastAPI Backend
Run with: uvicorn main:app --reload --port 8000
Then open: http://localhost:8000
"""

from __future__ import annotations
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
import chat_handler

app = FastAPI(title="Jarvees")
db.init_db()

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
def get_tasks():
    return db.get_active_tasks()


@app.get("/api/tasks/archived")
def get_archived():
    return db.get_archived_tasks()


@app.post("/api/tasks/{task_id}/complete")
def complete(task_id: int):
    task = db.complete_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.post("/api/tasks/{task_id}/uncomplete")
def uncomplete(task_id: int):
    task = db.uncomplete_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.delete("/api/tasks/{task_id}")
def delete(task_id: int):
    if not db.delete_task(task_id):
        raise HTTPException(404, "Task not found")
    return {"ok": True}


# ── Undo ──────────────────────────────────────────────────────────────────────

@app.post("/api/undo")
def undo():
    action_type, snapshot = db.undo_last()
    if action_type is None:
        raise HTTPException(400, "Nothing to undo")
    return {"action": action_type, "snapshot": snapshot}


@app.get("/api/undo/available")
def undo_available():
    return {"available": db.has_undo()}


# ── Model management ──────────────────────────────────────────────────────────

@app.post("/api/model/reset")
def model_reset():
    """
    Reset the Claude billing flag.
    The next chat message will attempt Claude again automatically.
    Returns which model is *expected* to be active after reset.
    """
    chat_handler.reset_claude_flag()
    return {"ok": True, "model": "claude"}


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str

# Undo phrases that get handled directly without any AI call
_UNDO_RE = re.compile(
    r'^\s*(undo|undo\s+that|undo\s+last(\s+action)?|revert(\s+that)?)\s*$',
    re.IGNORECASE
)


@app.post("/api/chat")
def chat(body: ChatRequest):

    # ── Zero-cost undo shortcut ───────────────────────────────────────────────
    # Intercept obvious undo phrases before they hit any AI model.
    if _UNDO_RE.match(body.message):
        action_type, _ = db.undo_last()
        if action_type is None:
            return {"action": "undo", "message": "Nothing to undo.", "model": "system"}
        return {"action": "undo", "message": "Done — last action undone.", "model": "system"}

    # ── AI-powered commands ───────────────────────────────────────────────────
    tasks = db.get_active_tasks()

    try:
        action, model_used = chat_handler.process_chat(body.message, tasks)
    except Exception as e:
        return {
            "action": "chat",
            "message": f"Something went wrong on my end: {e}",
            "model": "error",
        }

    a = action.get("action", "chat")
    result = {
        "action":  a,
        "message": action.get("message", "Done."),
        "model":   model_used,
    }

    # ── Add single task ───────────────────────────────────────────────────────
    if a == "add_task":
        result["task"] = db.add_task(action.get("task", {}))

    # ── Add multiple unrelated tasks ──────────────────────────────────────────
    elif a == "add_multiple_tasks":
        result["tasks"] = [db.add_task(t) for t in action.get("tasks", [])]

    # ── Add parent task with subtasks ─────────────────────────────────────────
    elif a == "add_task_with_subtasks":
        parent = db.add_task(action.get("task", {}))
        result["task"]     = parent
        result["subtasks"] = [
            db.add_task(s, parent_id=parent["id"])
            for s in action.get("subtasks", [])
        ]

    # ── Task mutations ────────────────────────────────────────────────────────
    elif a in ("complete_task", "uncomplete_task", "delete_task"):
        task_id = _resolve_task(action, tasks)
        if task_id is None:
            result["message"] = (
                "I couldn't find that task. "
                "Try referring to it by number — e.g. 'mark task 2 as done'."
            )
        elif a == "complete_task":
            result["task"] = db.complete_task(task_id)
        elif a == "uncomplete_task":
            result["task"] = db.uncomplete_task(task_id)
        elif a == "delete_task":
            db.delete_task(task_id)

    # ── Undo via AI (catch-all for "please undo" phrased differently) ─────────
    elif a == "undo":
        action_type, _ = db.undo_last()
        if action_type is None:
            result["message"] = "Nothing to undo."

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_task(action: dict, tasks: list[dict]) -> int | None:
    number = action.get("task_number")
    if number and 1 <= int(number) <= len(tasks):
        return tasks[int(number) - 1]["id"]

    keyword = (action.get("task_title") or "").lower().strip()
    if keyword:
        for t in tasks:
            if keyword in t["title"].lower():
                return t["id"]

    return None
