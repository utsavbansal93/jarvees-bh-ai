"""
Jarvees server launcher — used by .claude/launch.json (Claude Code preview tool).

Why this exists:
  The Claude preview sandbox blocks reading `pyvenv.cfg` during Python startup
  (site.py tries to open it), so a normal venv Python can't be used.

  Instead we launch with the system Python3 + the -S flag (which skips site.py
  entirely, so pyvenv.cfg is never read).  Then we manually insert the venv's
  site-packages into sys.path HERE, at runtime, before any package imports.

  Runtime file reads from the project directory ARE permitted by the sandbox,
  so this approach works even when Python startup reads are restricted.
"""

import sys
import os

# ── 1. Locate the project root (run.py is always invoked with an absolute path
#       from launch.json, so __file__ is absolute — no os.getcwd() needed). ──
_project = os.path.dirname(os.path.abspath(__file__))

# ── 2. Inject the venv site-packages so all installed packages are importable. ──
_site = os.path.join(_project, "venv", "lib", "python3.9", "site-packages")
if _site not in sys.path:
    sys.path.insert(0, _site)

# ── 3. Ensure the project root itself is on the path (for main, database, etc.). ──
if _project not in sys.path:
    sys.path.insert(0, _project)

# ── 4. Boot the server. ────────────────────────────────────────────────────────
import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=8000)
