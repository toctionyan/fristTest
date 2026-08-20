#!/usr/bin/env python3
from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_INVOCATION_COMMANDS = {
    "skill-load": "load",
    "skill-response-bind": "bind-response",
    "skill-invocation-verify": "verify",
    "task-status-project": "status-project",
}
DIRECT_CONTROLLER_COMMANDS = {
    "dev-command": ROOT / "skill-system" / "controller" / "dev_command.py",
    "invoke": ROOT / "skill-system" / "controller" / "plugin_gateway.py",
    "plugin-route": ROOT / "skill-system" / "controller" / "plugin_gateway.py",
    "workflow-validate": ROOT / "skill-system" / "controller" / "workflow_spec.py",
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
    if command in DIRECT_CONTROLLER_COMMANDS:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(DIRECT_CONTROLLER_COMMANDS[command]),
                *sys.argv[2:],
            ],
            cwd=ROOT,
            check=False,
        )
        raise SystemExit(completed.returncode)
    runpy.run_path(str(ROOT / "skill-system" / "controller" / "portable_cli.py"), run_name="__main__")
