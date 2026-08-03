from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REVIEWER_ROLES = {
    "failure-explorer",
    "repair-plan-reviewer",
    "diff-integrity-reviewer",
    "closure-arbiter",
}
IMPLEMENTER_ROLE = "product-implementer"
IMPORTER_ROLE = "review-importer"
ALL_AGENT_ROLES = REVIEWER_ROLES | {IMPLEMENTER_ROLE}
ROLE_ARTIFACTS = {
    "failure-explorer": "root-cause-proof.json",
    "repair-plan-reviewer": "plan-review.json",
    "diff-integrity-reviewer": "semantic-diff-review.json",
    "closure-arbiter": "closure-decision.json",
    "product-implementer": "implementer-registration.json",
}
ROLE_DECISIONS = {
    "failure-explorer": {"PROVEN", "UNPROVEN", "ORACLE_REVIEW_REQUIRED", "ENVIRONMENT_BLOCKED"},
    "repair-plan-reviewer": {
        "APPROVED",
        "REJECTED_ROOT_CAUSE_UNPROVEN",
        "REJECTED_SKILL_VIOLATION",
        "REJECTED_PATCH_LIKE_FIX",
        "REJECTED_SCOPE_ERROR",
        "REJECTED_MISSING_COUNTEREXAMPLES",
        "REJECTED_DUAL_AUTHORITY",
    },
    "diff-integrity-reviewer": {"PASS", "REJECT"},
    "closure-arbiter": {"CLOSED_VERIFIED", "BLOCKED", "NOT_CLOSED"},
    "product-implementer": {"STARTED", "CANDIDATE_FROZEN"},
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SIGNATURE_KEY_ENV = "MULTI_AGENT_ATTESTATION_KEY"
REQUIRE_SIGNATURE_ENV = "MULTI_AGENT_REQUIRE_SIGNATURE"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload_digest(value: dict[str, Any], *, exclude: Iterable[str] = ()) -> str:
    excluded = set(exclude)
    return hashlib.sha256(canonical_bytes({key: item for key, item in value.items() if key not in excluded})).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signature_payload(payload: dict[str, Any]) -> bytes:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "attestation_digest"
    }
    signature = unsigned.get("signature")
    if isinstance(signature, dict):
        unsigned["signature"] = {
            key: value for key, value in signature.items() if key != "value"
        }
    return canonical_bytes(unsigned)


def sign_attestation(payload: dict[str, Any], key: str, *, key_id: str = "trusted-codex-reviewer") -> dict[str, Any]:
    signed = dict(payload)
    signed["signature"] = {
        "algorithm": "hmac-sha256",
        "key_id": key_id,
    }
    signed["signature"]["value"] = hmac.new(
        key.encode("utf-8"), _signature_payload(signed), hashlib.sha256
    ).hexdigest()
    signed["attestation_digest"] = payload_digest(signed, exclude={"attestation_digest"})
    return signed


def _validate_signature(payload: dict[str, Any], role: str) -> None:
    require = (
        role in REVIEWER_ROLES
        and os.environ.get(REQUIRE_SIGNATURE_ENV, "").strip() == "1"
    )
    signature = payload.get("signature")
    if signature is None:
        if require:
            raise ValueError("reviewer agent-attestation requires a trusted signature")
        return
    if not isinstance(signature, dict) or signature.get("algorithm") != "hmac-sha256":
        raise ValueError("agent-attestation signature algorithm is invalid")
    key_id = str(signature.get("key_id") or "").strip()
    if not key_id:
        raise ValueError("agent-attestation signature requires key_id")
    key = os.environ.get(SIGNATURE_KEY_ENV, "")
    if not key:
        raise ValueError("agent-attestation signature cannot be verified without the trusted key")
    expected = hmac.new(key.encode("utf-8"), _signature_payload(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(signature.get("value") or ""), expected):
        raise ValueError("agent-attestation signature is invalid")


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"required multi-agent record is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"multi-agent record is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"multi-agent record must be a JSON object: {path}")
    return payload


