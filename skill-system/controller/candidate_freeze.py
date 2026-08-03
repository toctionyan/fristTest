from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .agent_attestation import (
        IMPLEMENTER_ROLE,
        case_dir_from_contract,
        file_sha256,
        git_value,
        load_manifest,
        manifest_path,
        payload_digest,
        validate_role_separation,
        validate_stage,
    )
    from .repair_governance import (
        capture_allowed_manifest,
        capture_workspace_manifest,
        load_chain,
        manifest_fingerprint,
    )
except ImportError:
    from agent_attestation import (  # type: ignore
        IMPLEMENTER_ROLE,
        case_dir_from_contract,
        file_sha256,
        git_value,
        load_manifest,
        manifest_path,
        payload_digest,
        validate_role_separation,
        validate_stage,
    )
    from repair_governance import (  # type: ignore
        capture_allowed_manifest,
        capture_workspace_manifest,
        load_chain,
        manifest_fingerprint,
    )

FREEZE_FILE = "candidate-freeze.json"


def freeze_path(case_dir: Path) -> Path:
    return case_dir / FREEZE_FILE


def _load_freeze(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"candidate freeze is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("candidate freeze must be a JSON object")
    if payload.get("schema_version") != 1 or payload.get("record_type") != "candidate-freeze":
        raise ValueError("candidate freeze has invalid schema or record_type")
    if payload.get("freeze_digest") != payload_digest(payload, exclude={"freeze_digest"}):
        raise ValueError("candidate freeze digest is invalid")
    return payload


def freeze_candidate(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    candidate_commit: str | None = None,
) -> Path:
    workspace = workspace.resolve()
    chain = load_chain(workspace, contract_payload)
    implementer = validate_stage(workspace, contract_payload, IMPLEMENTER_ROLE)
    manifest = implementer["manifest"]
    validate_role_separation(
        manifest,
        {"failure-explorer", "repair-plan-reviewer", IMPLEMENTER_ROLE},
    )
    commit = candidate_commit or git_value(workspace, "rev-parse", "HEAD")
    tree = git_value(workspace, "rev-parse", f"{commit}^{{tree}}")
    workspace_files = capture_workspace_manifest(workspace)
    allowed = [str(value) for value in chain.permit.get("allowed_paths") or []]
    source_files = capture_allowed_manifest(workspace, allowed, workspace_files=workspace_files)
    source_fingerprint = manifest_fingerprint(source_files)
    if source_fingerprint == chain.permit.get("baseline_source_fingerprint"):
        raise ValueError("candidate freeze requires a permitted source change")
    payload = {
        "schema_version": 1,
        "record_type": "candidate-freeze",
        "change_id": contract_payload.get("change_id"),
        "permit_digest": chain.permit_digest,
        "baseline_commit": manifest.get("baseline_commit"),
        "candidate_commit": commit,
        "candidate_tree": tree,
        "candidate_source_fingerprint": source_fingerprint,
        "implementer_task_id": implementer["stage"].get("task_id"),
        "implementer_worktree_id": implementer["stage"].get("worktree_id"),
        "status": "FROZEN",
    }
    payload["freeze_digest"] = payload_digest(payload)
    case_dir = case_dir_from_contract(workspace, contract_payload)
    path = freeze_path(case_dir)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    current_manifest = load_manifest(case_dir)
    stages = dict(current_manifest.get("stages") or {})
    stage = dict(stages[IMPLEMENTER_ROLE])
    stage["status"] = "CANDIDATE_FROZEN"
    stage["decision"] = "CANDIDATE_FROZEN"
    stage["candidate_commit"] = commit
    stage["candidate_source_fingerprint"] = source_fingerprint
    stages[IMPLEMENTER_ROLE] = stage
    updated = {
        "schema_version": 1,
        "record_type": "agent-task-manifest",
        "change_id": current_manifest.get("change_id"),
        "repository": current_manifest.get("repository"),
        "baseline_commit": current_manifest.get("baseline_commit"),
        "stages": dict(sorted(stages.items())),
    }
    updated["manifest_digest"] = payload_digest(updated)
    manifest_path(case_dir).write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def validate_candidate_freeze(
    workspace: Path,
    contract_payload: dict[str, Any],
) -> dict[str, Any]:
    workspace = workspace.resolve()
    chain = load_chain(workspace, contract_payload)
    case_dir = case_dir_from_contract(workspace, contract_payload)
    payload = _load_freeze(freeze_path(case_dir))
    if payload.get("change_id") != contract_payload.get("change_id"):
        raise ValueError("candidate freeze change_id mismatch")
    if payload.get("permit_digest") != chain.permit_digest:
        raise ValueError("candidate freeze is not bound to the active ChangePermit")
    manifest = load_manifest(case_dir)
    if payload.get("baseline_commit") != manifest.get("baseline_commit"):
        raise ValueError("candidate freeze baseline differs from task manifest")
    implementer = validate_stage(workspace, contract_payload, IMPLEMENTER_ROLE)
    if payload.get("implementer_task_id") != implementer["stage"].get("task_id"):
        raise ValueError("candidate freeze implementer task mismatch")
    if payload.get("implementer_worktree_id") != implementer["stage"].get("worktree_id"):
        raise ValueError("candidate freeze implementer worktree mismatch")
    commit = str(payload.get("candidate_commit") or "")
    if len(commit) != 40:
        raise ValueError("candidate freeze lacks a full candidate commit")
    tree = git_value(workspace, "rev-parse", f"{commit}^{{tree}}")
    if payload.get("candidate_tree") != tree:
        raise ValueError("candidate freeze tree differs from candidate commit")
    # `merge-base --is-ancestor` is status-only; run it separately.
    import subprocess
    completed = subprocess.run(
        ["git", "-C", str(workspace), "merge-base", "--is-ancestor", commit, "HEAD"],
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise ValueError("frozen candidate commit is not an ancestor of the current worktree")
    current_files = capture_workspace_manifest(workspace)
    allowed = [str(value) for value in chain.permit.get("allowed_paths") or []]
    current_allowed = capture_allowed_manifest(workspace, allowed, workspace_files=current_files)
    fingerprint = manifest_fingerprint(current_allowed)
    if payload.get("candidate_source_fingerprint") != fingerprint:
        raise ValueError("governed source changed after candidate freeze")
    return payload
