"""
Jarvees — FastAPI Backend
Run with: uvicorn main:app --reload --port 8000
Then open: http://localhost:8000
"""

from __future__ import annotations
import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
import chat_handler


# ── Background queue retry worker ─────────────────────────────────────────────

async def _process_queue() -> None:
    """Retry every pending queue item whose next_retry_at has passed."""
    items = db.get_due_queue_items()
    for item in items:
        db.mark_queue_processing(item["id"])
        try:
            tasks = db.get_active_tasks()
            loop = asyncio.get_event_loop()
            action, model_used, cascade_log = await loop.run_in_executor(
                None,
                lambda msg=item["user_message"]: chat_handler.process_message(msg, tasks),
            )
            result = _execute_action(action, tasks, model_used, cascade_log)
            db.save_chat_message("jarvees", result["message"], model_used)
            db.complete_queue_item(item["id"], json.dumps(result))
        except Exception as e:
            retries = item["retries"] + 1
            if retries >= db.MAX_RETRIES:
                db.fail_queue_item(item["id"], str(e))
            else:
                delay_mins = min(2 ** retries, 30)
                next_retry = (datetime.utcnow() + timedelta(minutes=delay_mins)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                db.requeue_item(item["id"], retries, next_retry)


async def _queue_retry_worker() -> None:
    while True:
        await asyncio.sleep(60)
        await _process_queue()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    task = asyncio.create_task(_queue_retry_worker())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Jarvees", lifespan=lifespan)
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
    Reset all AI failure flags and clear the request queue.
    The next chat message will start from Claude again.
    """
    chat_handler.reset_claude_flag()
    db.clear_queue()
    return {"ok": True, "model": "claude"}


# ── Request queue ──────────────────────────────────────────────────────────────

@app.get("/api/chat/queue")
def get_queue():
    """Poll for queue status — frontend calls this every 10s."""
    return db.get_all_queue_items()


@app.delete("/api/chat/queue/{queue_id}")
def cancel_queue_item(queue_id: int):
    """Cancel a queued request the user no longer wants."""
    cancelled = db.cancel_queue_item(queue_id)
    if not cancelled:
        raise HTTPException(404, "Queue item not found or already processed")
    return {"ok": True}


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


# ── Feature requests ───────────────────────────────────────────────────────────

class FeatureRequestBody(BaseModel):
    capability: str
    user_example: str

@app.post("/api/feature-request")
def add_feature_request(body: FeatureRequestBody):
    """
    Append a user feature request to FEATURE_REQUESTS.md.
    Claude Code reads this file in future sessions to create GitHub issues.
    """
    filepath = "FEATURE_REQUESTS.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not os.path.exists(filepath):
        with open(filepath, "w") as f:
            f.write(
                "# Jarvees — User Feature Requests\n\n"
                "Auto-logged when users request capabilities that don't exist yet.\n"
                "Read this file to create GitHub issues for the most-requested features.\n\n"
                "---\n"
            )

    entry = (
        f"\n### [{timestamp}] {body.capability}\n"
        f"**User said:** \"{body.user_example}\"\n"
        f"**Status:** open\n\n"
        f"---\n"
    )
    with open(filepath, "a") as f:
        f.write(entry)

    return {
        "ok": True,
        "message": f"Logged. I've added '{body.capability}' to the feature backlog — we'll pick it up in a future session.",
    }


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
        # ── All models unavailable → queue the request for retry ─────────────
        if "all ai models" in str(e).lower():
            if db.get_queue_depth() >= 5:
                msg = (
                    "All AI models are unavailable and the queue is full (5 items). "
                    "Please wait a few minutes and try again."
                )
                db.save_chat_message("jarvees", msg, "error")
                return {"action": "chat", "message": msg, "model": "error", "cascade_log": []}
            queue_id = db.queue_request(body.message)
            msg = (
                "All AI models are currently unavailable. "
                "I've queued your request and will retry automatically — "
                "you'll see the result here as soon as one comes back online."
            )
            return {
                "action":    "queued",
                "queue_id":  queue_id,
                "message":   msg,
                "model":     "system",
                "cascade_log": [],
            }
        # ── Any other unexpected error → surface it immediately ───────────────
        err_msg = f"Something went wrong on my end: {e}"
        db.save_chat_message("jarvees", err_msg, "error")
        return {
            "action":      "chat",
            "message":     err_msg,
            "model":       "error",
            "cascade_log": [],
        }

    result = _execute_action(action, tasks, model_used, cascade_log)

    # Persist Jarvees' reply — skip transient UI-state actions
    if result["action"] not in ("confirm", "feature_request"):
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

    # ── Feature request (capability doesn't exist yet) ────────────────────────
    elif a == "feature_request":
        result["capability"]   = action.get("capability", "")
        result["user_example"] = action.get("user_example", "")

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
