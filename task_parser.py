"""
Jarvees Task Parser
-------------------
Parses plain-English task descriptions into structured JSON,
with an interactive verification step so you can correct any assumptions.

Output schema:
  title               — short task name (3-6 words)
  deadline            — ISO date (YYYY-MM-DD) or null
  estimated_duration  — minutes (int)
  priority            — "high" | "medium" | "low"
  task_type           — "quick" | "deadline" | "recurring"
  recurrence          — e.g. "weekly:tuesday,thursday@07:00" or null

Usage:
  python3 task_parser.py                          # interactive mode, enter tasks one by one
  python3 task_parser.py "task one" "task two"   # batch: pass multiple tasks as arguments
  python3 task_parser.py --file tasks.txt        # batch: one task per line in a text file
  python3 task_parser.py --no-verify [...]       # skip verification, just parse and print
"""

import json
import sys
from datetime import date

# ── Field metadata ──────────────────────────────────────────────────────────
# Defines display labels, allowed values, and how to coerce user edits.

FIELDS = [
    {
        "key": "title",
        "label": "Title",
        "allowed": None,  # free text
        "coerce": str,
    },
    {
        "key": "deadline",
        "label": "Deadline (YYYY-MM-DD or 'none')",
        "allowed": None,
        "coerce": lambda v: None if v.lower() in ("none", "null", "") else v,
    },
    {
        "key": "estimated_duration",
        "label": "Duration (minutes)",
        "allowed": None,
        "coerce": int,
    },
    {
        "key": "priority",
        "label": "Priority",
        "allowed": ["high", "medium", "low"],
        "coerce": str.lower,
    },
    {
        "key": "task_type",
        "label": "Task type",
        "allowed": ["quick", "deadline", "recurring"],
        "coerce": str.lower,
    },
    {
        "key": "recurrence",
        "label": "Recurrence (or 'none')",
        "allowed": None,
        "coerce": lambda v: None if v.lower() in ("none", "null", "") else v,
    },
]

# ── AI prompt ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Jarvees, a personal AI assistant that parses task descriptions.

When given a plain-English task, extract and return ONLY a valid JSON object with these fields:
- "title": string — a short, clean task name (3-6 words max)
- "deadline": string (ISO date YYYY-MM-DD) or null — the due date if mentioned
- "estimated_duration": integer — estimated minutes to complete
  Smart defaults: quick errand=20, phone call=15, gym=60, meeting=30, report/document=90
- "priority": "high" | "medium" | "low"
- "task_type": "quick" | "deadline" | "recurring"
- "recurrence": string describing recurrence (e.g. "weekly:tuesday,thursday@07:00") or null

Rules:
- Today's date is {today}. Resolve relative dates like "Friday", "end of month", "tomorrow" from this.
- If no deadline and not recurring, deadline is null and task_type is "quick"
- Recurring tasks have task_type "recurring" and a non-null recurrence value
- Return ONLY the JSON object — no explanation, no markdown fences
"""


def build_system_prompt() -> str:
    return SYSTEM_PROMPT.format(today=date.today().isoformat())


# ── API call ─────────────────────────────────────────────────────────────────

def parse_task_with_api(description: str) -> dict:
    """Calls Claude API to parse a task. Requires ANTHROPIC_API_KEY."""
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(override=False)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=build_system_prompt(),
        messages=[{"role": "user", "content": description}],
    )
    return json.loads(response.content[0].text.strip())


# ── Verification layer ───────────────────────────────────────────────────────

def display_parsed(result: dict):
    """Pretty-prints the parsed task fields."""
    print()
    print("  ┌─ Jarvees parsed this as ─────────────────────────┐")
    for field in FIELDS:
        key = field["key"]
        val = result.get(key)
        display_val = str(val) if val is not None else "none"
        label = field["label"].split(" (")[0]  # strip hint for display
        print(f"  │  {label:<22} {display_val}")
    print("  └───────────────────────────────────────────────────┘")


def verify_and_edit(result: dict) -> dict:
    """
    Shows parsed fields and lets the user confirm or edit.
    Returns the final (possibly edited) task dict.
    """
    display_parsed(result)
    print()
    answer = input("  Looks right? [Y] confirm  [e] edit fields  [r] re-type task: ").strip().lower()

    if answer in ("", "y"):
        return result

    if answer == "r":
        new_input = input("  Re-type your task: ").strip()
        new_result = parse_task_with_api(new_input)
        return verify_and_edit(new_result)

    # Edit mode: step through each field
    print()
    print("  Press Enter to keep a value, or type a new one.")
    edited = dict(result)
    for field in FIELDS:
        key = field["key"]
        current = edited.get(key)
        hint = f" ({', '.join(field['allowed'])})" if field["allowed"] else ""
        prompt = f"  {field['label']}{hint} [{current}]: "
        raw = input(prompt).strip()
        if raw == "":
            continue  # keep existing value
        try:
            edited[key] = field["coerce"](raw)
        except (ValueError, TypeError):
            print(f"  ! Invalid value for {key}, keeping original.")

    display_parsed(edited)
    confirm = input("  Save these changes? [Y/n]: ").strip().lower()
    if confirm == "n":
        return verify_and_edit(result)  # start over
    return edited


# ── Batch runner ─────────────────────────────────────────────────────────────

def run_batch(descriptions: list[str], verify: bool) -> list[dict]:
    results = []
    total = len(descriptions)
    for i, desc in enumerate(descriptions, 1):
        print(f"\n{'─' * 60}")
        print(f"  Task {i}/{total}: \"{desc}\"")
        parsed = parse_task_with_api(desc)
        if verify:
            final = verify_and_edit(parsed)
        else:
            display_parsed(parsed)
            final = parsed
        results.append(final)
    return results


def run_interactive(verify: bool):
    """Keep prompting for tasks until the user submits an empty line."""
    print("  Enter tasks one at a time. Press Enter on an empty line to finish.\n")
    descriptions = []
    while True:
        task = input("  Task: ").strip()
        if not task:
            break
        descriptions.append(task)

    if not descriptions:
        print("  No tasks entered.")
        return []

    return run_batch(descriptions, verify)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    no_verify = "--no-verify" in args
    args = [a for a in args if a != "--no-verify"]

    print("=" * 60)
    print("  Jarvees Task Parser")
    print("=" * 60)

    # --file mode
    if args and args[0] == "--file":
        if len(args) < 2:
            print("  Error: --file requires a filename argument.")
            sys.exit(1)
        with open(args[1]) as f:
            lines = [line.strip() for line in f if line.strip()]
        results = run_batch(lines, verify=not no_verify)

    # CLI args mode: each arg is a task
    elif args:
        results = run_batch(args, verify=not no_verify)

    # Interactive mode
    else:
        results = run_interactive(verify=not no_verify)

    # Final summary
    if results:
        print(f"\n{'=' * 60}")
        print(f"  Done. {len(results)} task(s) parsed.")
        print(f"  JSON output:\n")
        print(json.dumps(results, indent=2))
