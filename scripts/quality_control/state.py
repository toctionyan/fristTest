from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .common import _load_json, _now, _sha256_text, _verify_evidence_attestation
from .constants import BLOCKED, FAIL, TRANSITION_TARGET_KINDS
from .convergence import _failure_classification
from .contracts import (
    _repair_change_fingerprint, _scope_violations, _target_identity, _validate_abstraction_record,
    _workspace_snapshot,
)

def _load_baseline(
    evidence_dir: Path | None,
    *,
    workspace: Path,
    target: dict[str, Any],
    policy_fingerprint: str,
) -> dict[str, Any]:
    if evidence_dir is None:
        raise ValueError("local-change verification requires --baseline-evidence from a prior --baseline pass")
    attestation_error = _verify_evidence_attestation(workspace, evidence_dir)
    if attestation_error:
        raise ValueError(attestation_error)
    record_path = evidence_dir / "baseline-record.json"
    if not record_path.is_file():
        raise ValueError("--baseline-evidence does not contain baseline-record.json")
    record = _load_json(record_path)
    if record.get("target_identity") != _target_identity(target):
        raise ValueError("baseline evidence target identity does not match the current target")
    if record.get("policy_fingerprint") != policy_fingerprint:
        raise ValueError("baseline evidence policy does not match the current policy")
    snapshot_file = str(record.get("workspace_snapshot_file") or "")
    if not snapshot_file or Path(snapshot_file).is_absolute() or ".." in Path(snapshot_file).parts:
        raise ValueError("baseline evidence does not declare a safe workspace snapshot file")
    snapshot_path = evidence_dir / snapshot_file
    if not snapshot_path.is_file():
        raise ValueError("baseline evidence does not contain its workspace source snapshot")
    snapshot = _load_json(snapshot_path)
    if snapshot.get("fingerprint") != record.get("workspace_snapshot_fingerprint"):
        raise ValueError("baseline workspace source snapshot fingerprint does not match its record")
    record = dict(record)
    record["workspace_snapshot"] = snapshot
    summary_path = evidence_dir / "run-summary.json"
    if not summary_path.is_file():
        raise ValueError("--baseline-evidence does not contain run-summary.json")
    summary = _load_json(summary_path)
    if summary.get("run_kind") != "baseline":
        raise ValueError("--baseline-evidence run-summary is not a baseline run")
    record["baseline_claim_statuses"] = {
        str(item.get("id")): str(item.get("status") or "NOT_EXECUTED")
        for item in summary.get("claim_results") or []
        if isinstance(item, dict) and item.get("id")
    }
    return record

def _state_path(state_dir: Path, target: dict[str, Any]) -> Path:
    return state_dir / f"{_sha256_text(str(target['id']))}.json"

def _load_loop_state(state_dir: Path, target: dict[str, Any]) -> dict[str, Any] | None:
    path = _state_path(state_dir, target)
    if not path.is_file():
        return None
    record = _load_json(path)
    if record.get("target_identity") != _target_identity(target):
        raise ValueError("quality-loop state belongs to a changed target; create a new 目标 ID")
    return record

