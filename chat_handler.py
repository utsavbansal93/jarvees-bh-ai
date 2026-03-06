"""
Jarvees — Chat Command Handler

Model cascade (best to fallback):
  claude-sonnet-4-6 → gemini-3.1-flash-lite-preview → gemini-3-flash-preview → gemini-2.5-pro → gemini-2.5-flash → gemini-2.5-flash-lite → gemini-2.5-flash-lite-preview-09-2025 → gemini-2.0-flash

Any model that fails is tracked in _failed_models:
  - Permanent failures (billing / quota): skipped for the entire session until reset.
  - Transient failures (503 / overloaded): skipped for TRANSIENT_COOLDOWN_SECS (5 min).

Reset triggers:
  - Manual "Retry with better model" button → /api/model/reset
  - 15-min auto-retry timer in the UI
  - Server restart

Undo commands are intercepted in main.py BEFORE reaching this module — zero AI cost.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import date

import anthropic
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(override=False)

# ── State ─────────────────────────────────────────────────────────────────────

_anthropic_client: anthropic.Anthropic | None = None

# Unified failure tracking.
# key   = model ID (e.g. "claude", "gemini-3-flash-preview")
# value = "permanent"  (billing / quota — never retry until reset)
#       | float         (Unix timestamp of transient failure — retry after cooldown)
_failed_models: dict[str, str | float] = {}

# Per-model response time samples (last 20 successes each).
# Used to compute p90 and show estimated wait time in the UI.
_model_response_times: dict[str, deque] = {}

TRANSIENT_COOLDOWN_SECS = 300   # 5 minutes

# ── Model cascade order ────────────────────────────────────────────────────────

GEMINI_CASCADE = [
    "gemini-3.1-flash-lite-preview",        # newest lite — fast, low cost
    "gemini-3-flash-preview",               # Gemini 3 full flash
    "gemini-2.5-pro",                       # most capable 2.5 model
    "gemini-2.5-flash",                     # balanced speed and intelligence
    "gemini-2.5-flash-lite",                # lightweight 2.5
    "gemini-2.5-flash-lite-preview-09-2025",# preview variant with extended quota
    "gemini-2.0-flash",                     # stable fallback
]

CASCADE = ["claude"] + GEMINI_CASCADE  # full ordered cascade including Claude


# ── Availability helpers ───────────────────────────────────────────────────────

def _is_model_available(model_id: str) -> bool:
    """True if the model has not failed, or its transient cooldown has expired."""
    val = _failed_models.get(model_id)
    if val is None:
        return True
    if val == "permanent":
        return False
    # Transient: available again after TRANSIENT_COOLDOWN_SECS
    return (time.time() - val) >= TRANSIENT_COOLDOWN_SECS


def _mark_model_failed(model_id: str, *, permanent: bool) -> None:
    """Record a model failure. permanent=True for billing/quota; False for 503."""
    _failed_models[model_id] = "permanent" if permanent else time.time()


def _record_response_time(model_id: str, elapsed_s: float) -> None:
    q = _model_response_times.setdefault(model_id, deque(maxlen=20))
    q.append(elapsed_s)


# ── Clients ───────────────────────────────────────────────────────────────────

def _anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def _gemini_response(full_prompt: str, model: str) -> str:
    """Call a Gemini model and return raw text, explicitly filtering text parts
    to avoid 'non-text parts: thought_signature' SDK warnings."""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    response = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT.format(today=date.today().isoformat()),
        ),
    )
    # Explicitly extract text parts — avoids SDK warning when thought_signature
    # parts are present (newer Gemini models with built-in reasoning).
    text_parts = [
        part.text
        for candidate in response.candidates
        for part in candidate.content.parts
        if hasattr(part, "text") and part.text
    ]
    if not text_parts:
        raise ValueError(f"No text content returned by {model}")
    return " ".join(text_parts).strip()


# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Jarvees, a personal AI task assistant. You process natural-language commands and return a structured JSON action.

Today's date: {today}

The user has a numbered task list shown below. They may reference tasks by:
- Number: "task 3", "#2", "the third one"
- Title keyword: "the gym task", "the accountant one"

Return ONLY a valid JSON object. Choose the right action:

── SINGLE TASK ──
{{"action":"add_task","task":{{"title":"...","deadline":"YYYY-MM-DD or null","estimated_duration":20,"priority":"high|medium|low","task_type":"quick|deadline|recurring","recurrence":"pattern or null"}},"message":"..."}}

── MULTIPLE TASKS — flat list (unrelated tasks in one message) ──
{{"action":"add_multiple_tasks","tasks":[{{"title":"...","deadline":null,"estimated_duration":20,"priority":"medium","task_type":"quick","recurrence":null}}, ...],"message":"Added N tasks."}}

── TASK WITH SUBTASKS (one main goal broken into steps) ──
{{"action":"add_task_with_subtasks","task":{{"title":"...","deadline":null,"estimated_duration":20,"priority":"medium","task_type":"quick","recurrence":null}},"subtasks":[{{"title":"...","estimated_duration":10,"priority":"medium","task_type":"quick","deadline":null,"recurrence":null}}, ...],"message":"Added '...' with N subtasks."}}

── COMPLETE / UNCOMPLETE / DELETE ──
{{"action":"complete_task","task_number":2,"message":"..."}}
{{"action":"uncomplete_task","task_number":2,"message":"..."}}
{{"action":"delete_task","task_title":"keyword","message":"..."}}

── MAKE SUBTASK (nest an existing task under another existing task) ──
{{"action":"make_subtask","task_number":5,"parent_number":4,"message":"'Create agreement' is now a subtask of 'Check on partnership'."}}

── UPDATE PRIORITY (set priority bucket: high / medium / low) ──
{{"action":"update_priority","task_number":6,"priority":"high","message":"'Call Shantanu' is now high priority."}}

── MOVE TASK TO POSITION (e.g. "priority 1", "move to top", "make this #2") ──
{{"action":"move_task_to_position","task_number":6,"position":1,"message":"Moved '...' to position 1 in your list."}}

── MERGE TASKS (combine two tasks under a new shared parent) ──
{{"action":"merge_tasks","task_number_a":3,"task_number_b":4,"merged_title":"Partnership work","message":"Merged tasks 3 and 4 under 'Partnership work'."}}

── SPLIT TASK (break an existing task into subtasks) ──
{{"action":"split_task","task_number":5,"subtasks":[{{"title":"Step 1","estimated_duration":30}},{{"title":"Step 2","estimated_duration":20}}],"message":"Split '...' into 2 subtasks."}}

── CONFIRM (when request is genuinely ambiguous — present 2-3 options) ──
{{"action":"confirm","options":[{{"label":"Option A description","action":{{"action":"add_task","task":{{...}},"message":"..."}}}},{{"label":"Option B description","action":{{"action":"delete_task","task_number":2,"message":"..."}}}}],"message":"Which did you mean?"}}

── INFORMATIONAL (no list change) ──
{{"action":"chat","message":"..."}}

Rules:
- Use add_task_with_subtasks when the user describes ONE goal that has multiple steps/components
- Use add_multiple_tasks when the user lists several UNRELATED tasks together
- Use add_task for a single standalone task
- Use make_subtask when the user says "make X a subtask/subsection of Y" or "nest X under Y" — task_number is the child, parent_number is the parent
- Use update_priority when the user says to change the priority bucket of an existing task (high/medium/low)
- Use move_task_to_position when the user says "priority 1", "move to top", "make task N the first task", "move to position N", or any positional reordering intent — NOT for priority bucket changes
- Use merge_tasks when the user says "merge X and Y", "combine task X with task Y", or "group these two together"
- Use split_task when the user says "break task X into parts", "split task X", or "give task X subtasks" — the AI should logically decompose the task title into meaningful steps
- Use confirm when the user's intent is genuinely ambiguous between 2-3 interpretations — present labeled options
- Smart duration defaults: errand=20, call=15, gym=60, meeting=30, report=90, email=10; when a task has subtasks the parent's estimated_duration should be the sum of its subtasks, not an independent estimate
- Priority default: medium unless context implies urgency
- Resolve relative dates (today, Friday, end of month) against today: {today}
- Return ONLY the JSON — no markdown, no explanation
"""


