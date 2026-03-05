# Jarvees — Backlog

Items here are fully designed but not yet scheduled. Pick up any item by moving it into a session plan in CLAUDE.md.

---

## Error Handling: Request Queue with User-Facing Retry

**Priority:** Medium
**Effort:** ~1 session
**Prerequisites:** None (pure backend + UI feature)

### Problem
When all AI models fail (billing exhausted, quotas hit, 503 storms), the user gets a one-time error message and the request is gone. If the issue resolves in seconds or minutes, the user has to re-type their message from scratch. There is no way to queue a retry or be notified when service resumes.

### Proposed Solution

#### Backend

1. **Request queue table** (`queued_requests` in SQLite):
   ```
   id           INTEGER PRIMARY KEY AUTOINCREMENT
   user_message TEXT    NOT NULL
   queued_at    TEXT    DEFAULT (datetime('now'))
   status       TEXT    DEFAULT 'pending'   -- pending | processing | done | cancelled
   result_json  TEXT                         -- filled when done
   retries      INTEGER DEFAULT 0
   next_retry_at TEXT                        -- ISO timestamp of next attempt
   ```

2. **`POST /api/chat`** — on "all models unavailable" exception:
   - Insert row into `queued_requests`
   - Return `{"action": "queued", "queue_id": 42, "message": "All models busy. I'll retry this in a few minutes — you'll see a notification here."}`

3. **Background retry worker** (APScheduler job, runs every 60 seconds):
   - Fetch all `pending` rows where `next_retry_at <= now()`
   - For each: call `process_message()` — if succeeds, execute the action, set `status = 'done'`, store `result_json`
   - On failure: increment `retries`, compute exponential backoff (`next_retry_at = now + 2^retries minutes`, max 30 min), update row
   - After 5 retries: set `status = 'failed'`, notify via SSE

4. **`DELETE /api/chat/queue/{id}`** — user cancels a queued request

5. **`GET /api/chat/queue`** — returns all pending/done items so the UI can poll

#### Frontend

1. When `action === 'queued'`:
   - Show a special message bubble with an orange "queued" indicator
   - Include a **Cancel** button (calls `DELETE /api/chat/queue/{id}`)
   - Start polling `GET /api/chat/queue` every 10 seconds (or use SSE)

2. When a queued item completes:
   - Show the result as a new Jarvees message in the chat
   - Remove the queued indicator, refresh task list
   - Show a toast: "Your queued request completed ✓"

3. If a queued item fails after all retries:
   - Show an error bubble with the original message quoted, so the user can copy-paste and retry

#### UX Details
- Maximum queue depth: 5 items (reject with message if over limit)
- Queued items persist across page reloads (stored server-side)
- Clear all queued items on `POST /api/model/reset` (manual retry button)
- Do NOT queue undo commands (they're ephemeral and time-sensitive)

### Open Questions
- SSE vs. polling for completion notification? SSE is cleaner but adds server complexity. Polling every 10s is fine for Phase 1.
- Should we re-show the queued message in the chat history on reload? Probably yes — treat it like a regular message with a "⏳ Queued" badge.

---

## Future Items (no design yet)

- **Google Calendar integration** — read gaps, write task blocks (Phase 1 original goal, deferred)
- **Nightly escalation checker** — APScheduler job at 9pm; missed-count increment; escalation messages
- **Telegram bot interface** — Phase 2
- **iOS Shortcuts + Siri** — Phase 3
- **Alexa Skill** — Phase 4
- **Pattern detection** — Jarvees notices recurring slippage and suggests habit-forming changes
- **Email triage** — flags inbox items needing action, creates tasks from them
- **Weekly review** — Sunday summary: what happened, what slipped, what's coming