def _safe_relative(workspace: Path, raw: object, *, label: str, must_exist: bool = True) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} is required")
    path = (workspace / raw).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the workspace") from exc
    if must_exist and not path.exists():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def git_value(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise ValueError(f"git {' '.join(args)} failed: {(completed.stderr or completed.stdout).strip()}")
    return completed.stdout.strip()


def repository_slug(workspace: Path) -> str:
    configured = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if configured:
        return configured
    remote = git_value(workspace, "config", "--get", "remote.origin.url")
    match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?$", remote)
    if not match:
        raise ValueError("cannot derive repository identity from GITHUB_REPOSITORY or remote.origin.url")
    return match.group(1)


def case_dir_from_contract(workspace: Path, contract_payload: dict[str, Any]) -> Path:
    path = _safe_relative(
        workspace,
        contract_payload.get("repair_governance"),
        label="repair_governance case directory",
    )
    try:
        path.relative_to(workspace.resolve() / "governance" / "repair-cases")
    except ValueError as exc:
        raise ValueError("multi-agent repair case must live under governance/repair-cases") from exc
    return path


def attestation_path(case_dir: Path, role: str) -> Path:
    return case_dir / "attestations" / f"{role}.json"


def manifest_path(case_dir: Path) -> Path:
    return case_dir / "agent-task-manifest.json"


def _require_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"agent-attestation requires {key}")
    return value


def _validate_sha(value: str, label: str) -> None:
    if not HEX64.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA256 digest")


def validate_attestation(
    payload: dict[str, Any],
    *,
    expected_role: str,
    expected_change_id: str,
    expected_repository: str,
    expected_artifact_sha256: str,
    expected_baseline_commit: str | None = None,
    expected_candidate_commit: str | None = None,
) -> dict[str, Any]:
    if expected_role not in ALL_AGENT_ROLES:
        raise ValueError(f"unsupported multi-agent role: {expected_role}")
    if payload.get("schema_version") != 1 or payload.get("record_type") != "agent-attestation":
        raise ValueError("invalid agent-attestation schema or record_type")
    if payload.get("change_id") != expected_change_id:
        raise ValueError("agent-attestation change_id mismatch")
    if payload.get("role") != expected_role:
        raise ValueError(f"agent-attestation role mismatch: expected {expected_role}")
    if payload.get("provider") not in {"codex-cloud", "codex-app", "codex-cli"}:
        raise ValueError("agent-attestation provider must identify a Codex execution host")
    if payload.get("repository") != expected_repository:
        raise ValueError("agent-attestation repository mismatch")
    task_id = _require_text(payload, "task_id")
    thread_id = _require_text(payload, "thread_id")
    worktree_id = _require_text(payload, "worktree_id")
    if len({task_id, thread_id, worktree_id}) < 3:
        raise ValueError("task_id, thread_id and worktree_id must be distinct identifiers")
    baseline_commit = _require_text(payload, "baseline_commit")
    if not HEX40.fullmatch(baseline_commit):
        raise ValueError("agent-attestation baseline_commit must be a 40 character commit SHA")
    if expected_baseline_commit and baseline_commit != expected_baseline_commit:
        raise ValueError("agent-attestation baseline_commit differs from the task manifest")
    candidate_commit = str(payload.get("candidate_commit") or "").strip()
    if expected_candidate_commit is not None and candidate_commit != expected_candidate_commit:
        raise ValueError("agent-attestation candidate_commit differs from the frozen candidate")
    if candidate_commit and not HEX40.fullmatch(candidate_commit):
        raise ValueError("agent-attestation candidate_commit must be a 40 character commit SHA")
    input_sha = _require_text(payload, "input_sha256")
    output_sha = _require_text(payload, "output_sha256")
    _validate_sha(input_sha, "agent-attestation input_sha256")
    _validate_sha(output_sha, "agent-attestation output_sha256")
    if output_sha != expected_artifact_sha256:
        raise ValueError("agent-attestation output_sha256 does not match the imported artifact")
    decision = _require_text(payload, "decision")
    if decision not in ROLE_DECISIONS[expected_role]:
        raise ValueError(f"invalid {expected_role} decision: {decision}")
    issued_at = _require_text(payload, "issued_at")
    if "T" not in issued_at:
        raise ValueError("agent-attestation issued_at must be an ISO-8601 timestamp")
    digest = _require_text(payload, "attestation_digest")
    if digest != payload_digest(payload, exclude={"attestation_digest"}):
        raise ValueError("agent-attestation digest is invalid")
    _validate_signature(payload, expected_role)
    return payload