# ── Core parser ───────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    """Strip markdown fences if present and parse JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
    return json.loads(raw)


def _is_billing_error(e: Exception) -> bool:
    return "credit balance" in str(e).lower() or "insufficient" in str(e).lower()


def _is_quota_error(e: Exception) -> bool:
    """Permanent session-level rate/quota limit."""
    s = str(e).lower()
    return "quota" in s or "resource exhausted" in s or "rate limit" in s or "429" in s


def _is_transient_error(e: Exception) -> bool:
    """Temporary service unavailability — cooldown, not permanent blacklist."""
    s = str(e).lower()
    return "503" in s or "unavailable" in s or "overloaded" in s or "service unavailable" in s


# ── Public API ────────────────────────────────────────────────────────────────

def reset_claude_flag() -> None:
    """
    Reset all AI failure flags so the next request starts from Claude again.
    Called by the /api/model/reset endpoint (manual button or 15-min auto-retry).
    """
    _failed_models.clear()


def get_model_stats() -> dict:
    """
    Return per-model p90 response times from the last 20 successful calls.
    Used by the frontend to show estimated wait time in the loading indicator.
    """
    result = {}
    for model_id, times in _model_response_times.items():
        if not times:
            continue
        sorted_t = sorted(times)
        n = len(sorted_t)
        p90_idx = min(int(n * 0.9), n - 1)
        result[model_id] = {
            "p90_s":  round(sorted_t[p90_idx], 1),
            "n":      n,
            "last_s": round(sorted_t[-1], 1),
        }
    return result


