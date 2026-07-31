#!/usr/bin/env python3
"""Provision declared Python and frontend dependencies, then run the doctor."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    managed_uv = workspace / ".quality/tools/uv-venv/bin/uv"
    uv = os.getenv("UV_BIN") or shutil.which("uv") or (
        str(managed_uv) if managed_uv.is_file() else None
    )
    npm = shutil.which("npm")
    if not npm:
        managed_npm = sorted((workspace / ".quality/tools").glob("node-*/bin/npm"))
        npm = str(managed_npm[-1].absolute()) if managed_npm else None
    if not uv:
        print("uv is required; install uv or set UV_BIN", file=sys.stderr)
        return 2
    if not npm:
        print("npm is required; install Node/npm before workspace bootstrap", file=sys.stderr)
        return 2
    environment = {
        **os.environ,
        "UV_BIN": uv,
        "PATH": str(Path(npm).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    commands = [
        ["bash", str(workspace / "services/agent-service/scripts/bootstrap.sh")],
        [
            uv,
            "sync",
            "--project",
            str(workspace / "services/business-service"),
            "--all-groups",
            "--locked",
        ],
        [npm, "ci", "--prefix", str(workspace / "services/agent-service/frontend")],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=workspace, env=environment, check=False)
        if result.returncode:
            return result.returncode
    return subprocess.run(
        [sys.executable, "-B", str(workspace / "scripts/workspace_doctor.py")],
        cwd=workspace,
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
