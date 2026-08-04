#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "agent-service"
CONTROL_PLANE = ROOT / "skill-system" / "controller"
if str(CONTROL_PLANE) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE))

from task_run import TaskRunStore, stable_task_id  # type: ignore  # noqa: E402

EXPECTED_PYTHON = "3.12.13"
CANDIDATE = "B34 Resilient Task Harness"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_definitions() -> list[dict[str, Any]]:
    python = sys.executable
    return [
        {
            "name": "task_run_control_plane",
            "command": [
                python, "-m", "pytest", "-q",
                "skill-system/tests/test_task_run.py",
                "skill-system/tests/test_task_run_cli.py",
                "skill-system/tests/test_resilient_repair_task_run.py",
            ],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "repair_loop_interruption_contracts",
            "command": [
                python, "-m", "pytest", "-q",
                "services/agent-service/tests/architecture/test_resilient_repair_task_run.py",
                "services/agent-service/tests/architecture/test_quality_loop_governance.py",
                "-k",
                (
                    "resilient_repair_task_run or "
                    "quality_loop_does_not_count_round_number_change_as_a_repair or "
                    "repair_target_rejects_green_baseline_and_no_change_verification or "
                    "repair_orchestrator_rejects_mismatched_target_before_fixer or "
                    "repair_orchestrator_builds_stable_issue_records"
                ),
            ],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "skill_control_plane",
            "command": [python, "-m", "pytest", "-q", "skill-system/tests"],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "stage1_stage5_core_regression",
            "command": [
                python, "-m", "pytest", "-q",
                "services/agent-service/tests/runtime/test_stage1_known_architecture_gaps.py",
                "services/agent-service/tests/runtime/test_stage2_p0_safety_boundaries.py",
                "services/agent-service/tests/runtime/test_stage3_entity_authority.py",
                "services/agent-service/tests/runtime/test_stage4_goal_output_refs.py",
                "services/agent-service/tests/runtime/test_stage5_controlled_target_dsl.py",
                "services/agent-service/tests/runtime/test_capability_target_schema.py",
                "services/agent-service/tests/runtime/test_pretool_execution_policy.py",
                "services/agent-service/tests/runtime/test_workflow_runtime.py",
            ],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "broad_context_transaction_governance_regression",
            "command": [
                python, "-m", "pytest", "-q",
                "services/agent-service/tests/context/test_stage3_referent_sets.py",
                "services/agent-service/tests/context/test_stage3_semantic_campaign.py",
                "services/agent-service/tests/context/test_strong_context_case_execution.py",
                "services/agent-service/tests/transactions/test_stage4_transaction_focus.py",
                "services/agent-service/tests/architecture/test_resilient_repair_task_run.py",
                "services/agent-service/tests/architecture/test_quality_loop_governance.py",
                "-k", "not protected_goal_smoke and not protected_model_smoke",
            ],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "architecture",
            "command": [python, "scripts/verify_architecture.py"],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "module_closure",
            "command": [python, "scripts/verify_module_closure.py"],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "version_consistency",
            "command": [python, "scripts/verify_version_consistency.py"],
            "cwd": ROOT,
            "timeout": 180,
        },
        {
            "name": "convergence",
            "command": [
                python,
                "architecture-skill/scripts/verify_convergence.py",
                "--workspace-root",
                str(ROOT),
            ],
            "cwd": ROOT,
            "timeout": 180,
        },
    ]


