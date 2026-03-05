# Jarvees — Changelog

All notable changes to this project are documented here.
Format: `[Session N — Date] — Short title` followed by details.

---

## [Session 3 — 2026-03-05] — Smart AI Fallback, Chat History, Dev Tooling

### Added
- **Smart Claude → Gemini fallback** (`chat_handler.py`)
  - Tries Claude (`claude-sonnet-4-6`) first on every request
  - On billing/credit error: flips `_claude_billing_failed = True` and routes to Gemini for the rest of the session
  - `reset_claude_flag()` public function resets the flag without restarting the server
- **`POST /api/model/reset`** endpoint (`main.py`) — resets fallback flag; called by UI button and auto-timer
- **Model badge** on every Jarvees reply in the chat UI
  - `✦ Claude` (purple), `✦ Gemini` (amber), `System` (grey), `⚠ Error` (red)
- **"Switch back to Claude" button** in header — appears automatically when on Gemini fallback; hidden otherwise
- **15-minute auto-retry timer** (`setInterval`) — silently resets Claude flag every 15 min while on Gemini fallback
- **Chat history persistence** via `localStorage`
  - Every message saved with `role`, `text`, `model`, `time`, `sessionId`
  - Max 40 messages retained; max 2 prior sessions
  - "📜 N messages from previous session" CTA above chat on load (only when history exists)
  - Expandable/collapsible; paginated 10 messages per page with Prev/Next
  - Model badges preserved in history view
- **`.claude/launch.json`** — Claude Code preview server config for Jarvees Backend
- **`CHANGELOG.md`** — this file

### Changed
- **Gemini SDK migration**: `google.generativeai` (deprecated) → `google.genai` (new official SDK)
- **Gemini model**: `gemini-1.5-flash` (retired) → `gemini-2.0-flash`
- **`requirements.txt`**: `google-generativeai` → `google-genai`
- **Undo**: now zero-cost — regex-intercepted in `main.py` before reaching any AI model
- **`addMsg(role, text, model)`**: updated to accept optional `model` parameter and render badge
- **Error messages**: Gemini quota/rate-limit errors now show a clean user-friendly message instead of raw API exception dump

### Fixed
- `google.generativeai` deprecation warnings on startup
- `gemini-1.5-flash` 404 error ("model not found for API version v1beta")
- Raw Gemini API exception text leaking into chat bubble on rate-limit errors

---

## [Session 2 — 2026-03-05] — Full Backend + Web UI

### Added
- **`database.py`** — SQLite task store
  - Tables: `tasks`, `undo_log`
  - Columns: `id`, `title`, `deadline`, `estimated_duration`, `priority`, `task_type`, `recurrence`, `status`, `parent_id`, `created_at`, `completed_at`
  - Auto-migration: adds `parent_id` column if missing (backward-compatible)
  - `complete_task()`: cascades completion to subtasks; auto-completes parent when all siblings done
  - `delete_task()`: cascades deletion to all subtasks
  - `_auto_archive()`: runs on every `get_active_tasks()` call; moves tasks completed >7 days ago to `archived` status
  - Undo log: stores last 20 actions with before/after snapshots
- **`chat_handler.py`** — Claude API integration
  - Structured JSON prompt with 6 actions: `add_task`, `add_multiple_tasks`, `add_task_with_subtasks`, `complete_task`, `uncomplete_task`, `delete_task`, `chat`
  - System prompt injects today's date and numbered task list for context
  - Smart duration defaults in prompt (errand=20, call=15, gym=60, etc.)
- **`main.py`** — FastAPI backend
  - `GET /` — serves `static/index.html`
  - `GET /api/tasks` — active task list
  - `GET /api/tasks/archived` — archived tasks
  - `POST /api/tasks/{id}/complete` — mark done
  - `POST /api/tasks/{id}/uncomplete` — unmark done
  - `DELETE /api/tasks/{id}` — delete task
  - `POST /api/undo` — undo last action
  - `GET /api/undo/available` — check if undo is possible
  - `POST /api/chat` — AI-powered natural language command
- **`static/index.html`** — full single-page web app
  - Left panel: numbered task list with checkbox, priority/deadline/duration badges, done strikethrough
  - Task groups: parent card + indented subtask list + `X/N done` progress badge
  - Right panel: chat window (only way to interact with Jarvees)
  - Archive modal with permanent delete
  - Undo button + ⌘Z/Ctrl+Z keyboard shortcut
  - Toast notifications
  - Auto-expanding textarea
- **`requirements.txt`** with: `fastapi`, `uvicorn[standard]`, `anthropic`, `python-dotenv`, `google-generativeai`
- **`start.sh`** — convenience script to install deps and launch server

### Fixed
- Python 3.9 `dict | None` type hint syntax — added `from __future__ import annotations` to `database.py`, `main.py`, `chat_handler.py`
- Multi-task batch error (`'"action"'` KeyError) — added `add_multiple_tasks` action to prompt and handler
- Subtask grouping — added `add_task_with_subtasks` action and tree rendering in UI

---

## [Session 1 — 2026-03-05] — Project Setup + Task Parser

### Added
- **Blueprint** read (`Jarvees_Blueprint_v1.docx`) and project architecture documented
- **`CLAUDE.md`** — permanent session memory file
- **`.env`** — API keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_CALENDAR_CREDENTIALS_FILE`)
- **`.gitignore`** — excludes `.env`, `*.db`, `__pycache__`, `*.pyc`, `.DS_Store`
- **`task_parser.py`** — standalone CLI task parser
  - Parses plain-English → structured JSON via Claude API
  - Schema: `title`, `deadline`, `estimated_duration`, `priority`, `task_type`, `recurrence`
  - Interactive mode (no args), batch mode (`"task1" "task2"` or `--file tasks.txt`)
  - Verification layer: shows parsed result, lets user confirm or edit field-by-field
  - `--no-verify` flag to skip confirmation
  - `load_dotenv(override=False)` to avoid overriding real env vars
- Dependencies installed: `anthropic`, `python-dotenv`
