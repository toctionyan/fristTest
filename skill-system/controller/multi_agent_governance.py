from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from .agent_attestation import (
        IMPLEMENTER_ROLE,
        case_dir_from_contract,
        file_sha256,
        load_manifest,
        validate_role_separation,
        validate_stage,
    )
    from .candidate_freeze import validate_candidate_freeze
    from .repair_governance import validate_begin_ready, validate_verification_ready
except ImportError:
    from agent_attestation import (  # type: ignore
        IMPLEMENTER_ROLE,
        case_dir_from_contract,
        file_sha256,
        load_manifest,
        validate_role_separation,
        validate_stage,
    )
    from candidate_freeze import validate_candidate_freeze  # type: ignore
    from repair_governance import validate_begin_ready, validate_verification_ready  # type: ignore


def current_agent_identity(payload: dict[str, Any] | None = None) -> dict[str, str]:
    payload = payload or {}
    aliases = {
        "role": ("agent_name", "subagent_name", "agent", "role"),
        "task_id": ("task_id", "agent_task_id", "codex_task_id"),
        "thread_id": ("thread_id", "agent_thread_id", "codex_thread_id"),
        "worktree_id": ("worktree_id", "workspace_id", "codex_worktree_id"),
    }
    env = {
        "role": ("CODEX_AGENT_ROLE", "AGENT_ROLE"),
        "task_id": ("CODEX_TASK_ID", "AGENT_TASK_ID"),
        "thread_id": ("CODEX_THREAD_ID", "AGENT_THREAD_ID"),
        "worktree_id": ("CODEX_WORKTREE_ID", "AGENT_WORKTREE_ID"),
    }
    result: dict[str, str] = {}
    for key, names in aliases.items():
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                normalized = value.strip()
                result[key] = normalized.replace("_", "-") if key == "role" else normalized
                break
        if key not in result:
            for name in env[key]:
                value = os.environ.get(name, "").strip()
                if value:
                    result[key] = value.replace("_", "-") if key == "role" else value
                    break
    return result


def _require_current_implementer(identity: dict[str, str], stage: dict[str, Any]) -> None:
    role = identity.get("role")
    if role and role != IMPLEMENTER_ROLE:
        raise ValueError(f"writable product transition requires {IMPLEMENTER_ROLE}, not {role}")
    for key in ("task_id", "thread_id", "worktree_id"):
        actual = identity.get(key)
        expected = str(stage.get(key) or "")
        if not actual:
            raise ValueError(f"Codex multi-agent mode requires current {key}")
        if actual != expected:
            raise ValueError(f"current implementer {key} differs from the registered task")


def validate_plan_review_artifact(workspace: Path, contract_payload: dict[str, Any]) -> None:
    stage = validate_stage(workspace, contract_payload, "repair-plan-reviewer")
    artifact = json.loads(stage["artifact"].read_text(encoding="utf-8"))
    if artifact.get("reviewer_role") != "repair-plan-reviewer":
        raise ValueError("imported plan review lacks repair-plan-reviewer identity")
    if artifact.get("decision") != "APPROVED":
        raise ValueError("independent repair plan reviewer did not approve the plan")
    case_dir = case_dir_from_contract(workspace, contract_payload)
    plan_path = case_dir / "repair-plan.json"
    if artifact.get("repair_plan_sha256") != file_sha256(plan_path):
        raise ValueError("independent plan review is bound to a stale repair plan")
    if stage["attestation"].get("input_sha256") != file_sha256(plan_path):
        raise ValueError("repair plan reviewer input digest differs from repair-plan.json")


def validate_failure_explorer_artifact(workspace: Path, contract_payload: dict[str, Any]) -> None:
    stage = validate_stage(workspace, contract_payload, "failure-explorer")
    artifact = json.loads(stage["artifact"].read_text(encoding="utf-8"))
    if artifact.get("record_type") != "root-cause-proof":
        raise ValueError("failure explorer output must be root-cause-proof.json")
    if artifact.get("decision") != "PROVEN":
        raise ValueError("failure explorer did not prove the root cause")
    case_dir = case_dir_from_contract(workspace, contract_payload)
    failure_path = case_dir / "failure-case.json"
    if artifact.get("failure_case_sha256") != file_sha256(failure_path):
        raise ValueError("failure explorer root cause is bound to a stale failure case")
    if stage["attestation"].get("input_sha256") != file_sha256(failure_path):
        raise ValueError("failure explorer input digest differs from failure-case.json")


def multi_agent_required(contract_payload: dict[str, Any]) -> bool:
    return str(contract_payload.get("multi_agent_mode") or "") == "required"


