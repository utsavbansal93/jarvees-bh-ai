# CLAUDE.md — Jarvees Project Memory

> This file is the permanent context for building Jarvees. Read it at the start of every session.

---

## What Is Jarvees?

Jarvees is a **personal AI assistant** — a living task management system that learns your rhythm, manages your obligations, and escalates missed tasks until you make a decision. It is not a to-do app. It is the single system where everything lives, gets scheduled, tracked, and followed up on.

**Core promise:** You tell Jarvees what needs to be done. It figures out when, schedules it, and makes sure nothing quietly disappears.

**Personality:** Mostly professional, occasionally witty. It earns the joke — it doesn't force one.

---

## The Problem Being Solved

Google Calendar handles meetings and events. But smaller obligations — errands, work tasks, recurring habits — live in your head or scattered notes. They don't appear against your actual day, so they get missed or silently deprioritized. Jarvees closes this gap.

---

## Tech Stack

| Component       | Tool / Service                     | Purpose                                                         |
|-----------------|------------------------------------|-----------------------------------------------------------------|
| AI Brain        | Claude API (Anthropic)             | Understands task inputs, extracts structure, decides tone       |
| Backend         | Python + FastAPI                   | Receives inputs, talks to AI and Calendar, stores task state    |
| Task Storage    | SQLite (Phase 1) → Supabase later  | Tracks every task, status, due date, escalation level           |
| Calendar        | Google Calendar API                | Reads gaps, writes task blocks, updates/deletes events          |
| Frontend / UI   | Simple web page (Phase 1), Telegram bot (Phase 2) | Where you type to Jarvees            |
| Scheduler       | APScheduler (Python)               | Nightly check: what's done, what's not, trigger escalations     |
| Voice (Phase 3) | Whisper (OpenAI) or iOS Shortcuts  | Voice → text → same backend pipeline                           |
| Alexa (Phase 4) | Alexa Skills Kit                   | "Alexa, tell Jarvees..." → hits backend                        |

**Architecture flow:**

```
YOU (type or speak)
  → CHAT INTERFACE (web or Telegram)
    → JARVEES BACKEND (Python)
      → CLAUDE API (understands language)
      → GOOGLE CALENDAR API (reads/writes schedule)
        → SQLite DB (task state)
```

---

## Phase 1 Goals (Build This First)

1. **Task Parser** — Takes plain-English input, returns structured JSON: `title`, `deadline`, `estimated_duration`, `priority`
2. **Google Calendar Connector** — Reads next 7 days, finds 30-min+ open gaps (8am–7pm), creates calendar events
3. **SQLite Task Store** — Tracks task status, missed count, escalation level
4. **Nightly Escalation Checker** — APScheduler job at 9pm: finds missed tasks, increments count, generates escalation message
5. **Basic Chat UI** — Simple web page to type tasks and see Jarvees responses

Each piece is independently useful. Build in this order.

---

## The Escalation System

This is what separates Jarvees from a basic task app. Tasks do not rot silently.

| Day   | Status           | Action                                                                                     |
|-------|------------------|--------------------------------------------------------------------------------------------|
| Day 0 | Created          | Task scheduled, placed on calendar                                                         |
| Day 1 | Missed once      | Neutral reschedule to next available slot. No drama.                                       |
| Day 2 | Missed twice     | Firm message. Asks: reschedule, delegate, or drop it?                                      |
| Day 3+| Still unresolved | Nagging mode. Will not stop until user makes an explicit decision.                         |

**Tone examples:**
- Day 1: `"Still on your list: 'Call the insurance broker.' Moved it to tomorrow at 10am."`
- Day 2: `"This is the second time 'Call insurance broker' has slipped. Do you want to reschedule, drop it, or should I block 30 minutes right now?"`
- Day 3 (if warranted): `"The insurance broker is starting to feel stood up. What are we doing here?"`

Wit is earned, not forced. Day 3 humor only applies when contextually appropriate.

---

## Smart Scheduling Logic

Jarvees doesn't dump tasks at 9am. It checks:
- Existing calendar events and meetings
- Open blocks of the right duration between 8am–7pm
- Hard deadlines (works backwards 1–2 days)
- Time-of-day preferences / off-limit windows