def load_manifest(case_dir: Path, *, required: bool = True) -> dict[str, Any]:
    path = manifest_path(case_dir)
    if not path.exists() and not required:
        return {}
    payload = _load_object(path)
    if payload.get("schema_version") != 1 or payload.get("record_type") != "agent-task-manifest":
        raise ValueError("invalid agent-task-manifest")
    digest = str(payload.get("manifest_digest") or "")
    if digest != payload_digest(payload, exclude={"manifest_digest"}):
        raise ValueError("agent-task-manifest digest is invalid")
    if not isinstance(payload.get("stages"), dict):
        raise ValueError("agent-task-manifest requires stages")
    return payload


def _validate_distinct_stage_identities(stages: dict[str, Any]) -> None:
    seen_tasks: dict[str, str] = {}
    seen_threads: dict[str, str] = {}
    seen_worktrees: dict[str, str] = {}
    for role, raw in stages.items():
        if role not in ALL_AGENT_ROLES or not isinstance(raw, dict):
            raise ValueError(f"invalid task-manifest stage: {role}")
        task_id = str(raw.get("task_id") or "")
        thread_id = str(raw.get("thread_id") or "")
        worktree_id = str(raw.get("worktree_id") or "")
        if not task_id or not thread_id or not worktree_id:
            raise ValueError(f"task-manifest stage {role} lacks task/thread/worktree identity")
        if task_id in seen_tasks and seen_tasks[task_id] != role:
            raise ValueError(f"agent task reused across roles: {task_id}")
        if thread_id in seen_threads and seen_threads[thread_id] != role:
            raise ValueError(f"agent thread reused across roles: {thread_id}")
        if worktree_id in seen_worktrees and seen_worktrees[worktree_id] != role:
            raise ValueError(f"agent worktree reused across roles: {worktree_id}")
        seen_tasks[task_id] = role
        seen_threads[thread_id] = role
        seen_worktrees[worktree_id] = role


def preflight_stage_update(
    case_dir: Path,
    *,
    role: str,
    task_id: str,
    thread_id: str,
    worktree_id: str,
    replace: bool,
) -> dict[str, Any]:
    current = load_manifest(case_dir, required=False)
    stages = dict(current.get("stages") or {}) if current else {}
    if role in stages and not replace:
        raise ValueError(f"task-manifest stage already exists for {role}; explicit replacement is required")
    prospective = dict(stages)
    prospective[role] = {
        "task_id": task_id,
        "thread_id": thread_id,
        "worktree_id": worktree_id,
    }
    _validate_distinct_stage_identities(prospective)
    return current


