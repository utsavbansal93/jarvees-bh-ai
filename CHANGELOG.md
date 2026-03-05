# Jarvees — Changelog

All notable changes to this project are documented here.
Format: `[Session N — Date] — Short title` followed by details.

---

## [Session 5 — 2026-03-05] — make_subtask, update_priority via chat, drag-and-drop

### Fixed
- **make_subtask was silently a no-op**: AI had no action for nesting tasks — returned `"action":"chat"` and claimed success while writing nothing to the DB. Fixed by adding `make_subtask` action to `SYSTEM_PROMPT`, a new `db.make_subtask(child_id, parent_id)` DB function, `_resolve_by_number()` helper in `main.py`, and the action handler in `POST /api/chat`.
- **update_priority via chat was also a no-op**: Same root cause. Fixed by adding `update_priority` action to `SYSTEM_PROMPT` and routing it to the existing `db.update_task_priority()`.
- **undo_last() didn't handle update_priority**: The action was logged but never reversed. Added `elif action == "update_priority"` branch that restores the original priority from the snapshot.
- **Gemini 503 "model overloaded" crashed instead of cascading**: `_is_gemini_transient_error()` now catches 503/UNAVAILABLE errors and falls through to the next cascade model without blacklisting it (unlike quota errors which do blacklist for the session).

### Added
- **make_subtask AI action**: `{"action":"make_subtask","task_number":5,"parent_number":4}` — nests an existing top-level task under another; clears its sort_order; undo restores original parent_id and sort_order.
- **update_priority AI action**: `{"action":"update_priority","task_number":6,"priority":"high"}` — changes priority of an existing task via natural language.
- **Drag-and-drop task reordering**
  - `sort_order INTEGER` column in `tasks` table (auto-migrated); set on new top-level tasks, NULL for subtasks.
  - `get_active_tasks()` orders by `sort_order` first (NULL falls back to priority/deadline logic — fully backwards compatible).
  - `db.reorder_tasks(ordered_ids)` updates sort_order for all visible tasks.
  - `POST /api/tasks/reorder` endpoint.
  - UI: `⠿` drag handle on every task card; `initDragDrop()` attaches HTML5 DnD listeners after each render; upper/lower-half mouse detection shows `drag-before` / `drag-after` border indicator; optimistic DOM reorder + async persist to server; reverts on error.
  - `undo_last()` delete branch now also restores `sort_order` from snapshot.

---

## [Session 4 — 2026-03-05] — Gemini Cascade, Server-Side History, UI Polish

### Added
- **3-model Gemini cascade** (`chat_handler.py`)
  - Tries models in order: `gemini-3-flash-preview` → `gemini-2.5-flash` → `gemini-2.5-flash-lite`
  - `_failed_gemini_models` set skips quota-exhausted models for the rest of the session
  - `reset_claude_flag()` now also clears `_failed_gemini_models`
  - Returns actual model ID (e.g. `"gemini-3-flash-preview"`) instead of generic `"gemini"`
  - Raises a clear "all models exhausted" exception when entire cascade fails
- **Server-side chat history** (replaces localStorage)
  - `chat_log` SQLite table: `id`, `role`, `text`, `model`, `timestamp`
  - `save_chat_message(role, text, model)` — called on every AI exchange
  - `get_chat_history(limit=100)` — returns messages in chronological order
  - `GET /api/chat/history` endpoint (`main.py`)
  - `POST /api/chat` now persists both user and Jarvees messages to `chat_log`
  - Undo commands are NOT logged (ephemeral UI actions)
  - UI: `loadChatHistory()` fetches from server on page load; renders with session dividers
- **Priority inline editing**
  - `update_task_priority(task_id, priority)` in `database.py` — logs to undo_log before updating
  - `POST /api/tasks/{id}/priority` endpoint with `PriorityUpdate` Pydantic model
  - UI: priority badge is now clickable — opens a floating dropdown with high / medium / low options (colour-dot + label)
  - Dropdown closes on option select, Escape, or click-outside; toast confirms change
- **System architecture diagrams** — `static/diagram.html` (served at `/static/diagram.html`)
  - Diagram 1: Module Map (all components and data flows)
  - Diagram 2: Chat Message Flow (step-by-step from Send to UI update)
  - Rendered with Mermaid.js on a dark-themed page

### Changed
- **Subtask display**: redesigned from mini-cards to an indented bulleted list
  - Brand-accent left border, lightweight checkboxes, duration label inline
  - CSS: `.subtask-list` / `.subtask-item` / `.subtask-check` / `.subtask-title` / `.sub-dur`
- **Model badge labels**: specific names instead of generic "✦ Gemini"
  - `gemini-3-flash-preview` → `✦ Gemini 3`
  - `gemini-2.5-flash` → `✦ Gemini 2.5`
  - `gemini-2.5-flash-lite` → `✦ Gemini 2.5 Lite`
  - New `MODEL_LABELS` constant and `modelCssClass()` helper (maps all Gemini variants to `model-gemini` CSS class)
- **`handleModelState()`**: changed `if (model === 'gemini')` → `if (model !== 'claude')` to correctly show "Switch back to Claude" button for any Gemini model ID
- **15-min auto-retry timer**: changed `_currentModel !== 'gemini'` → `_currentModel === 'claude'` for same reason
- **`loadChatHistory()`**: uses shared `MODEL_LABELS` / `modelCssClass()` instead of local label map

### Fixed
- Chat history not persisting between sessions — localStorage was wiped in Claude Code preview's ephemeral browser context; fixed by moving persistence to SQLite
- `gemini` badge not reflecting specific fallback model used
- "Switch back to Claude" button not appearing for non-`"gemini"` Gemini model IDs

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