**Task types:**
- **Quick tasks** (<30 min): Next available morning or lunch gap
- **Deadline tasks**: Scheduled 1–2 days before due date
- **Recurring tasks**: Fixed repeating block at specified time, left alone unless instructed

---

## How You Talk to Jarvees

No special format required. Plain English only.

```
"Remind me to call my accountant before end of month"
→ "Got it. 'Call accountant' added. Blocking 20 minutes on March 28th at 10am. Deadline: March 31st. Sound right?"

"I need to go to the gym every Tuesday and Thursday at 7am"
→ "Done. Recurring gym block added Tuesdays and Thursdays at 7:00am."

"Drop the dentist appointment, I rescheduled it"
→ "Removed. Your Tuesday 2pm is clear."
```

---

## Build Roadmap

| Phase | What Gets Built                                              | Key Win                              |
|-------|--------------------------------------------------------------|--------------------------------------|
| 1     | Task parser + Google Calendar scheduling + nightly escalation | Jarvees manages your calendar        |
| 2     | Telegram bot interface                                       | Fully mobile, mark tasks from phone  |
| 3     | iOS Shortcuts + Siri                                         | Hands-free task capture              |
| 4     | Alexa Skill                                                  | Ambient, always-on input             |
| 5     | Recurring habits, pattern detection, smart priority scoring   | Jarvees starts anticipating needs    |

---

## Conventions and Workflow Rules

- **Always plan before building.** Start important prompts with: `"Walk me through what you're going to build before you start."` This triggers Plan Mode.
- **Review diffs before approving.** Every file change should be inspected before accepting.
- **Paste errors verbatim.** Don't paraphrase — paste the exact error text.
- **End sessions with a summary.** Update this CLAUDE.md with what was built.
- **Secrets go in `.env`.** Never hardcode API keys. Always add `.env` to `.gitignore`.
- **SQLite first.** Don't over-engineer storage in Phase 1. Supabase is for Phase 2+.
- **Keep personality consistent.** Wit is earned, professional is default.

---

## Project Folder

`~/Documents/Claude/AI Agent Jarvees/` — all Jarvees code lives here.

Key files:
| File | Purpose |
|------|---------|
| `CLAUDE.md` | This file — permanent session memory |
| `CHANGELOG.md` | Detailed change log per session |
| `.env` | API keys — never commit |
| `.gitignore` | Excludes `.env`, `*.db`, `__pycache__`, `.claude/` |
| `main.py` | FastAPI entry point — all HTTP routes |
| `database.py` | SQLite task store (tasks + undo_log tables) |
| `chat_handler.py` | Claude → Gemini fallback AI routing |
| `task_parser.py` | Phase 1 CLI parser (standalone, pre-UI) |
| `static/index.html` | Single-page web UI |
| `requirements.txt` | Python dependencies |
| `.claude/launch.json` | Preview server config for Claude Code |
| `tasks.db` | SQLite database (auto-created, never commit) |

---

## Current Tech Decisions (as of Session 3)

| Decision | Chosen Approach |
|----------|----------------|
| AI primary | Claude Sonnet (`claude-sonnet-4-6`) via Anthropic API |
| AI fallback | Gemini 2.0 Flash via `google.genai` SDK (free tier) |
| Fallback trigger | Billing error on Claude → auto-switch; manual reset button + 15-min auto-retry |
| Undo | Zero-cost — intercepted by regex in `main.py` before any AI call |
| Storage | SQLite via Python `sqlite3` (no ORM) |
| Task hierarchy | `parent_id` column; subtasks cascade on complete/delete |
| Chat history | `localStorage` — max 40 msgs, 2 prior sessions, paginated |
| How to start server | `python3 -m uvicorn main:app --reload --port 8000` in Terminal |

## Pending Decisions (No Rush, Needed Before Phase 2)

1. **Where does Jarvees run?** Laptop only, Render/Railway (~$5/mo), or Raspberry Pi at home?
2. **Telegram bot or web UI first?** Telegram is faster to build; web UI is more flexible.
3. **How do you mark a task done on mobile?** Telegram reply? Jarvees web on phone?

---

## Future Modules (Post-Phase 5)

- Email triage — flags inbox items needing action
- Research assistant — returns a brief on a given topic
- Meeting prep — surfaces relevant notes before each call
- Weekly review — Sunday summary of what happened, what slipped, what's coming

