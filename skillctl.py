#!/usr/bin/env python3
from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_INVOCATION_COMMANDS = {
    "skill-load": "load",
    "skill-invocation-verify": "verify",
    "task-status-project": "status-project",
}

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in SKILL_INVOCATION_COMMANDS:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "skill-system" / "controller" / "skill_invocation_cli.py"),
                SKILL_INVOCATION_COMMANDS[command],
                *sys.argv[2:],
            ],
            cwd=ROOT,
            check=False,
        )
        raise SystemExit(completed.returncode)
    runpy.run_path(str(ROOT / "skill-system" / "controller" / "portable_cli.py"), run_name="__main__")
