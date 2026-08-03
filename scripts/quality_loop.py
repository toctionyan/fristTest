#!/usr/bin/env python3
"""Reproducible, bounded quality controller for this workspace.

The controller runs declared gates once.  It never edits source code and never
retries a failed command by itself: a developer or Codex records a target,
captures a baseline, applies one bounded repair, and asks for the smallest
valid dependency-closed regression. Evidence, source immutability, convergence
trend and the bounded repair-round budget are all machine-recorded. Historical
PASS rows never replace execution of this run's prerequisites.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import ast
import datetime as dt
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from source_paths import is_runtime_artifact_path

CONTROL_PLANE_DIR = SCRIPTS_DIR.parent / "skill-system" / "controller"
if str(CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_PLANE_DIR))
from progress import evaluate_progress  # type: ignore
from trusted_judge import verify_candidate as verify_trusted_candidate  # type: ignore

from quality_control.constants import (
    ABSTRACTION_RECORD_MARKERS, BLOCKED, BLOCKING_LEVELS, CLAIM_CLOSURE_REQUIREMENTS,
    CLAIM_EVIDENCE_KINDS, CLAIM_RISKS, CLAIM_SCHEMA_VERSION, EVIDENCE_REQUIRED_FIELDS,
    EVIDENCE_SCHEMA_VERSION, FAIL, MAX_REPAIR_ROUNDS, MODE_RANK, MODES, NO_CHANGE_TARGET_KINDS,
    PASS, PRODUCTION_SOURCE_PREFIXES, RERUN_CONTRACT, SNAPSHOT_IGNORED_NAMES,
    SNAPSHOT_IGNORED_PARTS, SNAPSHOT_IGNORED_SUFFIXES, STAGNATION_LIMIT, TARGET_CONTEXTS,
    TARGET_HEADINGS, TARGET_KINDS, TARGET_PLACEHOLDERS, TRANSITION_TARGET_KINDS, UPSTREAM_SKIPPED,
)
from quality_control.common import (
    _canonical_json_fingerprint, _clean_text, _evidence_file_hashes, _evidence_signing_key,
    _interpolate, _is_target_placeholder, _load_json, _now, _npm_executable,
    _python_selector_exists, _read, _safe_run_id, _safe_workspace_relative_json, _sha256_file,
    _sha256_text, _target_fingerprint, _target_metadata, _target_section,
    _verify_evidence_attestation, _write_evidence_attestation, verify_evidence_attestation,
)
from quality_control.contracts import (
    _allowed_change_paths, _load_claim_manifest, _load_requirement_profile,
    _new_production_source_paths, _parse_target, _repair_change_fingerprint,
    _scope_violations, _snapshot_ignored, _target_identity, _validate_abstraction_record,
    _validate_replan_predecessor, _validate_source_claim_binding, _workspace_snapshot,
    workspace_snapshot,
)
from quality_control import environment as _quality_environment
from quality_control.environment import (
    _probe_http, _probe_tcp_url, _python_ast_parse, _run_shell,
    _runtime_environment_block_evidence, _structured_stdout_payload, _terminate_process_group,
)
from quality_control.dimensions import (
    _dimension_decision, _production_certification_dimension,
    _quality_dimensions, _real_model_certification_dimension,
)
from quality_control.claims import (
    _claim_evidence_ref_statuses, _claim_results, _downstream_steps, _gate_contract_fingerprints,
    _gate_is_environment_blocked, _junit_case_index, _run_step, _steps_for_mode,
    _test_module_from_ref, _validate_claim_gate_contracts, _validate_policy, _write_step_evidence,
)
from quality_control.convergence import (
    _advance_convergence_state, _decision, _failure_classification, _failure_metrics,
)
from quality_control.state import (
    _blocked_prerequisite_result, _load_baseline, _load_loop_state, _repair_plan, _state_path,
    _verify_loop_round, _workspace_immutability_result, _write_loop_state,
)


def _environment_problem(workspace: Path, step: dict[str, Any]) -> list[str]:
    # Preserve the historical monkeypatch surface on scripts.quality_loop.shutil.
    _quality_environment.shutil = shutil
    return _quality_environment._environment_problem(workspace, step)





class QualityRunConflictError(RuntimeError):
    """Raised when another controller already owns this target/evidence run."""




















































































































































def _run_loop_unlocked(
    workspace: Path,
    policy_path: Path,
    *,
    mode: str,
    evidence_dir: Path,
    rerun_from: str | None,
    target_path: Path,
    baseline: bool,
    baseline_evidence: Path | None,
    prior_evidence: Path | None,
    state_dir: Path,
) -> dict[str, Any]:
    if prior_evidence is not None:
        raise ValueError("--prior-evidence is no longer supported; every targeted run executes its dependency closure and only a full current-source run can complete a target")
    policy = _load_json(policy_path)
    ordered_steps = _validate_policy(policy)
    if mode == "release" and target_path is not None:
        configured_signing_key = os.getenv("QUALITY_EVIDENCE_SIGNING_KEY", "")
        if len(configured_signing_key.encode("utf-8")) < 32:
            raise ValueError("release mode requires QUALITY_EVIDENCE_SIGNING_KEY with at least 32 bytes; local mutable trust keys cannot authorize a protected release")
    target = _parse_target(target_path, workspace=workspace)
    replan_predecessor = _validate_replan_predecessor(workspace, target=target)
    _validate_claim_gate_contracts(target, ordered_steps)
    all_steps = _steps_for_mode(ordered_steps, mode)
    if (
        not baseline
        and rerun_from is None
        and MODE_RANK[mode] < MODE_RANK[str(target["minimum_mode"])]
    ):
        raise ValueError(
            f"target requires 最低质量模式：{target['minimum_mode']}; --mode {mode} is insufficient"
        )
    policy_fingerprint = _sha256_file(policy_path)
    baseline_record, state = _verify_loop_round(
        target=target,
        baseline_evidence=baseline_evidence,
        policy_fingerprint=policy_fingerprint,
        state_dir=state_dir,
        baseline=baseline,
        workspace=workspace,
        target_path=target_path,
    )
    ignored_snapshot_roots = tuple(
        path for path in (evidence_dir, baseline_evidence) if path is not None
    )
    run_start_snapshot = _workspace_snapshot(workspace, ignored_roots=ignored_snapshot_roots)
    baseline_snapshot = run_start_snapshot if baseline and target["context"] == "local-change" else None
    steps = _downstream_steps(all_steps, rerun_from) if rerun_from else all_steps
    evidence_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target_path, evidence_dir / "target.md")
    shutil.copyfile(policy_path, evidence_dir / "quality-loop-policy.json")
    claim_manifest_evidence_file = "claim-manifest.json"
    shutil.copyfile(
        workspace / str(target["claim_manifest"]),
        evidence_dir / claim_manifest_evidence_file,
    )
    requirement_catalog_evidence_file: str | None = None
    if target.get("requirement_catalog"):
        requirement_catalog_evidence_file = "requirement-catalog.json"
        shutil.copyfile(
            workspace / str(target["requirement_catalog"]),
            evidence_dir / requirement_catalog_evidence_file,
        )

    # Targeted runs re-execute their entire dependency closure.  Historical PASS
    # rows and copied JUnit/coverage files are never accepted as prerequisites.
    reusable: dict[str, dict[str, Any]] = {}

    results: list[dict[str, Any]] = []
    result_by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        dependencies = [str(dep) for dep in step.get("depends_on") or []]
        absent_prerequisites = [dep for dep in dependencies if dep not in result_by_id and dep not in reusable]
        failed_dependencies = [
            dep
            for dep in dependencies
            if dep in result_by_id and result_by_id[dep]["status"] != PASS
        ]
        if absent_prerequisites:
            result = _blocked_prerequisite_result(
                step,
                absent_prerequisites,
                "the selected dependency closure is internally incomplete",
            )
        elif failed_dependencies:
            result = {
                "id": step["id"],
                "name": step.get("name", step["id"]),
                "status": UPSTREAM_SKIPPED,
                "owner": step.get("owner", "unassigned"),
                "category": step.get("category", "verification"),
                "blocking_level": step.get("blocking_level", "required"),
                "repair_playbook": step.get("repair_playbook", "repair the failed upstream gate"),
                "depends_on": dependencies,
                "started_at": _now(),
                "ended_at": _now(),
                "exit_code": None,
                "duration_ms": 0,
                "stdout": "",
                "stderr": "upstream gate did not pass: " + ", ".join(failed_dependencies),
                "metadata": {"failed_dependencies": failed_dependencies},
            }
        else:
            print(f"[quality-loop] {mode} running {step['id']}", file=sys.stderr, flush=True)
            result = _run_step(workspace, evidence_dir, mode, step)
            print(f"[quality-loop] {step['id']} -> {result['status']}", file=sys.stderr, flush=True)
        results.append(result)
        result_by_id[str(result["id"])] = result
        _write_step_evidence(evidence_dir, result)

    run_end_snapshot = _workspace_snapshot(workspace, ignored_roots=ignored_snapshot_roots)
    if run_end_snapshot["fingerprint"] != run_start_snapshot["fingerprint"]:
        immutability_result = _workspace_immutability_result(run_start_snapshot, run_end_snapshot)
        results.append(immutability_result)
        result_by_id[str(immutability_result["id"])] = immutability_result
        _write_step_evidence(evidence_dir, immutability_result)

    decision = _decision(results)
    selected_gate_ids = [str(step["id"]) for step in steps]
    required_gate_ids = [
        str(step["id"])
        for step in all_steps
        if str(step.get("blocking_level") or "required") in BLOCKING_LEVELS
    ]
    result_statuses = {str(result["id"]): str(result["status"]) for result in results}
    claim_results = _claim_results(
        target,
        result_by_id,
        mode=mode,
        evidence_dir=evidence_dir,
        snapshot_files=set(run_end_snapshot["files"]),
    )
    unverified_claim_ids = [
        str(item["id"]) for item in claim_results if item["status"] != "VERIFIED"
    ]
    baseline_transition_unverified_claim_ids: list[str] = []
    if not baseline and target["kind"] in TRANSITION_TARGET_KINDS:
        baseline_claim_statuses = (baseline_record or {}).get("baseline_claim_statuses") or {}
        baseline_transition_unverified_claim_ids = [
            str(claim["id"])
            for claim in target["claims"]
            if claim["closure_requirement"] == "regression-transition"
            and baseline_claim_statuses.get(str(claim["id"])) != "FAILED"
        ]
    complete_gate_set = rerun_from is None and all(
        result_statuses.get(gate_id) == PASS for gate_id in required_gate_ids
    )
    completion_eligible = bool(
        not baseline
        and decision == PASS
        and complete_gate_set
        and MODE_RANK[mode] >= MODE_RANK[str(target["minimum_mode"])]
        and not unverified_claim_ids
        and not baseline_transition_unverified_claim_ids
    )

    loop_status: str | None = None
    if target["context"] == "local-change":
        if baseline:
            if target["kind"] in TRANSITION_TARGET_KINDS and (
                decision != FAIL
                or not any(item["status"] == "FAILED" for item in claim_results)
            ):
                raise ValueError(
                    f"{target['kind']} target baseline must reproduce at least one failing acceptance claim"
                )
            loop_status = "BASELINE_RECORDED"
            if baseline_snapshot is None:  # defensive: local baseline always creates one
                raise RuntimeError("local baseline did not capture a workspace source snapshot")
            snapshot_file = "workspace-baseline.json"
            (evidence_dir / snapshot_file).write_text(
                json.dumps(baseline_snapshot, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            baseline_record = {
                "schema_version": 2,
                "generated_at": _now(),
                "decision": decision,
                "evidence_dir": str(evidence_dir),
                "target_identity": _target_identity(target),
                "policy_fingerprint": policy_fingerprint,
                "mode": mode,
                "replan_predecessor": replan_predecessor,
                "workspace_snapshot_file": snapshot_file,
                "workspace_snapshot_fingerprint": baseline_snapshot["fingerprint"],
            }
            (evidence_dir / "baseline-record.json").write_text(
                json.dumps(baseline_record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _write_loop_state(
                state_dir,
                target,
                {
                    "schema_version": 2,
                    "target_identity": _target_identity(target),
                    "policy_fingerprint": policy_fingerprint,
                    "baseline_evidence": str(evidence_dir),
                    "baseline_decision": decision,
                    "replan_predecessor": replan_predecessor,
                    "required_round": 1,
                    "completed": False,
                    "stopped": False,
                    "stagnant_rounds": 0,
                    "round_history": [],
                    "updated_at": _now(),
                },
            )
        elif rerun_from is not None:
            # A focused rerun is diagnostic evidence only.  It must never consume
            # a repair round or close the target; the same source snapshot still
            # needs one complete minimum-mode pass.
            if decision == PASS:
                loop_status = "TARGETED_REGRESSION_PASSED"
            elif decision == BLOCKED:
                loop_status = "TARGETED_REGRESSION_BLOCKED"
            else:
                loop_status = "TARGETED_REGRESSION_FAILED"
        elif state is not None:
            state = dict(state)
            state["updated_at"] = _now()
            state["last_evidence"] = str(evidence_dir)
            state["last_repair_fingerprint"] = state.pop("current_repair_fingerprint", None)
            state["last_repair_paths"] = state.pop("current_repair_paths", [])
            convergence: dict[str, Any] | None = None
            if completion_eligible:
                loop_status = "CONVERGED"
                state.update(
                    {
                        "completed": True,
                        "stopped": False,
                        "stop_reason": None,
                        "required_round": int(target["current_round"]),
                        "stagnant_rounds": 0,
                    }
                )
            elif decision == FAIL:
                convergence = _advance_convergence_state(
                    state,
                    current_round=int(target["current_round"]),
                    results=results,
                )
                if int(target["current_round"]) >= int(target["max_rounds"]):
                    loop_status = "STOPPED_MAX_REPAIRS"
                    state.update(
                        {
                            "stopped": True,
                            "completed": False,
                            "stop_reason": "max repair rounds reached",
                            "required_round": int(target["current_round"]),
                        }
                    )
                elif int(convergence["stagnant_rounds"]) >= STAGNATION_LIMIT:
                    loop_status = "ARCHITECTURE_REPLAN_REQUIRED"
                    state.update(
                        {
                            "stopped": True,
                            "completed": False,
                            "stop_reason": "two consecutive rounds without measurable improvement",
                            "required_round": int(target["current_round"]),
                        }
                    )
                else:
                    loop_status = "REPAIR_REQUIRED"
                    state.update(
                        {
                            "stopped": False,
                            "completed": False,
                            "stop_reason": None,
                            "required_round": int(target["current_round"]) + 1,
                        }
                    )
            else:
                loop_status = "BLOCKED_BY_ENVIRONMENT"
            _write_loop_state(state_dir, target, state)
    else:
        if rerun_from is not None:
            loop_status = (
                "TARGETED_REGRESSION_PASSED"
                if decision == PASS
                else "TARGETED_REGRESSION_BLOCKED"
                if decision == BLOCKED
                else "TARGETED_REGRESSION_FAILED"
            )
        elif completion_eligible:
            loop_status = "CI_VERIFIED"
        elif decision == BLOCKED:
            loop_status = "BLOCKED_BY_ENVIRONMENT"
        elif decision == FAIL:
            loop_status = "CI_FAILED"

    if baseline:
        verification_snapshot = baseline_snapshot
        workspace_snapshot_file = "workspace-baseline.json"
    else:
        verification_snapshot = run_end_snapshot
        workspace_snapshot_file = "workspace-snapshot.json"
        (evidence_dir / workspace_snapshot_file).write_text(
            json.dumps(verification_snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if verification_snapshot is None:
        raise RuntimeError("quality run did not capture a workspace source snapshot")

    summary = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": _now(),
        "workspace_version": _read(workspace / "VERSION").strip() or str(policy.get("version") or "unknown"),
        "ci_run_identity_fingerprint_sha256": str(os.getenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT") or "").strip().casefold() or None,
        "mode": mode,
        "run_kind": "baseline" if baseline else "verification",
        "decision": decision,
        "loop_status": loop_status,
        "evidence_dir": str(evidence_dir),
        "judge_identity": {
            "root": os.getenv("SKILL_JUDGE_ROOT", str(Path(__file__).resolve().parents[1])),
            "trust_mode": os.getenv("SKILL_JUDGE_TRUST_MODE", "workspace-fallback"),
            "controller_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "target": str(target_path),
        "target_identity": _target_identity(target),
        "target_minimum_mode_declared": target["minimum_mode_declared"],
        "target_minimum_mode_derived": target["minimum_mode_derived"],
        "target_minimum_mode_effective": target["minimum_mode"],
        "replan_predecessor": replan_predecessor,
        "claim_manifest": target["claim_manifest"],
        "claim_manifest_fingerprint": target["claim_manifest_fingerprint"],
        "claim_manifest_evidence_file": claim_manifest_evidence_file,
        "requirement_catalog": target.get("requirement_catalog"),
        "requirement_profile": target.get("requirement_profile"),
        "requirement_catalog_fingerprint": target.get(
            "requirement_catalog_fingerprint"
        ),
        "requirement_catalog_evidence_file": requirement_catalog_evidence_file,
        "claim_results": claim_results,
        "unverified_claim_ids": unverified_claim_ids,
        "baseline_transition_unverified_claim_ids": baseline_transition_unverified_claim_ids,
        "policy_fingerprint": policy_fingerprint,
        "rerun_from": rerun_from,
        "prior_evidence": None,
        "reused_prerequisites": [],
        "missing_prerequisites": [],
        "workspace_snapshot_start_fingerprint": run_start_snapshot["fingerprint"],
        "workspace_snapshot_fingerprint": verification_snapshot["fingerprint"],
        "workspace_snapshot_file": workspace_snapshot_file,
        "selected_gate_ids": selected_gate_ids,
        "required_gate_ids": required_gate_ids,
        "gate_contract_fingerprints": _gate_contract_fingerprints(all_steps),
        "completion_eligible": completion_eligible,
        "quality_dimensions": _quality_dimensions(results, mode=mode),
        "evidence_attestation_file": "evidence-attestation.json",
        "convergence": (
            {
                "current_round": int(target["current_round"]),
                "max_rounds": int(target["max_rounds"]),
                "stagnant_rounds": int((state or {}).get("stagnant_rounds") or 0),
                "last_failure_count": (state or {}).get("last_failure_count"),
                "failed_gate_ids": list((state or {}).get("failed_gate_ids") or []),
                "upstream_skipped_gate_ids": list(
                    (state or {}).get("upstream_skipped_gate_ids") or []
                ),
            }
            if target["context"] == "local-change"
            else None
        ),
        "results": results,
    }
    (evidence_dir / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    repair = _repair_plan(
        results,
        workspace=workspace,
        policy_path=policy_path,
        mode=mode,
        target_path=target_path,
        target=target,
        evidence_dir=evidence_dir,
        baseline_evidence=baseline_evidence,
        loop_status=loop_status,
    )
    (evidence_dir / "repair-plan.json").write_text(
        json.dumps(repair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_evidence_attestation(workspace, evidence_dir)
    return summary



@contextlib.contextmanager
def _exclusive_quality_run(
    *,
    evidence_dir: Path,
    state_dir: Path,
    target_path: Path,
):
    """Prevent concurrent controllers from mutating the same evidence or target state.

    Locks live outside the governed evidence directory so a rejected contender
    cannot change or invalidate the active run's attestation.  Both the output
    directory and target/state identity are locked; acquiring in sorted path
    order prevents deadlocks when two invocations overlap both resources.
    """
    evidence_lock = evidence_dir.parent / f".{evidence_dir.name}.quality-run.lock"
    target_digest = hashlib.sha256(str(target_path.resolve()).encode("utf-8")).hexdigest()
    state_lock = state_dir / ".run-locks" / f"{target_digest}.lock"
    lock_paths = sorted({evidence_lock.resolve(), state_lock.resolve()}, key=str)
    held: list[tuple[int, Path]] = []
    try:
        for lock_path in lock_paths:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(descriptor)
                owner = ""
                try:
                    owner = lock_path.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                detail = f"; active owner: {owner}" if owner else ""
                raise QualityRunConflictError(
                    f"another quality controller already owns {lock_path}{detail}"
                ) from exc
            metadata = json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": _now(),
                    "evidence_dir": str(evidence_dir),
                    "state_dir": str(state_dir),
                    "target": str(target_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            os.ftruncate(descriptor, 0)
            os.write(descriptor, metadata.encode("utf-8"))
            os.fsync(descriptor)
            held.append((descriptor, lock_path))
        yield
    finally:
        for descriptor, _lock_path in reversed(held):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def run_loop(
    workspace: Path,
    policy_path: Path,
    *,
    mode: str,
    evidence_dir: Path,
    rerun_from: str | None,
    target_path: Path,
    baseline: bool,
    baseline_evidence: Path | None,
    prior_evidence: Path | None,
    state_dir: Path,
) -> dict[str, Any]:
    with _exclusive_quality_run(
        evidence_dir=evidence_dir,
        state_dir=state_dir,
        target_path=target_path,
    ):
        if evidence_dir.exists() and any(evidence_dir.iterdir()):
            raise QualityRunConflictError(
                f"evidence directory must be new and empty: {evidence_dir}"
            )
        return _run_loop_unlocked(
            workspace,
            policy_path,
            mode=mode,
            evidence_dir=evidence_dir,
            rerun_from=rerun_from,
            target_path=target_path,
            baseline=baseline,
            baseline_evidence=baseline_evidence,
            prior_evidence=prior_evidence,
            state_dir=state_dir,
        )

def _controller_failure(
    *,
    workspace: Path,
    policy_path: Path,
    mode: str,
    evidence_dir: Path,
    target_path: Path | None,
    baseline_evidence: Path | None,
    prior_evidence: Path | None,
    error: Exception,
) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "id": "quality-controller-input",
        "name": "quality-controller-input",
        "status": FAIL,
        "owner": "scripts/quality_loop.py",
        "category": "controller",
        "blocking_level": "required",
        "repair_playbook": "repair the target, policy or baseline record before running a gate",
        "depends_on": [],
        "started_at": _now(),
        "ended_at": _now(),
        "exit_code": 2,
        "duration_ms": 0,
        "stdout": "",
        "stderr": str(error),
        "metadata": {"exception": error.__class__.__name__},
    }
    _write_step_evidence(evidence_dir, result)
    snapshot = _workspace_snapshot(
        workspace,
        ignored_roots=tuple(
            path for path in (evidence_dir, baseline_evidence) if path is not None
        ),
    )
    snapshot_file = "workspace-snapshot.json"
    (evidence_dir / snapshot_file).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    controller_contract = {
        "id": "quality-controller-input",
        "owner": "scripts/quality_loop.py",
        "category": "controller",
        "blocking_level": "required",
        "contract": "validate target, policy, baseline, protected release key and rerun semantics before gates",
    }
    target_identity = None
    target_contract: dict[str, Any] | None = None
    if target_path is not None and target_path.is_file():
        try:
            target_contract = _parse_target(target_path, workspace=workspace)
            target_identity = _target_identity(target_contract)
        except (OSError, ValueError):
            target_identity = None
            target_contract = None
    policy_fingerprint = _sha256_file(policy_path) if policy_path.is_file() else None
    summary = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": _now(),
        "workspace_version": _read(workspace / "VERSION").strip() or "unknown",
        "ci_run_identity_fingerprint_sha256": str(os.getenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT") or "").strip().casefold() or None,
        "mode": mode,
        "run_kind": "verification",
        "decision": FAIL,
        "loop_status": "INVALID_INPUT",
        "evidence_dir": str(evidence_dir),
        "judge_identity": {
            "root": os.getenv("SKILL_JUDGE_ROOT", str(Path(__file__).resolve().parents[1])),
            "trust_mode": os.getenv("SKILL_JUDGE_TRUST_MODE", "workspace-fallback"),
            "controller_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "target": str(target_path) if target_path else None,
        "target_identity": target_identity,
        "target_minimum_mode_declared": (
            target_contract.get("minimum_mode_declared") if target_contract else None
        ),
        "target_minimum_mode_derived": (
            target_contract.get("minimum_mode_derived") if target_contract else None
        ),
        "target_minimum_mode_effective": (
            target_contract.get("minimum_mode") if target_contract else None
        ),
        "replan_predecessor": None,
        "claim_manifest": target_contract.get("claim_manifest") if target_contract else None,
        "claim_manifest_fingerprint": (
            target_contract.get("claim_manifest_fingerprint") if target_contract else None
        ),
        "claim_manifest_evidence_file": None,
        "claim_results": [],
        "unverified_claim_ids": [],
        "policy_fingerprint": policy_fingerprint,
        "rerun_from": None,
        "prior_evidence": str(prior_evidence) if prior_evidence else None,
        "reused_prerequisites": [],
        "missing_prerequisites": [],
        "workspace_snapshot_start_fingerprint": snapshot["fingerprint"],
        "workspace_snapshot_fingerprint": snapshot["fingerprint"],
        "workspace_snapshot_file": snapshot_file,
        "selected_gate_ids": ["quality-controller-input"],
        "required_gate_ids": ["quality-controller-input"],
        "gate_contract_fingerprints": {
            "quality-controller-input": _sha256_text(
                json.dumps(controller_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
        },
        "completion_eligible": False,
        "evidence_attestation_file": "evidence-attestation.json",
        "results": [result],
    }
    (evidence_dir / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    repair = _repair_plan(
        [result],
        workspace=workspace,
        policy_path=policy_path,
        mode=mode,
        target_path=target_path,
        target=None,
        evidence_dir=evidence_dir,
        baseline_evidence=baseline_evidence,
        loop_status="INVALID_INPUT",
    )
    (evidence_dir / "repair-plan.json").write_text(
        json.dumps(repair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_evidence_attestation(workspace, evidence_dir)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded, reproducible quality validation pass.")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--policy", default="governance/quality-loop-policy.json")
    parser.add_argument("--mode", choices=MODES, default="static")
    parser.add_argument("--evidence-dir")
    parser.add_argument("--target", help="required filled quality-loop target markdown record")
    parser.add_argument("--baseline", action="store_true", help="record this pass as the pre-change baseline")
    parser.add_argument("--baseline-evidence", help="evidence directory from the required local baseline pass")
    parser.add_argument("--prior-evidence", help="removed compatibility flag; supplying it is an error because historical PASS evidence is never reused")
    parser.add_argument("--state-dir", help="local quality-loop state directory; defaults to .quality/loop-state")
    parser.add_argument(
        "--rerun-from",
        help="rerun this gate, all dependency ancestors, and all policy-declared downstream gates",
    )
    parser.add_argument("--list-steps", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    policy_path = (workspace / args.policy).resolve()
    judge_root_raw = os.environ.get("SKILL_JUDGE_ROOT")
    judge_trust_mode = os.environ.get("SKILL_JUDGE_TRUST_MODE", "workspace-fallback")
    if judge_trust_mode == "external-readonly":
        if not judge_root_raw:
            print(json.dumps({"decision": FAIL, "error": "external Judge root is missing"}, ensure_ascii=False), file=sys.stderr)
            return 1
        trust_errors = verify_trusted_candidate(workspace, Path(judge_root_raw).resolve())
        if trust_errors:
            print(
                json.dumps(
                    {"decision": FAIL, "loop_status": "TRUSTED_JUDGE_INPUT_CHANGED", "errors": trust_errors},
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 1
    if args.list_steps:
        try:
            steps = _steps_for_mode(_validate_policy(_load_json(policy_path)), args.mode)
            if args.rerun_from:
                steps = _downstream_steps(steps, args.rerun_from)
            print(json.dumps({"mode": args.mode, "steps": [step["id"] for step in steps]}, ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"decision": FAIL, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
    evidence_raw = args.evidence_dir or os.getenv("QUALITY_EVIDENCE_DIR")
    evidence_dir = Path(evidence_raw).expanduser().resolve() if evidence_raw else workspace / ".quality" / "evidence" / _safe_run_id()
    target_path = Path(args.target).expanduser().resolve() if args.target else None
    baseline_evidence = Path(args.baseline_evidence).expanduser().resolve() if args.baseline_evidence else None
    prior_evidence = Path(args.prior_evidence).expanduser().resolve() if args.prior_evidence else None
    state_raw = args.state_dir or os.getenv("QUALITY_LOOP_STATE_DIR")
    state_dir = Path(state_raw).expanduser().resolve() if state_raw else workspace / ".quality" / "loop-state"
    try:
        if target_path is None:
            raise ValueError("--target is required for every quality validation run")
        summary = run_loop(
            workspace,
            policy_path,
            mode=args.mode,
            evidence_dir=evidence_dir,
            rerun_from=args.rerun_from,
            target_path=target_path,
            baseline=args.baseline,
            baseline_evidence=baseline_evidence,
            prior_evidence=prior_evidence,
            state_dir=state_dir,
        )
    except QualityRunConflictError as exc:
        # A rejected contender must never write into the active run's evidence
        # directory.  Report the conflict only on this invocation's stdout.
        summary = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "generated_at": _now(),
            "workspace_version": _read(workspace / "VERSION").strip() or "unknown",
            "ci_run_identity_fingerprint_sha256": str(os.getenv("PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT") or "").strip().casefold() or None,
            "mode": args.mode,
            "run_kind": "controller-rejection",
            "decision": FAIL,
            "loop_status": "CONCURRENT_RUN_REJECTED",
            "evidence_dir": str(evidence_dir),
            "completion_eligible": False,
            "error": str(exc),
            "results": [
                {
                    "id": "quality-controller-lock",
                    "status": FAIL,
                    "stderr": str(exc),
                }
            ],
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        summary = _controller_failure(
            workspace=workspace,
            policy_path=policy_path,
            mode=args.mode,
            evidence_dir=evidence_dir,
            target_path=target_path,
            baseline_evidence=baseline_evidence,
            prior_evidence=prior_evidence,
            error=exc,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["decision"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