---

## Session Log

### Session 1 — Setup + Task Parser
- Blueprint read and CLAUDE.md created
- `.gitignore` and `.env` created (never commit `.env`)
- `task_parser.py` built — parses plain-English → structured JSON via Claude API
  - Schema: `title`, `deadline`, `estimated_duration`, `priority`, `task_type`, `recurrence`
  - Verification layer, batch mode, interactive mode, `--no-verify` flag
- Dependencies installed: `anthropic`, `python-dotenv`

**Phase 1 status:** Task parser ✅ | Calendar connector ⬜ | SQLite store ⬜ | Scheduler ⬜ | Web UI ⬜

---

### Session 2 — Full Web UI + Backend

**Built from scratch:**
- `database.py` — SQLite store with `tasks` and `undo_log` tables
  - `parent_id` column for subtask hierarchy (auto-migrated if missing)
  - `complete_task()` cascades to subtasks; auto-completes parent when all subtasks done
  - `delete_task()` cascades to subtasks
  - `_auto_archive()` runs on every `get_active_tasks()` — moves tasks completed >7 days ago
  - Undo log retains last 20 entries
- `chat_handler.py` — Claude API integration with structured JSON prompt
  - Actions: `add_task`, `add_multiple_tasks`, `add_task_with_subtasks`, `complete_task`, `uncomplete_task`, `delete_task`, `chat`
  - System prompt includes today's date and numbered task list for context
- `main.py` — FastAPI with 7 endpoints: tasks CRUD, undo, chat
- `static/index.html` — Full single-page app
  - Task list with checkboxes, strikethrough, priority/deadline/duration badges
  - Parent + subtask tree view with progress badge (`2/6 done`)
  - Chat window (only way to add tasks)
  - Archive modal (7-day auto-archive, permanent delete)
  - Undo button + ⌘Z shortcut
  - Toast notifications

**Bugs fixed:**
- Python 3.9 `dict | None` syntax → added `from __future__ import annotations` to all backend files
- Multi-task batch error (`'"action"'` KeyError) → added `add_multiple_tasks` action
- Subtask detection: user clarified tasks should be grouped → added `add_task_with_subtasks`

**Phase 1 status:** Task parser ✅ | Calendar connector ⬜ | SQLite store ✅ | Scheduler ⬜ | Web UI ✅

---

### Session 3 — Smart AI Fallback + Chat History + Dev Tooling

**Smart AI fallback (Claude → Gemini):**
- `chat_handler.py`: tries Claude first; on billing error flips `_claude_billing_failed = True` and falls back to Gemini 2.0 Flash for the rest of the session
- `reset_claude_flag()` public function resets the flag (called by `/api/model/reset`)
- **Migrated Gemini SDK:** `google.generativeai` (deprecated) → `google.genai`; model `gemini-1.5-flash` → `gemini-2.0-flash`
- Gemini quota errors now surface a clean "rate limit" message instead of raw API exception
- `main.py` `/api/model/reset` endpoint resets the Claude flag without server restart
- **Undo is zero-cost** — regex intercept in `main.py` before any AI call
- UI: model badge on every Jarvees reply (✦ Claude / ✦ Gemini / System / ⚠ Error)
- UI: "Switch back to Claude" button appears automatically when on Gemini fallback
- UI: 15-minute `setInterval` silently resets flag so next message re-tries Claude

**Chat history (localStorage):**
- Every message persisted to `localStorage` under key `jarvees_history`
- Max 40 messages, max 2 prior sessions (whichever is fewer)
- On load: "📜 N messages from previous session" CTA appears if history exists
- Click to expand/collapse; paginated 10 per page with Prev/Next
- Model badges preserved in history display

**Dev tooling:**
- `.claude/launch.json` created — registers Jarvees Backend as a Claude Code preview server
- Server start command for preview: `python3 -m uvicorn main:app --port 8000 --loop asyncio`
- Note: `uvicorn[standard]` C extensions (uvloop, httptools) are blocked by macOS preview sandbox; run directly from Terminal for development with `--reload`

**Phase 1 status:** Task parser ✅ | Calendar connector ⬜ | SQLite store ✅ | Scheduler ⬜ | Web UI ✅
