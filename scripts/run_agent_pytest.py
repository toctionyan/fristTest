#!/usr/bin/env python3
"""Run selected Agent tests with an explicit, verified pytest environment."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _can_import(python: Path, env: dict[str, str]) -> bool:
    try:
        return subprocess.run(
            [str(python), "-c", "import pytest, langchain_core"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _runtime(workspace: Path) -> tuple[Path, dict[str, str]] | None:
    base_env = os.environ.copy()
    candidates = [
        Path(os.environ["QUALITY_AGENT_PYTHON"]).expanduser() if os.environ.get("QUALITY_AGENT_PYTHON") else None,
        workspace / "services/agent-service/.venv/bin/python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK) and _can_import(candidate, base_env):
            return candidate, base_env
    for site_packages in sorted((workspace / ".quality/tools/agent-venv/lib").glob("python*/site-packages"), reverse=True):
        env = dict(base_env)
        env["PYTHONPATH"] = str(site_packages) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        candidate = Path(sys.executable)
        if _can_import(candidate, env):
            return candidate, env
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--junitxml")
    parser.add_argument("tests", nargs="+")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    runtime = _runtime(workspace)
    if runtime is None:
        print("no verified Agent pytest runtime is available", file=sys.stderr)
        return 78
    python, env = runtime
    agent = workspace / "services/agent-service"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = "src:." + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [str(python), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if args.junitxml:
        junit = Path(args.junitxml).expanduser().resolve()
        junit.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--junitxml={junit}")
    command.extend(args.tests)
    return subprocess.run(command, cwd=agent, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