def validate_multi_agent_begin_ready(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    deterministic = validate_begin_ready(workspace, contract_payload)
    if not multi_agent_required(contract_payload):
        return {**deterministic, "multi_agent_status": "LEGACY_NOT_REQUIRED"}
    validate_failure_explorer_artifact(workspace, contract_payload)
    validate_plan_review_artifact(workspace, contract_payload)
    implementer = validate_stage(workspace, contract_payload, IMPLEMENTER_ROLE)
    manifest = implementer["manifest"]
    validate_role_separation(
        manifest,
        {"failure-explorer", "repair-plan-reviewer", IMPLEMENTER_ROLE},
    )
    _require_current_implementer(identity or current_agent_identity(), implementer["stage"])
    return {
        **deterministic,
        "multi_agent_status": "PASS",
        "implementer_task_id": implementer["stage"].get("task_id"),
        "task_manifest": (
            case_dir_from_contract(workspace, contract_payload) / "agent-task-manifest.json"
        ).relative_to(workspace).as_posix(),
    }


def validate_semantic_diff_review(workspace: Path, contract_payload: dict[str, Any], freeze: dict[str, Any]) -> None:
    stage = validate_stage(
        workspace,
        contract_payload,
        "diff-integrity-reviewer",
        expected_candidate_commit=str(freeze.get("candidate_commit") or ""),
    )
    artifact = json.loads(stage["artifact"].read_text(encoding="utf-8"))
    if artifact.get("record_type") != "semantic-diff-review":
        raise ValueError("diff reviewer output must be semantic-diff-review")
    if artifact.get("reviewer_role") != "diff-integrity-reviewer":
        raise ValueError("semantic diff review has the wrong reviewer role")
    if artifact.get("decision") != "PASS":
        raise ValueError("independent semantic diff review did not PASS")
    if artifact.get("candidate_commit") != freeze.get("candidate_commit"):
        raise ValueError("semantic diff review is bound to a different candidate")
    if artifact.get("candidate_source_fingerprint") != freeze.get("candidate_source_fingerprint"):
        raise ValueError("semantic diff review source fingerprint differs from candidate freeze")
    case_dir = case_dir_from_contract(workspace, contract_payload)
    deterministic_diff = case_dir / "diff-review.json"
    if artifact.get("deterministic_diff_review_sha256") != file_sha256(deterministic_diff):
        raise ValueError("semantic diff review is bound to a stale deterministic diff scan")
    if stage["attestation"].get("input_sha256") != file_sha256(deterministic_diff):
        raise ValueError("diff reviewer input digest differs from diff-review.json")


def validate_closure_decision(
    workspace: Path,
    contract_payload: dict[str, Any],
    freeze: dict[str, Any],
    *,
    expected_result: str,
) -> None:
    stage = validate_stage(
        workspace,
        contract_payload,
        "closure-arbiter",
        expected_candidate_commit=str(freeze.get("candidate_commit") or ""),
    )
    artifact = json.loads(stage["artifact"].read_text(encoding="utf-8"))
    if artifact.get("record_type") != "closure-decision":
        raise ValueError("closure arbiter output must be closure-decision")
    if artifact.get("reviewer_role") != "closure-arbiter":
        raise ValueError("closure decision has the wrong reviewer role")
    expected_decision = "CLOSED_VERIFIED" if expected_result == "CONVERGED" else None
    if expected_decision and artifact.get("decision") != expected_decision:
        raise ValueError("independent closure arbiter did not close the candidate")
    if artifact.get("candidate_commit") != freeze.get("candidate_commit"):
        raise ValueError("closure decision is bound to a different candidate")
    case_dir = case_dir_from_contract(workspace, contract_payload)
    closure_matrix = case_dir / "closure-matrix.json"
    if artifact.get("closure_matrix_sha256") != file_sha256(closure_matrix):
        raise ValueError("closure decision is bound to a stale closure matrix")
    if stage["attestation"].get("input_sha256") != file_sha256(closure_matrix):
        raise ValueError("closure arbiter input digest differs from closure-matrix.json")


def validate_multi_agent_verification_ready(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    expected_result: str,
) -> dict[str, Any]:
    deterministic = validate_verification_ready(
        workspace,
        contract_payload,
        expected_result=expected_result,
    )
    if not multi_agent_required(contract_payload):
        return {**deterministic, "multi_agent_status": "LEGACY_NOT_REQUIRED"}
    freeze = validate_candidate_freeze(workspace, contract_payload)
    validate_semantic_diff_review(workspace, contract_payload, freeze)
    validate_closure_decision(
        workspace,
        contract_payload,
        freeze,
        expected_result=expected_result,
    )
    manifest = load_manifest(case_dir_from_contract(workspace, contract_payload))
    validate_role_separation(
        manifest,
        {
            "failure-explorer",
            "repair-plan-reviewer",
            IMPLEMENTER_ROLE,
            "diff-integrity-reviewer",
            "closure-arbiter",
        },
    )
    return {
        **deterministic,
        "multi_agent_status": "PASS",
        "candidate_commit": freeze.get("candidate_commit"),
        "candidate_source_fingerprint": freeze.get("candidate_source_fingerprint"),
    }
