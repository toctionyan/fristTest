#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "agent-service"


def run(name: str, command: list[str], *, cwd: Path = ROOT, timeout: int = 180) -> dict[str, object]:
    env = os.environ.copy()
    if cwd == ROOT:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SERVICE), str(SERVICE / "src"), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
    elif cwd == SERVICE:
        env["PYTHONPATH"] = os.pathsep.join(["src", ".", env.get("PYTHONPATH", "")]).strip(os.pathsep)
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "status": "FAIL",
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\nTimed out after {timeout}s",
        }
    return {
        "name": name,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def environment_closure() -> dict[str, object]:
    missing = [
        name for name in ("langchain_core", "langgraph")
        if importlib.util.find_spec(name) is None
    ]
    expected_python = "3.12.13"
    actual_python = ".".join(map(str, sys.version_info[:3]))
    blockers: list[str] = []
    if missing:
        blockers.append("missing Python modules: " + ", ".join(missing))
    if actual_python != expected_python:
        blockers.append(f"locked Python is {expected_python}; current interpreter is {actual_python}")
    blockers.extend([
        "real DeepSeek/OpenAI provider certification not executed locally",
        "managed PostgreSQL/pgvector recovery not executed locally",
        "dual-service browser and concurrency certification not executed locally",
    ])
    return {
        "name": "production_environment_closure",
        "status": "BLOCKED_BY_ENVIRONMENT" if blockers else "READY_FOR_EXTERNAL_CERTIFICATION",
        "required_for_candidate": False,
        "production_closed": False,
        "blockers": blockers,
    }


def main() -> int:
    checks = [
        run(
            "task_run_control_plane",
            [
                sys.executable, "-m", "pytest", "-q",
                "skill-system/tests/test_task_run.py",
                "skill-system/tests/test_task_run_cli.py",
                "skill-system/tests/test_resilient_repair_task_run.py",
            ],
        ),
        run(
            "repair_loop_interruption_contracts",
            [
                sys.executable, "-m", "pytest", "-q",
                "services/agent-service/tests/architecture/test_resilient_repair_task_run.py",
            ],
        ),
        run(
            "stage1_stage5_core_regression",
            [
                sys.executable, "-m", "pytest", "-q",
                "services/agent-service/tests/runtime/test_stage1_known_architecture_gaps.py",
                "services/agent-service/tests/runtime/test_stage2_p0_safety_boundaries.py",
                "services/agent-service/tests/runtime/test_stage3_entity_authority.py",
                "services/agent-service/tests/runtime/test_stage4_goal_output_refs.py",
                "services/agent-service/tests/runtime/test_stage5_controlled_target_dsl.py",
                "services/agent-service/tests/runtime/test_capability_target_schema.py",
                "services/agent-service/tests/runtime/test_pretool_execution_policy.py",
                "services/agent-service/tests/runtime/test_workflow_runtime.py",
            ],
        ),
        run("architecture", [sys.executable, "scripts/verify_architecture.py"]),
        run("module_closure", [sys.executable, "scripts/verify_module_closure.py"]),
        run("version_consistency", [sys.executable, "scripts/verify_version_consistency.py"]),
        run(
            "convergence",
            [sys.executable, "architecture-skill/scripts/verify_convergence.py", "--workspace-root", str(ROOT)],
        ),
    ]
    environment = environment_closure()
    status = "PASS" if all(row["status"] == "PASS" for row in checks) else "FAIL"
    report = {
        "schema_version": 1,
        "candidate": "B34 Resilient Task Harness",
        "status": status,
        "production_closed": False,
        "checks": checks,
        "environment": environment,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