def source_fingerprint() -> str:
    paths = [
        ROOT / "governance/task-run.schema.json",
        ROOT / "scripts/repair_loop.py",
        ROOT / "scripts/verify_b34_resilient_task_harness.py",
        ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py",
        ROOT / "services/agent-service/tests/architecture/test_resilient_repair_task_run.py",
        ROOT / "skill-system/controller/project_compatibility.py",
        ROOT / "skill-system/controller/task_run.py",
        ROOT / "skill-system/controller/task_run_cli.py",
        ROOT / "skill-system/tests/test_b34_resilient_verifier.py",
        ROOT / "skill-system/tests/test_project_compatibility.py",
        ROOT / "skill-system/tests/test_resilient_repair_task_run.py",
        ROOT / "skill-system/tests/test_task_run.py",
        ROOT / "skill-system/tests/test_task_run_cli.py",
    ]
    rows = []
    for path in paths:
        rows.append({
            "path": str(path.relative_to(ROOT)),
            "sha256": _sha256_file(path),
        })
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _environment(cwd: Path) -> dict[str, str]:
    env = os.environ.copy()
    if cwd == ROOT:
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SERVICE), str(SERVICE / "src"), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
    elif cwd == SERVICE:
        env["PYTHONPATH"] = os.pathsep.join(
            ["src", ".", env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
    return env


def _command_fingerprint(check: dict[str, Any], *, source: str) -> str:
    payload = {
        "name": check["name"],
        "command": check["command"],
        "cwd": str(Path(check["cwd"]).resolve()),
        "timeout": int(check["timeout"]),
        "source_fingerprint": source,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _result_path(evidence_dir: Path, name: str) -> Path:
    return evidence_dir / f"{name}.result.json"


def _load_compatible_result(path: Path, *, command_fingerprint: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("command_fingerprint") != command_fingerprint:
        return None
    return payload


def execute_check(
    check: dict[str, Any],
    *,
    evidence_dir: Path,
    source: str,
) -> dict[str, Any]:
    name = str(check["name"])
    command_fingerprint = _command_fingerprint(check, source=source)
    stdout_path = evidence_dir / f"{name}.stdout.txt"
    stderr_path = evidence_dir / f"{name}.stderr.txt"
    result_path = _result_path(evidence_dir, name)
    started = time.monotonic()
    print(f"[B34] START {name}", flush=True)
    try:
        completed = subprocess.run(
            list(check["command"]),
            cwd=Path(check["cwd"]),
            env=_environment(Path(check["cwd"])),
            text=True,
            capture_output=True,
            timeout=int(check["timeout"]),
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nTimed out after {check['timeout']}s"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    result = {
        "schema_version": 1,
        "name": name,
        "status": "PASS" if returncode == 0 else "FAIL",
        "returncode": returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "command_fingerprint": command_fingerprint,
        "source_fingerprint": source,
        "stdout": str(stdout_path.relative_to(ROOT)),
        "stderr": str(stderr_path.relative_to(ROOT)),
        "completed_at": _now(),
    }
    _atomic_json(result_path, result)
    print(
        f"[B34] {result['status']} {name} rc={returncode} "
        f"duration={result['duration_seconds']}s",
        flush=True,
    )
    return result


def environment_closure() -> dict[str, object]:
    missing = [
        name for name in ("langchain_core", "langgraph")
        if importlib.util.find_spec(name) is None
    ]
    actual_python = ".".join(map(str, sys.version_info[:3]))
    blockers: list[str] = []
    if missing:
        blockers.append("missing Python modules: " + ", ".join(missing))
    if actual_python != EXPECTED_PYTHON:
        blockers.append(f"locked Python is {EXPECTED_PYTHON}; current interpreter is {actual_python}")
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


def _report(
    *,
    task_run: TaskRunStore,
    checks: list[dict[str, Any]],
    evidence_dir: Path,
    source: str,
) -> dict[str, Any]:
    results = []
    for check in checks:
        name = str(check["name"])
        compatible = _load_compatible_result(
            _result_path(evidence_dir, name),
            command_fingerprint=_command_fingerprint(check, source=source),
        )
        if compatible is None:
            compatible = {
                "schema_version": 1,
                "name": name,
                "status": "PENDING",
                "returncode": None,
            }
        results.append(compatible)
    decision = task_run.completion_decision()
    failed = [row for row in results if row.get("status") == "FAIL"]
    status = "PASS" if task_run.payload.get("status") == "COMPLETED" else (
        "FAIL" if failed else "RUNNING"
    )
    return {
        "schema_version": 2,
        "candidate": CANDIDATE,
        "status": status,
        "production_closed": False,
        "source_fingerprint": source,
        "task_run_file": str(task_run.path.relative_to(ROOT)),
        "task_run_status": task_run.payload.get("status"),
        "task_run_phase": task_run.payload.get("phase"),
        "completion_eligible": decision.eligible,
        "missing_conditions": list(decision.missing_conditions),
        "invalid_conditions": list(decision.invalid_conditions),
        "checks": results,
        "environment": environment_closure(),
        "updated_at": _now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resumable verification for the B34 resilient task harness."
    )
    parser.add_argument(
        "--check",
        action="append",
        default=[],
        help="Run only the named pending check; repeat for multiple checks.",
    )
    parser.add_argument(
        "--max-checks",
        type=int,
        default=0,
        help="Bound this invocation to N pending checks; zero means all.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the durable task-run and compatible evidence for this source fingerprint.",
    )
    args = parser.parse_args()

    checks = check_definitions()
    names = [str(row["name"]) for row in checks]
    unknown = sorted(set(args.check) - set(names))
    if unknown:
        parser.error("unknown check(s): " + ", ".join(unknown))
    if args.max_checks < 0:
        parser.error("--max-checks must be non-negative")

    source = source_fingerprint()
    task_id = stable_task_id("verify-b34-resilient-harness", {"source": source, "checks": names})
    task_path = ROOT / ".quality/task-runs" / f"{task_id}.json"
    evidence_dir = ROOT / ".quality/evidence/b34-resilient-task-harness" / task_id
    if args.reset:
        if task_path.exists():
            task_path.unlink()
        lock_path = task_path.with_suffix(task_path.suffix + ".lock")
        if lock_path.exists():
            lock_path.unlink()
        if evidence_dir.is_dir():
            for path in sorted(evidence_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()

    task_run = TaskRunStore.open_or_create(
        task_path,
        task_id=task_id,
        task_kind="b34-resilient-verification",
        binding={
            "candidate": CANDIDATE,
            "source_fingerprint": source,
            "check_names": names,
        },
        required_conditions=names,
        current_workspace_fingerprint=source,
    )

    status = str(task_run.payload.get("status") or "CREATED")
    if status == "CREATED":
        task_run.checkpoint(
            status="PLANNED",
            phase="VERIFICATION_PLANNED",
            workspace_fingerprint=source,
            evidence_refs=[],
            metadata={"check_count": len(names)},
        )
        status = "PLANNED"
    if status in {"PLANNED", "FAILED_RECOVERABLE", "BLOCKED"}:
        task_run.checkpoint(
            status="RUNNING",
            phase="VERIFICATION_RUNNING",
            workspace_fingerprint=source,
            evidence_refs=[],
            metadata={"resume_from": status},
        )

    if task_run.payload.get("status") == "COMPLETED":
        report = _report(
            task_run=task_run,
            checks=checks,
            evidence_dir=evidence_dir,
            source=source,
        )
        _atomic_json(evidence_dir / "verification-report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0

    selected = set(args.check) if args.check else set(names)
    executed = 0
    failed = False
    for check in checks:
        name = str(check["name"])
        condition = (task_run.payload.get("conditions") or {}).get(name) or {}
        if condition.get("satisfied") is True:
            print(f"[B34] SKIP {name} (durable PASS)", flush=True)
            continue
        if name not in selected:
            continue
        if args.max_checks and executed >= args.max_checks:
            break

        command_fingerprint = _command_fingerprint(check, source=source)
        existing = _load_compatible_result(
            _result_path(evidence_dir, name),
            command_fingerprint=command_fingerprint,
        )
        if existing is not None and existing.get("status") == "PASS":
            result = existing
            print(f"[B34] RECONCILE {name} from durable result", flush=True)
        else:
            plan = task_run.plan_action(
                action_name="b34-verification-check",
                arguments={"name": name, "command_fingerprint": command_fingerprint},
                state_fingerprint=source,
                strategies=("execute-check", "execute-check-retry"),
                max_attempts_per_strategy=1,
            )
            if plan.strategy is None:
                print(f"[B34] BLOCKED {name}: retry budget exhausted", flush=True)
                failed = True
                continue
            task_run.checkpoint(
                status="VALIDATING",
                phase=f"RUNNING_{name.upper()}",
                workspace_fingerprint=source,
                evidence_refs=[],
                metadata={"check": name, "strategy": plan.strategy},
            )
            result = execute_check(check, evidence_dir=evidence_dir, source=source)
            task_run.record_action_result(
                plan,
                result={
                    "status": result["status"],
                    "returncode": result["returncode"],
                    "result_file": str(_result_path(evidence_dir, name).relative_to(ROOT)),
                },
                produced_new_evidence=True,
                evidence_refs=[str(_result_path(evidence_dir, name).relative_to(ROOT))],
            )
            executed += 1

        result_ref = str(_result_path(evidence_dir, name).relative_to(ROOT))
        if result.get("status") == "PASS":
            task_run.mark_condition(name, evidence_refs=[result_ref])
            task_run.checkpoint(
                status="VALIDATING",
                phase=f"PASSED_{name.upper()}",
                workspace_fingerprint=source,
                evidence_refs=[result_ref],
                metadata={"check": name},
            )
        else:
            failed = True
            task_run.checkpoint(
                status="FAILED_RECOVERABLE",
                phase=f"FAILED_{name.upper()}",
                workspace_fingerprint=source,
                evidence_refs=[result_ref],
                metadata={"check": name, "returncode": result.get("returncode")},
            )

    decision = task_run.completion_decision()
    if decision.eligible and task_run.payload.get("status") != "COMPLETED":
        result_refs = [
            str(_result_path(evidence_dir, name).relative_to(ROOT))
            for name in names
        ]
        task_run.complete(workspace_fingerprint=source, evidence_refs=result_refs)
    elif not failed:
        task_run.checkpoint(
            status="VALIDATING",
            phase="PARTIAL_VALIDATION",
            workspace_fingerprint=source,
            evidence_refs=[],
            metadata={"missing_conditions": list(decision.missing_conditions)},
        )

    report = _report(
        task_run=task_run,
        checks=checks,
        evidence_dir=evidence_dir,
        source=source,
    )
    _atomic_json(evidence_dir / "verification-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report["status"] == "PASS":
        return 0
    if args.max_checks and not failed:
        return 0
    return 1 if failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
