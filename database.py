"""
Jarvees — SQLite Task Store
All reads/writes to tasks.db go through this module.
"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "tasks.db"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                role      TEXT NOT NULL,
                text      TEXT NOT NULL,
                model     TEXT,
                timestamp TEXT DEFAULT (datetime('now'))
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                title             TEXT    NOT NULL,
                deadline          TEXT,
                estimated_duration INTEGER DEFAULT 20,
                priority          TEXT    DEFAULT 'medium',
                task_type         TEXT    DEFAULT 'quick',
                recurrence        TEXT,
                status            TEXT    DEFAULT 'active',
                completed_at      TEXT,
                created_at        TEXT    DEFAULT (datetime('now')),
                missed_count      INTEGER DEFAULT 0,
                escalation_level  INTEGER DEFAULT 0,
                calendar_event_id TEXT,
                parent_id         INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS undo_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type   TEXT NOT NULL,
                task_snapshot TEXT NOT NULL,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migrate existing DBs
        existing = [r[1] for r in c.execute("PRAGMA table_info(tasks)").fetchall()]
        if "parent_id" not in existing:
            c.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER")
        if "sort_order" not in existing:
            c.execute("ALTER TABLE tasks ADD COLUMN sort_order INTEGER")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _auto_archive(c):
    """Archive tasks completed more than 7 days ago."""
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    c.execute("""
        UPDATE tasks SET status = 'archived'
        WHERE status = 'completed' AND completed_at <= ?
    """, (cutoff,))


def _log(c, action_type: str, task: dict):
    """Append to undo_log; keep only the last 20 entries."""
    c.execute(
        "INSERT INTO undo_log (action_type, task_snapshot) VALUES (?, ?)",
        (action_type, json.dumps(task))
    )
    c.execute("""
        DELETE FROM undo_log WHERE id NOT IN (
            SELECT id FROM undo_log ORDER BY id DESC LIMIT 20
        )
    """)


def _row(c, task_id: int) -> dict | None:
    r = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(r) if r else None


# ── Reads ─────────────────────────────────────────────────────────────────────

def get_active_tasks() -> list[dict]:
    with _conn() as c:
        _auto_archive(c)
        rows = c.execute("""
            SELECT * FROM tasks
            WHERE status IN ('active', 'completed')
            ORDER BY
                CASE WHEN sort_order IS NOT NULL THEN sort_order ELSE 999999 END,
                CASE status WHEN 'active' THEN 0 ELSE 1 END,
                CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                (deadline IS NULL),
                deadline ASC,
                created_at ASC
        """).fetchall()
    return [dict(r) for r in rows]


def get_archived_tasks() -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT * FROM tasks WHERE status = 'archived'
            ORDER BY completed_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def has_undo() -> bool:
    with _conn() as c:
        row = c.execute("SELECT id FROM undo_log ORDER BY id DESC LIMIT 1").fetchone()
    return row is not None


# ── Writes ────────────────────────────────────────────────────────────────────

def add_task(task: dict, parent_id: int | None = None) -> dict:
    with _conn() as c:
        # Top-level tasks get a sort_order appended to the end; subtasks don't need one
        sort_order = None
        if not parent_id:
            max_order = c.execute(
                "SELECT COALESCE(MAX(sort_order), 0) FROM tasks WHERE parent_id IS NULL"
            ).fetchone()[0]
            sort_order = max_order + 1

        cur = c.execute("""
            INSERT INTO tasks
                (title, deadline, estimated_duration, priority, task_type,
                 recurrence, parent_id, sort_order)
            VALUES
                (:title, :deadline, :estimated_duration, :priority, :task_type,
                 :recurrence, :parent_id, :sort_order)
        """, {
            "title":              task.get("title", "Untitled"),
            "deadline":           task.get("deadline"),
            "estimated_duration": task.get("estimated_duration", 20),
            "priority":           task.get("priority", "medium"),
            "task_type":          task.get("task_type", "quick"),
            "recurrence":         task.get("recurrence"),
            "parent_id":          parent_id,
            "sort_order":         sort_order,
        })
        new = _row(c, cur.lastrowid)
        _log(c, "add", new)
    return new


def complete_task(task_id: int) -> dict | None:
    with _conn() as c:
        before = _row(c, task_id)
        if not before:
            return None
        _log(c, "complete", before)
        now = datetime.now().isoformat()
        c.execute(
            "UPDATE tasks SET status='completed', completed_at=? WHERE id=?",
            (now, task_id)
        )
        # If this is a parent task, complete all its subtasks too
        c.execute(
            "UPDATE tasks SET status='completed', completed_at=? WHERE parent_id=? AND status='active'",
            (now, task_id)
        )
        # If this is a subtask, check if all siblings are done → auto-complete parent
        parent_id = before.get("parent_id")
        if parent_id:
            remaining = c.execute(
                "SELECT COUNT(*) FROM tasks WHERE parent_id=? AND status='active'",
                (parent_id,)
            ).fetchone()[0]
            if remaining == 0:
                c.execute(
                    "UPDATE tasks SET status='completed', completed_at=? WHERE id=? AND status='active'",
                    (now, parent_id)
                )
        return _row(c, task_id)


def uncomplete_task(task_id: int) -> dict | None:
    with _conn() as c:
        before = _row(c, task_id)
        if not before:
            return None
        _log(c, "uncomplete", before)
        c.execute(
            "UPDATE tasks SET status='active', completed_at=NULL WHERE id=?",
            (task_id,)
        )
        return _row(c, task_id)


def delete_task(task_id: int) -> bool:
    with _conn() as c:
        before = _row(c, task_id)
        if not before:
            return False
        _log(c, "delete", before)
        # Delete all subtasks first, then the task itself
        c.execute("DELETE FROM tasks WHERE parent_id=?", (task_id,))
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return True


# ── Chat log ──────────────────────────────────────────────────────────────────

def save_chat_message(role: str, text: str, model: str | None = None) -> None:
    """Persist a single chat message (user or jarvees) to the chat_log table."""
    with _conn() as c:
        c.execute(
            "INSERT INTO chat_log (role, text, model) VALUES (?, ?, ?)",
            (role, text, model)
        )


def update_task_priority(task_id: int, priority: str) -> dict | None:
    """Change the priority of a task and log it for undo."""
    with _conn() as c:
        before = _row(c, task_id)
        if not before:
            return None
        _log(c, "update_priority", before)
        c.execute("UPDATE tasks SET priority=? WHERE id=?", (priority, task_id))
        return _row(c, task_id)


def get_chat_history(limit: int = 100) -> list[dict]:
    """Return the last `limit` messages in chronological order."""
    with _conn() as c:
        rows = c.execute(
            "SELECT role, text, model, timestamp FROM chat_log ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def undo_last() -> tuple[str, dict] | tuple[None, None]:
    """Undo the last logged action. Returns (action_type, snapshot) or (None, None)."""
    with _conn() as c:
        log = c.execute(
            "SELECT * FROM undo_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not log:
            return None, None

        action   = log["action_type"]
        snapshot = json.loads(log["task_snapshot"])
        tid      = snapshot["id"]

        if action == "add":
            # Undo an add → delete the task
            c.execute("DELETE FROM tasks WHERE id=?", (tid,))

        elif action == "complete":
            # Undo a completion → restore to active
            c.execute(
                "UPDATE tasks SET status='active', completed_at=NULL WHERE id=?",
                (tid,)
            )

        elif action == "uncomplete":
            # Undo an uncomplete → restore to completed
            c.execute(
                "UPDATE tasks SET status='completed', completed_at=? WHERE id=?",
                (snapshot.get("completed_at"), tid)
            )

        elif action == "delete":
            # Undo a delete → restore the full row (only if id is free)
            if not c.execute("SELECT id FROM tasks WHERE id=?", (tid,)).fetchone():
                c.execute("""
                    INSERT INTO tasks
                        (id, title, deadline, estimated_duration, priority, task_type,
                         recurrence, status, completed_at, created_at,
                         missed_count, escalation_level, calendar_event_id, parent_id, sort_order)
                    VALUES
                        (:id, :title, :deadline, :estimated_duration, :priority, :task_type,
                         :recurrence, :status, :completed_at, :created_at,
                         :missed_count, :escalation_level, :calendar_event_id, :parent_id, :sort_order)
                """, {**snapshot,
                      "parent_id":  snapshot.get("parent_id"),
                      "sort_order": snapshot.get("sort_order")})

        elif action == "update_priority":
            # Undo a priority change → restore the original priority
            c.execute(
                "UPDATE tasks SET priority=? WHERE id=?",
                (snapshot.get("priority"), tid)
            )

        elif action == "make_subtask":
            # Undo make_subtask → restore original parent_id and sort_order
            c.execute(
                "UPDATE tasks SET parent_id=?, sort_order=? WHERE id=?",
                (snapshot.get("parent_id"), snapshot.get("sort_order"), tid)
            )

        c.execute("DELETE FROM undo_log WHERE id=?", (log["id"],))

    return action, snapshot


def make_subtask(child_id: int, parent_id: int) -> dict | None:
    """Nest an existing top-level task under a parent. Clears its sort_order."""
    with _conn() as c:
        before = _row(c, child_id)
        if not before:
            return None
        _log(c, "make_subtask", before)
        c.execute(
            "UPDATE tasks SET parent_id=?, sort_order=NULL WHERE id=?",
            (parent_id, child_id)
        )
        return _row(c, child_id)


def reorder_tasks(ordered_ids: list[int]) -> None:
    """Persist a user-defined display order for top-level tasks."""
    with _conn() as c:
        for pos, task_id in enumerate(ordered_ids, start=1):
            c.execute("UPDATE tasks SET sort_order=? WHERE id=?", (pos, task_id))
