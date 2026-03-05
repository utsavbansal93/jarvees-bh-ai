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


class PriorityUpdate(BaseModel):
    priority: str

@app.post("/api/tasks/{task_id}/priority")
def update_priority(task_id: int, body: PriorityUpdate):
    if body.priority not in ("high", "medium", "low"):
        raise HTTPException(400, "priority must be high, medium, or low")
    task = db.update_task_priority(task_id, body.priority)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


class ReorderRequest(BaseModel):
    order: list[int]

@app.post("/api/tasks/reorder")
def reorder_tasks(body: ReorderRequest):
    """Persist a user-defined drag-and-drop sort order for top-level tasks."""
    db.reorder_tasks(body.order)
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
    Reset all AI failure flags.
    The next chat message will start from Claude again.
    Returns which model is *expected* to be active after reset.
    """
    chat_handler.reset_claude_flag()
    return {"ok": True, "model": "claude"}


@app.get("/api/model/stats")
def model_stats():
    """
    Return per-model p90 response times from the last 20 successful calls.
    Used by the frontend to show estimated wait time in the loading indicator.
    """
    return chat_handler.get_model_stats()


@app.get("/api/model/status")
def model_status():
    """Return availability state for every model in the cascade."""
    return chat_handler.get_failed_model_state()


# ── Chat history ──────────────────────────────────────────────────────────────

@app.get("/api/chat/history")
def get_chat_history():
    """Return the last 100 messages for display on page load."""
    return db.get_chat_history()


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str

class ConfirmRequest(BaseModel):
    action: dict

# Undo phrases that get handled directly without any AI call
_UNDO_RE = re.compile(
    r'^\s*(undo|undo\s+that|undo\s+last(\s+action)?|revert(\s+that)?)\s*$',
    re.IGNORECASE
)


@app.post("/api/chat")
def chat(body: ChatRequest):

    # ── Zero-cost undo shortcut ───────────────────────────────────────────────
    # Intercept obvious undo phrases before they hit any AI model.
    # Undo commands are intentionally NOT saved to chat_log (ephemeral UI action).
    if _UNDO_RE.match(body.message):
        action_type, _ = db.undo_last()
        if action_type is None:
            return {"action": "undo", "message": "Nothing to undo.", "model": "system", "cascade_log": []}
        return {"action": "undo", "message": "Done — last action undone.", "model": "system", "cascade_log": []}

    # ── AI-powered commands ───────────────────────────────────────────────────
    # Persist the user's message before calling the AI.
    db.save_chat_message("user", body.message)

    tasks = db.get_active_tasks()

    try:
        action, model_used, cascade_log = chat_handler.process_message(body.message, tasks)
    except Exception as e:
        err_msg = f"Something went wrong on my end: {e}"
        db.save_chat_message("jarvees", err_msg, "error")
        return {
            "action":      "chat",
            "message":     err_msg,
            "model":       "error",
            "cascade_log": [],
        }

    result = _execute_action(action, tasks, model_used, cascade_log)

    # Persist Jarvees' reply — but not confirm prompts (those are pure UI state)
    if result["action"] != "confirm":
        db.save_chat_message("jarvees", result["message"], model_used)

    return result


@app.post("/api/chat/confirm")
def chat_confirm(body: ConfirmRequest):
    """
    Execute a pre-resolved action that the user selected from a confirm dialog.
    No AI call — the action dict was already fully formed by the AI in a prior request.
    """
    tasks = db.get_active_tasks()
    result = _execute_action(body.action, tasks, "system", [])
    db.save_chat_message("jarvees", result["message"], "system")
    return result


# ── Shared action executor ────────────────────────────────────────────────────

def _execute_action(
    action: dict,
    tasks: list[dict],
    model_used: str,
    cascade_log: list,
) -> dict:
    """
    Route an AI-resolved action dict to the appropriate DB call and return
    the unified result dict (sent straight to the frontend).
    Used by both POST /api/chat and POST /api/chat/confirm.
    """
    a = action.get("action", "chat")
    result: dict = {
        "action":      a,
        "message":     action.get("message", "Done."),
        "model":       model_used,
        "cascade_log": cascade_log,
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

    # ── Make subtask (nest one existing task under another) ───────────────────
    elif a == "make_subtask":
        child_id  = _resolve_by_number(action.get("task_number"), tasks)
        parent_id = _resolve_by_number(action.get("parent_number"), tasks)
        if child_id is None or parent_id is None or child_id == parent_id:
            result["message"] = (
                "I couldn't find those tasks. "
                "Try 'make task 5 a subtask of task 4'."
            )
        else:
            result["task"] = db.make_subtask(child_id, parent_id)

    # ── Update priority bucket (high / medium / low) ──────────────────────────
    elif a == "update_priority":
        task_id  = _resolve_task(action, tasks)
        priority = action.get("priority", "medium")
        if task_id is None:
            result["message"] = "I couldn't find that task. Try 'set task 6 to high priority'."
        elif priority not in ("high", "medium", "low"):
            result["message"] = "Priority must be high, medium, or low."
        else:
            result["task"] = db.update_task_priority(task_id, priority)

    # ── Move task to a position in the list (e.g. "make this #1") ────────────
    elif a == "move_task_to_position":
        task_id  = _resolve_by_number(action.get("task_number"), tasks)
        position = action.get("position")
        if task_id is None:
            result["message"] = "I couldn't find that task. Try 'move task 3 to position 1'."
        elif position is None:
            result["message"] = "Please specify a position number."
        else:
            result["task"] = db.move_task_to_position(task_id, int(position))

    # ── Merge two tasks under a new shared parent ─────────────────────────────
    elif a == "merge_tasks":
        task_id_a    = _resolve_by_number(action.get("task_number_a"), tasks)
        task_id_b    = _resolve_by_number(action.get("task_number_b"), tasks)
        merged_title = action.get("merged_title", "Merged Tasks")
        if task_id_a is None or task_id_b is None:
            result["message"] = "I couldn't find those tasks. Try 'merge task 3 and task 4'."
        elif task_id_a == task_id_b:
            result["message"] = "Those refer to the same task — pick two different ones."
        else:
            result["task"] = db.merge_tasks(task_id_a, task_id_b, merged_title)

    # ── Split an existing task into subtasks ──────────────────────────────────
    elif a == "split_task":
        task_id  = _resolve_by_number(action.get("task_number"), tasks)
        subtasks = action.get("subtasks", [])
        if task_id is None:
            result["message"] = "I couldn't find that task. Try 'split task 3'."
        elif not subtasks:
            result["message"] = "No subtasks were specified for the split."
        else:
            result["task"] = db.split_task(task_id, subtasks)

    # ── Confirm (AI is uncertain — present options to user) ───────────────────
    elif a == "confirm":
        result["options"] = action.get("options", [])

    # ── Undo via AI (catch-all for "please undo" phrased differently) ─────────
    elif a == "undo":
        action_type, _ = db.undo_last()
        if action_type is None:
            result["message"] = "Nothing to undo."

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_by_number(n, tasks: list[dict]) -> int | None:
    """Resolve a 1-based task number directly to a task ID."""
    try:
        n = int(n)
        if 1 <= n <= len(tasks):
            return tasks[n - 1]["id"]
    except (TypeError, ValueError):
        pass
    return None


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
