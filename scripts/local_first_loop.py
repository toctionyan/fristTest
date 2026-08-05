#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from local_first_governance import (  # noqa: E402
    LOCAL_GATE_ORDER,
    LocalFirstGovernanceError,
    approve_remote_repair,
    begin_local_repair_round,
    bind_ci_run,
    create_local_first_task,
    export_status,
    record_ci_result,
    record_local_gate,
    scope_violations,
    upload_admission,
)
from task_run import TaskRunError, TaskRunStore, fingerprint  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _workspace_snapshot(workspace: Path) -> dict[str, str]:
    ignored_parts = {".git", ".quality", ".pytest_cache", "__pycache__", "node_modules", ".venv"}
    rows: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or any(part in ignored_parts for part in path.parts):
            continue
        relative = path.relative_to(workspace).as_posix()
        rows[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


def _workspace_fingerprint(workspace: Path) -> str:
    return fingerprint(_workspace_snapshot(workspace))


def _changed_paths(baseline: dict[str, str], current: dict[str, str]) -> list[str]:
    return sorted(
        path
        for path in set(baseline) | set(current)
        if baseline.get(path) != current.get(path)
    )


def _baseline_manifest_path(workspace: Path, task_id: str) -> Path:
    return workspace / ".quality" / "local-first" / task_id / "baseline-manifest.json"


def _load_baseline_manifest(workspace: Path, store: TaskRunStore) -> dict[str, str]:
    path = _baseline_manifest_path(workspace, store.payload["task_id"])
    payload = _load_json(path)
    files = payload.get("files")
    if not isinstance(files, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in files.items()):
        raise ValueError("invalid local-first baseline manifest")
    return dict(files)


def _open_task(path: Path) -> TaskRunStore:
    payload = _load_json(path)
    return TaskRunStore(path.resolve(), payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_task_spec(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if payload.get("schema_version") != 1:
        raise ValueError("local-first task spec requires schema_version=1")
    gate_ids = [str(row.get("id")) for row in payload.get("gates") or [] if isinstance(row, dict)]
    required = list(LOCAL_GATE_ORDER[:-1])
    if gate_ids != required:
        raise ValueError(f"task gates must be exactly {required}, got {gate_ids}")
    for row in payload.get("gates") or []:
        argv = row.get("argv")
        if not isinstance(argv, list) or not argv or any(not str(item).strip() for item in argv):
            raise ValueError(f"gate {row.get('id')} requires non-empty argv")
        if int(row.get("timeout_seconds") or 0) < 1:
            raise ValueError(f"gate {row.get('id')} requires positive timeout_seconds")
    return payload


def _gate_evidence_dir(workspace: Path, task_id: str, gate: str, round_number: int) -> Path:
    return workspace / ".quality" / "local-first" / task_id / "gates" / f"round-{round_number:02d}" / gate


def _terminate_process_group(process: subprocess.Popen[str], *, grace_seconds: float = 3.0) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass


def _run_gate(workspace: Path, row: dict[str, Any], evidence_dir: Path) -> tuple[bool, list[str], dict[str, Any]]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    argv = [str(item) for item in row["argv"]]
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in (row.get("env") or {}).items()})
    timeout_seconds = int(row["timeout_seconds"])
    stdout_path = evidence_dir / "stdout.txt"
    stderr_path = evidence_dir / "stderr.txt"
    result_path = evidence_dir / "result.json"
    started = time.monotonic()
    timed_out = False
    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        process = subprocess.Popen(
            argv,
            cwd=workspace,
            env=env,
            text=True,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_group(process)
            returncode = 124
        else:
            # A gate may crash after spawning descendants that inherited its output
            # handles. Always reap the whole isolated process group so a completed
            # gate cannot hang the local TaskRun or leak background mutation.
            _terminate_process_group(process, grace_seconds=0.2)
    duration = round(time.monotonic() - started, 3)
    result = {
        "schema_version": 1,
        "gate": row["id"],
        "argv": argv,
        "exit_code": returncode,
        "status": "PASS" if returncode == 0 and not timed_out else "FAIL",
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": duration,
    }
    _write_json(result_path, result)
    refs = [
        stdout_path.relative_to(workspace).as_posix(),
        stderr_path.relative_to(workspace).as_posix(),
        result_path.relative_to(workspace).as_posix(),
    ]
    return result["status"] == "PASS", refs, result


def command_init(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    spec_path = Path(args.spec).resolve()
    spec = _parse_task_spec(spec_path)
    state = Path(args.state).resolve()
    store = create_local_first_task(
        state,
        task_id=str(spec["task_id"]),
        change_id=str(spec["change_id"]),
        base_sha=str(spec["base_sha"]),
        branch=str(spec["branch"]),
        patch_owner=str(spec["patch_owner"]),
        allowed_paths=[str(item) for item in spec["allowed_paths"]],
        target_fingerprint=fingerprint(spec),
        budgets=spec.get("budgets"),
    )
    baseline_path = _baseline_manifest_path(workspace, store.payload["task_id"])
    baseline_files = _workspace_snapshot(workspace)
    _write_json(
        baseline_path,
        {
            "schema_version": 1,
            "task_id": store.payload["task_id"],
            "workspace": str(workspace),
            "fingerprint": fingerprint(baseline_files),
            "files": baseline_files,
        },
    )
    store.set_metadata(
        local_first_task_spec=str(spec_path),
        local_first_baseline_manifest=str(baseline_path),
        local_first_baseline_fingerprint=fingerprint(baseline_files),
    )
    print(json.dumps(export_status(store), ensure_ascii=False, indent=2))
    return 0


def command_run_local(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    store = _open_task(Path(args.state))
    spec = _parse_task_spec(Path(args.spec).resolve())
    if fingerprint(spec) != store.payload["binding"]["target_fingerprint"]:
        raise ValueError("local-first task spec changed after immutable task creation")
    local = store.payload["metadata"]["local_first"]
    round_number = int(local["counters"]["local_repair_rounds"]) + 1
    workspace_fp = _workspace_fingerprint(workspace)
    begin_local_repair_round(store, workspace_fingerprint=workspace_fp)
    for row in spec["gates"]:
        gate = str(row["id"])
        evidence_dir = _gate_evidence_dir(workspace, store.payload["task_id"], gate, round_number)
        passed, refs, details = _run_gate(workspace, row, evidence_dir)
        workspace_fp = _workspace_fingerprint(workspace)
        record_local_gate(
            store,
            gate=gate,
            passed=passed,
            evidence_refs=refs,
            workspace_fingerprint=workspace_fp,
            details=details,
        )
        if not passed:
            print(json.dumps(export_status(store), ensure_ascii=False, indent=2))
            return 1
    baseline = _load_baseline_manifest(workspace, store)
    changed_paths = _changed_paths(baseline, _workspace_snapshot(workspace))
    violations = scope_violations(changed_paths, store.payload["binding"]["allowed_paths"])
    scope_evidence = workspace / ".quality" / "local-first" / store.payload["task_id"] / "scope.json"
    _write_json(
        scope_evidence,
        {
            "schema_version": 1,
            "allowed_paths": store.payload["binding"]["allowed_paths"],
            "changed_paths": changed_paths,
            "scope_violations": list(violations),
            "status": "PASS" if changed_paths and not violations else "FAIL",
        },
    )
    record_local_gate(
        store,
        gate="scope",
        passed=bool(changed_paths) and not violations,
        evidence_refs=[scope_evidence.relative_to(workspace).as_posix()],
        workspace_fingerprint=_workspace_fingerprint(workspace),
        details={"changed_paths": changed_paths, "scope_violations": list(violations)},
    )
    print(json.dumps(export_status(store), ensure_ascii=False, indent=2))
    return 0 if changed_paths and not violations else 1


def command_admit(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    store = _open_task(Path(args.state))
    baseline = _load_baseline_manifest(workspace, store)
    computed_changed = _changed_paths(baseline, _workspace_snapshot(workspace))
    supplied_changed = sorted(set(args.changed_path or []))
    if supplied_changed and supplied_changed != computed_changed:
        raise ValueError(
            f"supplied changed paths do not match the local baseline: supplied={supplied_changed} computed={computed_changed}"
        )
    evidence = workspace / ".quality" / "local-first" / store.payload["task_id"] / "upload-admission.json"
    decision = upload_admission(
        store,
        changed_paths=computed_changed,
        candidate_head_sha=args.head_sha,
        workspace_fingerprint=_workspace_fingerprint(workspace),
        evidence_refs=[evidence.relative_to(workspace).as_posix()],
    )
    _write_json(evidence, {"schema_version": 1, **decision.__dict__})
    print(json.dumps({"decision": decision.__dict__, "status": export_status(store)}, ensure_ascii=False, indent=2))
    return 0 if decision.allowed else 1


def command_ci_start(args: argparse.Namespace) -> int:
    store = _open_task(Path(args.state))
    bind_ci_run(
        store,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        head_sha=args.head_sha,
        evidence_refs=[args.evidence],
    )
    print(json.dumps(export_status(store), ensure_ascii=False, indent=2))
    return 0


def command_ci_result(args: argparse.Namespace) -> int:
    store = _open_task(Path(args.state))
    log_text = Path(args.log_file).read_text(encoding="utf-8") if args.log_file else args.log_text
    decision = record_ci_result(
        store,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        head_sha=args.head_sha,
        conclusion=args.conclusion,
        job_name=args.job_name,
        log_text=log_text or "",
        evidence_refs=[args.evidence],
    )
    print(json.dumps({"decision": decision.__dict__ if decision else None, "status": export_status(store)}, ensure_ascii=False, indent=2))
    return 0 if args.conclusion == "success" else 1


def command_approve_remote(args: argparse.Namespace) -> int:
    store = _open_task(Path(args.state))
    approve_remote_repair(store, approval="explicitly-approved", evidence_refs=[args.evidence])
    print(json.dumps(export_status(store), ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    store = _open_task(Path(args.state))
    print(json.dumps(export_status(store), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local-first repair and CI certification controller")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--workspace", default=".")
    init.add_argument("--spec", required=True)
    init.add_argument("--state", required=True)
    init.set_defaults(func=command_init)

    run_local = sub.add_parser("run-local")
    run_local.add_argument("--workspace", default=".")
    run_local.add_argument("--spec", required=True)
    run_local.add_argument("--state", required=True)
    run_local.set_defaults(func=command_run_local)

    admit = sub.add_parser("admit-upload")
    admit.add_argument("--workspace", default=".")
    admit.add_argument("--state", required=True)
    admit.add_argument("--head-sha", required=True)
    admit.add_argument("--changed-path", action="append", default=[])
    admit.set_defaults(func=command_admit)

    ci_start = sub.add_parser("ci-start")
    ci_start.add_argument("--state", required=True)
    ci_start.add_argument("--run-id", type=int, required=True)
    ci_start.add_argument("--run-attempt", type=int, default=1)
    ci_start.add_argument("--head-sha", required=True)
    ci_start.add_argument("--evidence", required=True)
    ci_start.set_defaults(func=command_ci_start)

    ci_result = sub.add_parser("ci-result")
    ci_result.add_argument("--state", required=True)
    ci_result.add_argument("--run-id", type=int, required=True)
    ci_result.add_argument("--run-attempt", type=int, default=1)
    ci_result.add_argument("--head-sha", required=True)
    ci_result.add_argument("--conclusion", choices=["success", "failure", "cancelled"], required=True)
    ci_result.add_argument("--job-name", required=True)
    ci_result.add_argument("--log-file")
    ci_result.add_argument("--log-text", default="")
    ci_result.add_argument("--evidence", required=True)
    ci_result.set_defaults(func=command_ci_result)

    approve = sub.add_parser("approve-remote-repair")
    approve.add_argument("--state", required=True)
    approve.add_argument("--evidence", required=True)
    approve.set_defaults(func=command_approve_remote)

    status = sub.add_parser("status")
    status.add_argument("--state", required=True)
    status.set_defaults(func=command_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError, TaskRunError, LocalFirstGovernanceError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "production_closed": False}, ensure_ascii=False, indent=2))
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