def get_failed_model_state() -> dict:
    """
    Return current state of all models in the cascade (for /api/model/status).
    """
    state = {}
    for model_id in CASCADE:
        val = _failed_models.get(model_id)
        if val is None:
            state[model_id] = {"available": True}
        elif val == "permanent":
            state[model_id] = {"available": False, "reason": "permanent", "cooldown_remaining": None}
        else:
            remaining = max(0, TRANSIENT_COOLDOWN_SECS - (time.time() - val))
            state[model_id] = {
                "available":          remaining <= 0,
                "reason":             "transient",
                "cooldown_remaining": int(remaining),
            }
    return state


def process_message(user_message: str, current_tasks: list[dict]) -> tuple[dict, str, list]:
    """
    Parse a natural-language command and return (action_dict, model_used, cascade_log).

    cascade_log is a list of dicts, one per model attempted:
      {"model": "claude", "failed": True, "reason": "billing", "elapsed_s": 0.3}
      {"model": "gemini-3-flash-preview", "failed": False, "elapsed_s": 1.4}
    """
    # Build task context
    if current_tasks:
        lines = []
        for i, t in enumerate(current_tasks, 1):
            deadline = f", due {t['deadline']}" if t.get("deadline") else ""
            status   = " [done]" if t["status"] == "completed" else ""
            lines.append(f"{i}. {t['title']}{deadline}{status}")
        task_context = "\n\nCurrent task list:\n" + "\n".join(lines)
    else:
        task_context = "\n\nCurrent task list: (empty)"

    full_user_content = user_message + task_context

    cascade_log: list[dict] = []

    for model_id in CASCADE:
        if not _is_model_available(model_id):
            # Already failed this session — skip silently
            cascade_log.append({"model": model_id, "failed": True, "reason": "skipped", "elapsed_s": 0})
            continue

        t0 = time.perf_counter()
        try:
            if model_id == "claude":
                response = _anthropic().messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=1500,
                    system=SYSTEM_PROMPT.format(today=date.today().isoformat()),
                    messages=[{"role": "user", "content": full_user_content}],
                )
                raw = response.content[0].text
            else:
                raw = _gemini_response(full_user_content, model_id)

            elapsed = round(time.perf_counter() - t0, 1)
            _record_response_time(model_id, elapsed)
            cascade_log.append({"model": model_id, "failed": False, "elapsed_s": elapsed})
            return _parse_json(raw), model_id, cascade_log

        except Exception as e:
            elapsed = round(time.perf_counter() - t0, 1)
            if _is_billing_error(e):
                reason = "billing"
                _mark_model_failed(model_id, permanent=True)
                print(f"[Jarvees] {model_id} billing limit hit — cascading.")
            elif _is_quota_error(e):
                reason = "quota"
                _mark_model_failed(model_id, permanent=True)
                print(f"[Jarvees] {model_id} quota exhausted — cascading.")
            elif _is_transient_error(e):
                reason = "transient"
                _mark_model_failed(model_id, permanent=False)
                print(f"[Jarvees] {model_id} temporarily unavailable (503) — cascading.")
            else:
                # Unexpected error — don't cascade silently, surface it
                cascade_log.append({"model": model_id, "failed": True, "reason": "error", "elapsed_s": elapsed})
                raise

            cascade_log.append({"model": model_id, "failed": True, "reason": reason, "elapsed_s": elapsed})
            continue

    raise Exception(
        "All AI models are currently unavailable — billing limits and/or quotas exhausted on "
        "every model in the cascade. Please wait for cooldowns to expire or top up credits."
    )


# Backward-compat alias used by tests / any external callers
def process_chat(user_message: str, current_tasks: list[dict]) -> tuple[dict, str]:
    action, model, _ = process_message(user_message, current_tasks)
    return action, model
