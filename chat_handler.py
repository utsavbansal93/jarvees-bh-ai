"""
Jarvees — Chat Command Handler

Routing logic:
  1. Claude (claude-sonnet-4-6) is tried first — best quality.
  2. If Claude returns a billing/balance error, flip _claude_billing_failed=True
     and work through the Gemini cascade in order:
       a. gemini-3-flash-preview  (newest — best quality)
       b. gemini-2.5-flash        (balanced speed + intelligence)
       c. gemini-2.5-flash-lite   (most generous free-tier limits)
  3. Each Gemini model that hits a quota/rate-limit is added to
     _failed_gemini_models and the next model in the cascade is tried.
  4. reset_claude_flag() clears both state vars — called by /api/model/reset
     (manual "Switch back to Claude" button or the 15-min auto-retry timer).

Undo commands are intercepted in main.py BEFORE reaching this module — zero AI cost.
"""

from __future__ import annotations

import json
import os
from datetime import date

import anthropic
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(override=False)

# ── State ─────────────────────────────────────────────────────────────────────

_anthropic_client: anthropic.Anthropic | None = None
_claude_billing_failed: bool = False    # flips True on first billing error
_failed_gemini_models: set = set()      # models that hit quota this session

# ── Gemini model cascade ──────────────────────────────────────────────────────
# Tried in order when Claude is unavailable. First model to succeed wins.
# Models that hit their free-tier quota are skipped for the rest of the session.
# All flags reset on server restart or when /api/model/reset is called.

GEMINI_CASCADE = [
    "gemini-3-flash-preview",   # newest — best quality, may have preview instability
    "gemini-2.5-flash",         # balanced speed and intelligence
    "gemini-2.5-flash-lite",    # most generous free-tier limits
]


# ── Clients ───────────────────────────────────────────────────────────────────

def _anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def _gemini_response(full_prompt: str, model: str) -> str:
    """Call a Gemini model (google.genai SDK) and return raw text."""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    response = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT.format(today=date.today().isoformat()),
        ),
    )
    return response.text.strip()


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

── INFORMATIONAL (no list change) ──
{{"action":"chat","message":"..."}}

Rules:
- Use add_task_with_subtasks when the user describes ONE goal that has multiple steps/components
- Use add_multiple_tasks when the user lists several UNRELATED tasks together
- Use add_task for a single standalone task
- Smart duration defaults: errand=20, call=15, gym=60, meeting=30, report=90, email=10
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


def _is_gemini_quota_error(e: Exception) -> bool:
    s = str(e).lower()
    return "quota" in s or "resource exhausted" in s or "rate limit" in s or "429" in s


# ── Public API ────────────────────────────────────────────────────────────────

def reset_claude_flag() -> None:
    """
    Reset all AI failure flags so the next request tries Claude again.
    Also clears the Gemini quota-hit set so the full cascade is available.
    Called by the /api/model/reset endpoint (manual button or 15-min auto-retry).
    """
    global _claude_billing_failed, _failed_gemini_models
    _claude_billing_failed = False
    _failed_gemini_models = set()


def process_chat(user_message: str, current_tasks: list[dict]) -> tuple[dict, str]:
    """
    Parse a natural-language command and return (action_dict, model_used).
    model_used is one of: "claude" | "gemini"
    """
    global _claude_billing_failed, _failed_gemini_models

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

    # ── Try Claude first (unless already known to be out of credits) ──────────
    if not _claude_billing_failed:
        try:
            response = _anthropic().messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                system=SYSTEM_PROMPT.format(today=date.today().isoformat()),
                messages=[{"role": "user", "content": full_user_content}],
            )
            return _parse_json(response.content[0].text), "claude"

        except Exception as e:
            if _is_billing_error(e):
                _claude_billing_failed = True
                print("[Jarvees] Claude billing limit hit — starting Gemini cascade.")
            else:
                # Non-billing error (network, etc.) — surface it, don't fall back silently
                raise

    # ── Gemini cascade ────────────────────────────────────────────────────────
    for model in GEMINI_CASCADE:
        if model in _failed_gemini_models:
            continue  # already hit quota this session — skip

        try:
            raw = _gemini_response(full_user_content, model)
            return _parse_json(raw), model   # e.g. "gemini-3-flash-preview"

        except Exception as e:
            if _is_gemini_quota_error(e):
                _failed_gemini_models.add(model)
                print(f"[Jarvees] {model} quota hit — trying next model in cascade.")
                continue
            raise  # non-quota error — surface it

    # All models exhausted
    raise Exception(
        "All AI models are currently unavailable — Claude billing limit reached "
        "and all Gemini free-tier quotas exhausted. Please wait or top up credits."
    )