def _write_loop_state(state_dir: Path, target: dict[str, Any], record: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _state_path(state_dir, target).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _verify_loop_round(
    *,
    target: dict[str, Any],
    baseline_evidence: Path | None,
    policy_fingerprint: str,
    state_dir: Path,
    baseline: bool,
    workspace: Path,
    target_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if target["context"] == "ci":
        return None, None
    if baseline:
        if int(target["current_round"]) != 1:
            raise ValueError("a new baseline must start at 当前轮次：1")
        return None, None
    if target["kind"] in TRANSITION_TARGET_KINDS:
        baseline_record = _load_baseline(
            baseline_evidence, workspace=workspace, target=target, policy_fingerprint=policy_fingerprint
        )
    else:
        if baseline_evidence is not None:
            baseline_record = _load_baseline(
                baseline_evidence, workspace=workspace, target=target, policy_fingerprint=policy_fingerprint
            )
        else:
            baseline_record = {
                "decision": None,
                "workspace_snapshot": _workspace_snapshot(workspace, ignored_roots=(state_dir,)),
                "baseline_claim_statuses": {},
            }
    runtime_roots = tuple(
        path
        for path in (baseline_evidence, state_dir)
        if path is not None
    )
    violations = _scope_violations(
        workspace,
        baseline=baseline_record,
        allowed_paths=tuple(target["allowed_paths"]),
        ignored_roots=runtime_roots,
    )
    if violations:
        preview = ", ".join(violations[:20])
        suffix = "" if len(violations) <= 20 else f" (+{len(violations) - 20} more)"
        raise ValueError(
            "workspace changes are outside the frozen target 允许变更路径: " + preview + suffix
            + "; update the target scope and create a new --baseline record"
        )
    _validate_abstraction_record(
        workspace,
        baseline=baseline_record,
        target=target,
        ignored_roots=runtime_roots,
    )
    state = _load_loop_state(state_dir, target)
    if state is None:
        state = {
            "schema_version": 1,
            "target_identity": _target_identity(target),
            "policy_fingerprint": policy_fingerprint,
            "baseline_evidence": str(baseline_evidence),
            "baseline_decision": baseline_record.get("decision"),
            "required_round": 1,
            "completed": False,
            "stopped": False,
            "stagnant_rounds": 0,
            "round_history": [],
        }
    if state.get("completed"):
        raise ValueError("this target already converged; stop the loop or create a new target")
    if state.get("stopped"):
        reason = str(state.get("stop_reason") or "repair budget exhausted")
        raise ValueError(f"this target is stopped ({reason}); stop patching and deliver its evidence")
    if int(state.get("required_round") or 1) != int(target["current_round"]):
        raise ValueError(
            f"target 当前轮次 must be {state.get('required_round')} after its prior result, not {target['current_round']}"
        )
    repair_fingerprint, repair_paths = _repair_change_fingerprint(
        workspace,
        baseline=baseline_record,
        allowed_paths=tuple(target["allowed_paths"]),
        target_path=target_path,
        ignored_roots=runtime_roots,
    )
    if target["kind"] in TRANSITION_TARGET_KINDS and not repair_paths:
        raise ValueError(
            f"{target['kind']} target verification requires an actual in-scope candidate change relative to its baseline"
        )
    if int(target["current_round"]) > 1 and repair_fingerprint == str(state.get("last_repair_fingerprint") or ""):
        raise ValueError(
            "no actual in-scope repair detected since the preceding failed round; "
            "changing only 当前轮次 cannot advance the quality loop"
        )
    state["current_repair_fingerprint"] = repair_fingerprint
    state["current_repair_paths"] = repair_paths
    return baseline_record, state

def _repair_plan(
    results: list[dict[str, Any]],
    *,
    workspace: Path,
    policy_path: Path,
    mode: str,
    target_path: Path | None,
    target: dict[str, Any] | None,
    evidence_dir: Path,
    baseline_evidence: Path | None,
    loop_status: str | None,
) -> dict[str, Any]:
    repairs: list[dict[str, Any]] = []
    for result in results:
        if result["status"] not in {FAIL, BLOCKED}:
            continue
        rerun: list[str] = [
            sys.executable,
            "-B",
            str(workspace / "scripts" / "quality_loop.py"),
            "--workspace-root",
            str(workspace),
            "--policy",
            str(policy_path),
            "--mode",
            mode,
        ]
        controller_input = str(result["id"]) == "quality-controller-input"
        if not controller_input:
            rerun.extend(["--rerun-from", str(result["id"])])
        if target_path is not None:
            rerun.extend(["--target", str(target_path)])
        if baseline_evidence is not None:
            rerun.extend(["--baseline-evidence", str(baseline_evidence)])
        repairs.append(
            {
                "gate_id": result["id"],
                "owner": result["owner"],
                "category": result["category"],
                "blocking_level": result.get("blocking_level", "required"),
                "failure_kind": _failure_classification(result),
                "repair_playbook": result.get("repair_playbook"),
                "evidence": {
                    "stderr": f"steps/{result['id']}.stderr.txt",
                    "stdout": f"steps/{result['id']}.stdout.txt",
                },
                "next_action": (
                    "stop local patching and create a new architecture target with revised assumptions"
                    if loop_status == "ARCHITECTURE_REPLAN_REQUIRED"
                    else (
                        "provision the declared environment"
                        if result["status"] == BLOCKED
                        else (
                            "repair the target, policy or baseline input; if its frozen scope changes, create a new --baseline record"
                            if controller_input
                            else "apply the smallest fix within the declared target scope"
                        )
                    )
                ),
                "rerun": rerun,
            }
        )
    return {
        "generated_at": _now(),
        "mode": mode,
        "target_identity": _target_identity(target) if target else None,
        "loop_status": loop_status,
        "repairs": repairs,
    }

def _blocked_prerequisite_result(step: dict[str, Any], prerequisites: list[str], reason: str) -> dict[str, Any]:
    return {
        "id": step["id"],
        "name": step.get("name", step["id"]),
        "status": BLOCKED,
        "owner": step.get("owner", "unassigned"),
        "category": step.get("category", "verification"),
        "blocking_level": step.get("blocking_level", "required"),
        "repair_playbook": step.get("repair_playbook", "repair the policy dependency closure"),
        "depends_on": list(step.get("depends_on") or []),
        "started_at": _now(),
        "ended_at": _now(),
        "exit_code": 78,
        "duration_ms": 0,
        "stdout": "",
        "stderr": f"selected dependency closure is incomplete for: {', '.join(prerequisites)}; {reason}",
        "metadata": {"failure_kind": "dependency_closure", "missing_prerequisites": prerequisites},
    }

def _workspace_immutability_result(
    start_snapshot: dict[str, Any], end_snapshot: dict[str, Any]
) -> dict[str, Any]:
    start_files = start_snapshot.get("files") or {}
    end_files = end_snapshot.get("files") or {}
    changed = sorted(
        name
        for name in set(start_files) | set(end_files)
        if start_files.get(name) != end_files.get(name)
    )
    return {
        "id": "controller-workspace-immutability",
        "name": "controller-workspace-immutability",
        "status": FAIL,
        "owner": "quality-controller",
        "category": "quality-evidence",
        "blocking_level": "required",
        "repair_playbook": (
            "make every Gate read-only with respect to governed source; write generated evidence only "
            "under declared evidence or coverage directories"
        ),
        "depends_on": [],
        "started_at": _now(),
        "ended_at": _now(),
        "exit_code": 1,
        "duration_ms": 0,
        "stdout": "",
        "stderr": "quality Gate execution changed governed workspace files: " + ", ".join(changed[:50]),
        "metadata": {
            "failure_kind": "source_changed_during_verification",
            "changed_files": changed,
            "start_fingerprint": start_snapshot.get("fingerprint"),
            "end_fingerprint": end_snapshot.get("fingerprint"),
        },
    }

