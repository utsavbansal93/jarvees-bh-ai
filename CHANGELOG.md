# Jarvees — Changelog

All notable changes to this project are documented here.
Format: `[Session N — Date] — Short title` followed by details.

---

## [Session 6 — 2026-03-05] — Unified cascade, merge/split/move, typing model hint, confirm UI

### Fixed
- **"Priority 1" was changing priority bucket instead of position**: AI had no `move_task_to_position` action, so it fell back to `update_priority` with `priority="high"`. Fixed by adding `move_task_to_position` to `SYSTEM_PROMPT` with an explicit rule: positional intent ("priority 1", "move to top", "make this #2") → `move_task_to_position`; bucket intent ("set to high priority") → `update_priority`.
- **Low-priority tasks were not naturally sinking to bottom**: `add_task()` always assigned explicit `sort_order = max+1`, pinning every new task ahead of lower-priority tasks added earlier. Fixed by removing the auto sort_order block — new tasks have `sort_order = NULL` and sort by priority bucket (high→medium→low) → deadline → created_at. Drag-and-drop and `move_task_to_position` still write explicit sort_order values.
- **Gemini `thought_signature` warning**: `_gemini_response()` was calling `response.text` which triggers `Warning: there are non-text parts in the response: ['thought_signature']` on newer reasoning models. Fixed by iterating `response.candidates[0].content.parts` and explicitly extracting only parts with a `.text` attribute.
- **Any-model failures not being skipped on next request**: Only Claude billing and Gemini quota were blacklisted. 503/transient errors were re-tried on every request. Unified into a single `_failed_models: dict[str, str | float]` with permanent and time-based cooldown modes.

### Added
- **Unified model failure tracking** (`chat_handler.py`)
  - Single `_failed_models: dict[str, str | float]` replaces `_claude_billing_failed` bool + `_failed_gemini_models` set
  - `"permanent"` value = billing/quota failures (skip for entire session until reset)
  - `float` timestamp value = transient 503/overloaded failures (skip for `TRANSIENT_COOLDOWN_SECS = 300` seconds, then auto-eligible again)
  - `_is_model_available(model_id)` checks permanent vs. cooldown expiry
  - `_mark_model_failed(model_id, *, permanent)` records failure type
  - `reset_claude_flag()` now just calls `_failed_models.clear()` — resets everything
- **cascade_log on every response**: `process_message()` returns 3-tuple `(action_dict, model_used, cascade_log)`. Each log entry: `{"model": "...", "failed": bool, "reason": "billing|quota|transient|skipped", "elapsed_s": float}`. Returned in every API response as `cascade_log`.
- **Response time tracking**: `_model_response_times: dict[str, deque(maxlen=20)]` records successful call durations. `get_model_stats()` computes p90, last, and n per model. Exposed at `GET /api/model/stats`.
- **`GET /api/model/status` endpoint**: Returns availability state for every model in the cascade (available, reason, cooldown_remaining).
- **Three new AI task operations** (in `SYSTEM_PROMPT`, `main.py`, `database.py`):
  - `move_task_to_position`: Moves a task to a specific 1-based list position. Assigns explicit sort_order to all top-level tasks. Undo restores the full pre-move sort_order snapshot for every task.
  - `merge_tasks`: Creates a new parent task and makes two existing tasks its subtasks. Inherits higher priority, sums durations. Undo deletes the parent and restores both tasks' original parent_id and sort_order.
  - `split_task`: AI logically decomposes an existing task's title into steps and creates them as subtasks. The original task becomes the parent. Undo deletes the created subtasks.
- **Confirm UI** (`SYSTEM_PROMPT`, `main.py`, `index.html`)
  - AI returns `{"action":"confirm","options":[{"label":"...","action":{...}},...],"message":"Which did you mean?"}` when genuinely uncertain between 2-3 interpretations
  - Frontend renders options as clickable buttons below the message bubble
  - User clicks → `POST /api/chat/confirm` with the pre-formed inner action dict → executes directly, no second AI call
  - `_execute_action()` helper refactored out of `chat()` and reused by both endpoints
- **Loading indicator with model hint + elapsed timer** (`index.html`)
  - Typing indicator now shows which model is expected to be tried (based on `_frontendFailedModels` set)
  - Elapsed time counter ticks every second while waiting
  - `removeTyping()` calls `clearInterval` on the timer before removal
- **Cascade log note on messages** (`index.html`)
  - When multiple models were tried, a small italic line appears below the message: e.g. `✦ Claude ✗ 0.3s → ✦ Gemini 3 ✓ 1.4s`
  - Built by `buildCascadeNote(cascadeLog)` — only shows when there were actual failures (skips clean single-model responses)
- **Frontend cascade state mirroring** (`index.html`)
  - `_frontendFailedModels: Set` updated from `cascade_log` on every response
  - `getNextExpectedModel()` returns first non-failed model for the loading indicator
  - `updateFailedModelsFromLog(cascadeLog)` populates the set from response data
  - Cleared on "Retry with better model" click and on 15-min auto-retry
- **"Retry with better model" button** — renamed from "Switch back to Claude"; now reflects that any failed model may be retried, not just Gemini→Claude.
- **`.claudeignore`** — excludes `tasks.db`, `*.db`, `venv/`, `__pycache__/`, `.claude/`, `*.log` from Claude's reading context.
- **`BACKLOG.md`** — full design spec for the Error Handling / Request Queue feature (queued_requests table, retry worker, cancel button, completion notification).

### Changed
- **`add_task()`** — removed auto `sort_order = max+1` assignment for top-level tasks. New tasks default to `sort_order = NULL`, sorting naturally by priority bucket → deadline → created_at. Explicit ordering still available via drag-and-drop or `move_task_to_position`.
- **`main.py` refactored**: action routing extracted into `_execute_action(action, tasks, model_used, cascade_log) → dict`. Both `POST /api/chat` and `POST /api/chat/confirm` call this helper, eliminating code duplication.
- **Confirm messages** not saved to `chat_log` (they're transient UI state — only the user's selection and the resulting action are persisted).
- **SYSTEM_PROMPT** updated with rule: "when a task has subtasks the parent's `estimated_duration` should be the sum of its subtasks, not an independent estimate" — prevents the parent showing an arbitrary estimate that's lower than a single subtask.

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