def write_manifest(
    case_dir: Path,
    *,
    change_id: str,
    repository: str,
    baseline_commit: str,
    role: str,
    attestation: dict[str, Any],
    attestation_rel: str,
    artifact_rel: str,
    artifact_sha256: str,
    replace: bool = False,
) -> Path:
    current = load_manifest(case_dir, required=False)
    if current:
        if current.get("change_id") != change_id:
            raise ValueError("agent-task-manifest change_id mismatch")
        if current.get("repository") != repository:
            raise ValueError("agent-task-manifest repository mismatch")
        if current.get("baseline_commit") != baseline_commit:
            raise ValueError("agent-task-manifest baseline_commit mismatch")
        stages = dict(current.get("stages") or {})
    else:
        stages = {}
    existing = stages.get(role)
    if existing and not replace:
        if existing.get("attestation_sha256") == file_sha256(case_dir.parent.parent.parent / attestation_rel):
            return manifest_path(case_dir)
        raise ValueError(f"task-manifest stage already exists for {role}; explicit replacement is required")
    stages[role] = {
        "status": "IMPORTED" if role != IMPLEMENTER_ROLE else str(attestation.get("decision") or "STARTED"),
        "task_id": attestation["task_id"],
        "thread_id": attestation["thread_id"],
        "worktree_id": attestation["worktree_id"],
        "provider": attestation["provider"],
        "decision": attestation["decision"],
        "baseline_commit": attestation["baseline_commit"],
        "candidate_commit": attestation.get("candidate_commit") or "",
        "artifact": artifact_rel,
        "artifact_sha256": artifact_sha256,
        "attestation": attestation_rel,
        "attestation_sha256": file_sha256(case_dir.parent.parent.parent / attestation_rel),
    }
    _validate_distinct_stage_identities(stages)
    payload = {
        "schema_version": 1,
        "record_type": "agent-task-manifest",
        "change_id": change_id,
        "repository": repository,
        "baseline_commit": baseline_commit,
        "stages": dict(sorted(stages.items())),
    }
    payload["manifest_digest"] = payload_digest(payload)
    path = manifest_path(case_dir)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def import_attestation(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    role: str,
    artifact_source: Path,
    attestation_source: Path,
    replace: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    case_dir = case_dir_from_contract(workspace, contract_payload)
    case_dir.mkdir(parents=True, exist_ok=True)
    if role not in REVIEWER_ROLES:
        raise ValueError("only read-only reviewer attestations may be imported")
    artifact_target = case_dir / ROLE_ARTIFACTS[role]
    artifact_bytes = artifact_source.read_bytes()
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
    raw_attestation = _load_object(attestation_source)
    existing_manifest = load_manifest(case_dir, required=False)
    baseline_commit = str(existing_manifest.get("baseline_commit") or raw_attestation.get("baseline_commit") or "")
    repository = repository_slug(workspace)
    validate_attestation(
        raw_attestation,
        expected_role=role,
        expected_change_id=str(contract_payload.get("change_id") or ""),
        expected_repository=repository,
        expected_artifact_sha256=artifact_sha,
        expected_baseline_commit=baseline_commit or None,
        expected_candidate_commit=(
            str(raw_attestation.get("candidate_commit") or "")
            if role in {"diff-integrity-reviewer", "closure-arbiter"}
            else None
        ),
    )
    if existing_manifest and existing_manifest.get("baseline_commit") != raw_attestation.get("baseline_commit"):
        raise ValueError("reviewer attestation is bound to a stale baseline")
    preflight_stage_update(
        case_dir,
        role=role,
        task_id=str(raw_attestation["task_id"]),
        thread_id=str(raw_attestation["thread_id"]),
        worktree_id=str(raw_attestation["worktree_id"]),
        replace=replace,
    )
    artifact_target.write_bytes(artifact_bytes)
    target_attestation = attestation_path(case_dir, role)
    target_attestation.parent.mkdir(parents=True, exist_ok=True)
    target_attestation.write_text(
        json.dumps(raw_attestation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    root = workspace
    artifact_rel = artifact_target.relative_to(root).as_posix()
    attestation_rel = target_attestation.relative_to(root).as_posix()
    write_manifest(
        case_dir,
        change_id=str(contract_payload.get("change_id") or ""),
        repository=repository,
        baseline_commit=str(raw_attestation["baseline_commit"]),
        role=role,
        attestation=raw_attestation,
        attestation_rel=attestation_rel,
        artifact_rel=artifact_rel,
        artifact_sha256=artifact_sha,
        replace=replace,
    )
    return {
        "status": "PASS",
        "role": role,
        "artifact": artifact_rel,
        "attestation": attestation_rel,
        "task_id": raw_attestation["task_id"],
    }


def register_implementer(
    workspace: Path,
    contract_payload: dict[str, Any],
    *,
    provider: str,
    task_id: str,
    thread_id: str,
    worktree_id: str,
    baseline_commit: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    case_dir = case_dir_from_contract(workspace, contract_payload)
    case_dir.mkdir(parents=True, exist_ok=True)
    repository = repository_slug(workspace)
    baseline = baseline_commit or git_value(workspace, "rev-parse", "HEAD")
    plan_path = case_dir / "repair-plan.json"
    if not plan_path.is_file():
        raise ValueError("repair-plan.json is required before registering the implementer")
    preflight_stage_update(
        case_dir,
        role=IMPLEMENTER_ROLE,
        task_id=task_id,
        thread_id=thread_id,
        worktree_id=worktree_id,
        replace=replace,
    )
    artifact = {
        "schema_version": 1,
        "record_type": "implementer-registration",
        "change_id": str(contract_payload.get("change_id") or ""),
        "role": IMPLEMENTER_ROLE,
        "repository": repository,
        "baseline_commit": baseline,
        "task_id": task_id,
        "thread_id": thread_id,
        "worktree_id": worktree_id,
        "status": "STARTED",
    }
    artifact_path = case_dir / ROLE_ARTIFACTS[IMPLEMENTER_ROLE]
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact_sha = file_sha256(artifact_path)
    attestation = {
        "schema_version": 1,
        "record_type": "agent-attestation",
        "change_id": str(contract_payload.get("change_id") or ""),
        "role": IMPLEMENTER_ROLE,
        "provider": provider,
        "repository": repository,
        "task_id": task_id,
        "thread_id": thread_id,
        "worktree_id": worktree_id,
        "baseline_commit": baseline,
        "candidate_commit": "",
        "input_sha256": file_sha256(plan_path),
        "output_sha256": artifact_sha,
        "decision": "STARTED",
        "issued_at": os.environ.get("AGENT_ATTESTATION_TIME") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    # The reviewer signing key must remain unavailable to the writable implementer.
    # Implementer identity is bound to the host task/thread/worktree registration.
    attestation["attestation_digest"] = payload_digest(attestation)
    target_attestation = attestation_path(case_dir, IMPLEMENTER_ROLE)
    target_attestation.parent.mkdir(parents=True, exist_ok=True)
    target_attestation.write_text(json.dumps(attestation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_manifest(
        case_dir,
        change_id=str(contract_payload.get("change_id") or ""),
        repository=repository,
        baseline_commit=baseline,
        role=IMPLEMENTER_ROLE,
        attestation=attestation,
        attestation_rel=target_attestation.relative_to(workspace).as_posix(),
        artifact_rel=artifact_path.relative_to(workspace).as_posix(),
        artifact_sha256=artifact_sha,
        replace=replace,
    )
    return {
        "status": "PASS",
        "role": IMPLEMENTER_ROLE,
        "task_id": task_id,
        "baseline_commit": baseline,
    }


def validate_stage(
    workspace: Path,
    contract_payload: dict[str, Any],
    role: str,
    *,
    expected_candidate_commit: str | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    case_dir = case_dir_from_contract(workspace, contract_payload)
    manifest = load_manifest(case_dir)
    if manifest.get("change_id") != contract_payload.get("change_id"):
        raise ValueError("task manifest change_id differs from active contract")
    if manifest.get("repository") != repository_slug(workspace):
        raise ValueError("task manifest repository differs from current repository")
    stages = manifest.get("stages") or {}
    _validate_distinct_stage_identities(stages)
    stage = stages.get(role)
    if not isinstance(stage, dict):
        raise ValueError(f"required agent stage is missing: {role}")
    artifact = _safe_relative(workspace, stage.get("artifact"), label=f"{role} artifact")
    attestation_file = _safe_relative(workspace, stage.get("attestation"), label=f"{role} attestation")
    artifact_sha = file_sha256(artifact)
    if stage.get("artifact_sha256") != artifact_sha:
        raise ValueError(f"{role} artifact changed after import")
    if stage.get("attestation_sha256") != file_sha256(attestation_file):
        raise ValueError(f"{role} attestation changed after import")
    attestation = _load_object(attestation_file)
    validate_attestation(
        attestation,
        expected_role=role,
        expected_change_id=str(contract_payload.get("change_id") or ""),
        expected_repository=str(manifest.get("repository") or ""),
        expected_artifact_sha256=artifact_sha,
        expected_baseline_commit=str(manifest.get("baseline_commit") or ""),
        expected_candidate_commit=expected_candidate_commit,
    )
    if stage.get("task_id") != attestation.get("task_id") or stage.get("worktree_id") != attestation.get("worktree_id"):
        raise ValueError(f"{role} task identity differs from its attestation")
    return {
        "manifest": manifest,
        "stage": stage,
        "attestation": attestation,
        "artifact": artifact,
    }


def validate_role_separation(manifest: dict[str, Any], required_roles: Iterable[str]) -> None:
    stages = manifest.get("stages") or {}
    missing = [role for role in required_roles if role not in stages]
    if missing:
        raise ValueError("required multi-agent stages are missing: " + ",".join(sorted(missing)))
    selected = {role: stages[role] for role in required_roles}
    _validate_distinct_stage_identities(selected)
    implementer = selected.get(IMPLEMENTER_ROLE)
    if isinstance(implementer, dict):
        for role in REVIEWER_ROLES.intersection(selected):
            reviewer = selected[role]
            if reviewer.get("task_id") == implementer.get("task_id"):
                raise ValueError(f"{role} cannot reuse the product implementer task")
            if reviewer.get("worktree_id") == implementer.get("worktree_id"):
                raise ValueError(f"{role} cannot reuse the product implementer worktree")
