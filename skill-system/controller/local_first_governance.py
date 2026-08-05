from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from task_run import TaskRunError, TaskRunStore, fingerprint

SCHEMA_VERSION = 1
LOCAL_GATE_ORDER = (
    "targeted",
    "module",
    "static",
    "quick",
    "review",
    "scope",
)
LOCAL_COMPLETION_CONDITIONS = tuple(f"local_{gate}_green" for gate in LOCAL_GATE_ORDER)
CI_COMPLETION_CONDITION = "ci_certification_green"
DEFAULT_BUDGETS = {
    "local_repair_rounds": 4,
    "local_verification_rounds": 2,
    "ci_feedback_rounds": 2,
    "no_progress_limit": 2,
    "flaky_retries": 2,
}
REMOTE_REPAIR_APPROVAL_VALUES = {"approved", "explicitly-approved"}
PROTECTED_PREFIXES = (
    ".github/workflows/",
    "deployment/",
    "governance/",
    "skill-system/",
    "scripts/",
)


class LocalFirstGovernanceError(TaskRunError):
    """Raised when local-first governance rejects an operation."""


class UploadAdmissionError(LocalFirstGovernanceError):
    """Raised when local evidence is insufficient for a GitHub upload."""


class CIFeedbackBindingError(LocalFirstGovernanceError):
    """Raised when CI feedback does not belong to the admitted candidate."""


@dataclass(frozen=True)
class CIFailureDecision:
    kind: str
    owner: str
    product_code_write_allowed: bool
    automatic_retry_allowed: bool
    remote_repair_allowed: bool
    reason: str


@dataclass(frozen=True)
class UploadAdmissionDecision:
    allowed: bool
    missing_conditions: tuple[str, ...]
    scope_violations: tuple[str, ...]
    reason: str


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _canonical_path(value: str) -> str:
    path = str(value).replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise LocalFirstGovernanceError(f"invalid repository path: {value!r}")
    return path


def _normalize_paths(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({_canonical_path(value) for value in values}))


def _path_matches(path: str, rule: str) -> bool:
    if rule.endswith("/**"):
        prefix = rule[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return path == rule


def scope_violations(changed_paths: Iterable[str], allowed_paths: Iterable[str]) -> tuple[str, ...]:
    changed = _normalize_paths(changed_paths)
    allowed = _normalize_paths(allowed_paths)
    return tuple(path for path in changed if not any(_path_matches(path, rule) for rule in allowed))


def _validate_budgets(raw: dict[str, Any] | None) -> dict[str, int]:
    budgets = dict(DEFAULT_BUDGETS)
    for key, value in (raw or {}).items():
        if key not in budgets:
            raise LocalFirstGovernanceError(f"unknown local-first budget: {key}")
        integer = int(value)
        if integer < 0:
            raise LocalFirstGovernanceError(f"budget must be non-negative: {key}")
        budgets[key] = integer
    if budgets["local_repair_rounds"] < 1:
        raise LocalFirstGovernanceError("local_repair_rounds must be positive")
    if budgets["local_verification_rounds"] < 1:
        raise LocalFirstGovernanceError("local_verification_rounds must be positive")
    return budgets


def create_local_first_task(
    path: Path,
    *,
    task_id: str,
    change_id: str,
    base_sha: str,
    branch: str,
    patch_owner: str,
    allowed_paths: Iterable[str],
    target_fingerprint: str,
    budgets: dict[str, Any] | None = None,
) -> TaskRunStore:
    normalized_allowed = _normalize_paths(allowed_paths)
    if not normalized_allowed:
        raise LocalFirstGovernanceError("local-first task requires explicit allowed paths")
    if any(rule in {"**", "services/**", "scripts/**", "skill-system/**"} for rule in normalized_allowed):
        raise LocalFirstGovernanceError("over-broad local-first write scope is forbidden")
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", base_sha):
        raise LocalFirstGovernanceError("base_sha must be a hexadecimal commit-like identifier")
    if not str(branch).strip() or branch in {"main", "master"}:
        raise LocalFirstGovernanceError("local-first task must use a non-default working branch")
    if not str(patch_owner).strip():
        raise LocalFirstGovernanceError("patch_owner is required")
    binding = {
        "schema_version": SCHEMA_VERSION,
        "change_id": str(change_id),
        "base_sha": base_sha.lower(),
        "branch": str(branch),
        "patch_owner": str(patch_owner),
        "allowed_paths": list(normalized_allowed),
        "target_fingerprint": str(target_fingerprint),
    }
    store = TaskRunStore.open_or_create(
        path,
        task_id=task_id,
        task_kind="local-first-repair",
        binding=binding,
        required_conditions=(*LOCAL_COMPLETION_CONDITIONS, CI_COMPLETION_CONDITION),
    )
    if "local_first" not in store.payload.get("metadata", {}):
        store.set_metadata(
            local_first={
                "schema_version": SCHEMA_VERSION,
                "budgets": _validate_budgets(budgets),
                "counters": {
                    "local_repair_rounds": 0,
                    "local_verification_rounds": 0,
                    "ci_feedback_rounds": 0,
                    "no_progress_events": 0,
                    "flaky_retries": 0,
                },
                "gate_results": {},
                "active_local_round": 0,
                "upload_admission": None,
                "ci_binding": None,
                "ci_feedback": [],
                "remote_repair": {"status": "not-approved", "evidence_refs": []},
                "patch_owner": str(patch_owner),
                "created_at": _now(),
            }
        )
        store.checkpoint(
            status="PLANNED",
            phase="LOCAL_PLAN_APPROVED",
            workspace_fingerprint=None,
            evidence_refs=[],
            metadata={"allowed_paths": list(normalized_allowed), "patch_owner": patch_owner},
        )
    return store


def local_metadata(store: TaskRunStore) -> dict[str, Any]:
    metadata = store.payload.get("metadata") if isinstance(store.payload.get("metadata"), dict) else {}
    local = metadata.get("local_first") if isinstance(metadata.get("local_first"), dict) else None
    if local is None:
        raise LocalFirstGovernanceError("task-run is not local-first governed")
    return local


def _persist_local_metadata(store: TaskRunStore, local: dict[str, Any]) -> None:
    store.set_metadata(local_first=local)


def _increment_counter(store: TaskRunStore, name: str) -> int:
    local = local_metadata(store)
    counters = local.setdefault("counters", {})
    budgets = local.setdefault("budgets", dict(DEFAULT_BUDGETS))
    current = int(counters.get(name) or 0) + 1
    counters[name] = current
    _persist_local_metadata(store, local)
    limit = int(budgets.get(name) or 0)
    if limit and current > limit:
        raise LocalFirstGovernanceError(f"local-first budget exhausted: {name}={current}>{limit}")
    return current


def begin_local_repair_round(
    store: TaskRunStore,
    *,
    workspace_fingerprint: str,
    evidence_refs: Iterable[str] = (),
) -> int:
    if store.payload.get("status") in {"COMPLETED", "CANCELLED", "WAITING_EXTERNAL_RESULT"}:
        raise LocalFirstGovernanceError(
            f"cannot begin a local repair round from status {store.payload.get('status')}"
        )
    round_number = _increment_counter(store, "local_repair_rounds")
    local = local_metadata(store)
    local["active_local_round"] = round_number
    local["gate_results"] = {}
    local["upload_admission"] = None
    conditions = store.payload.get("conditions") if isinstance(store.payload.get("conditions"), dict) else {}
    for condition in LOCAL_COMPLETION_CONDITIONS:
        conditions[condition] = {"satisfied": False, "evidence_refs": [], "updated_at": None}
    store.payload["conditions"] = conditions
    _persist_local_metadata(store, local)
    store.checkpoint(
        status="RUNNING",
        phase="LOCAL_REPAIRING",
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=evidence_refs,
        metadata={"round": round_number, "patch_owner": store.payload["binding"]["patch_owner"]},
    )
    return round_number


def record_no_progress(
    store: TaskRunStore,
    *,
    workspace_fingerprint: str,
    evidence_refs: Iterable[str],
) -> int:
    count = _increment_counter(store, "no_progress_events")
    local = local_metadata(store)
    limit = int(local["budgets"]["no_progress_limit"])
    status = "BLOCKED" if count >= limit else "FAILED_RECOVERABLE"
    store.checkpoint(
        status=status,
        phase="LOCAL_NO_PROGRESS",
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=evidence_refs,
        metadata={"no_progress_events": count, "limit": limit},
    )
    return count


def record_local_gate(
    store: TaskRunStore,
    *,
    gate: str,
    passed: bool,
    evidence_refs: Iterable[str],
    workspace_fingerprint: str,
    details: dict[str, Any] | None = None,
) -> None:
    gate = str(gate)
    if gate not in LOCAL_GATE_ORDER:
        raise LocalFirstGovernanceError(f"unknown local-first gate: {gate}")
    local = local_metadata(store)
    active_round = int(local.get("active_local_round") or 0)
    if active_round < 1:
        raise LocalFirstGovernanceError("begin_local_repair_round must run before local gates")
    gate_index = LOCAL_GATE_ORDER.index(gate)
    prior_gates = LOCAL_GATE_ORDER[:gate_index]
    missing_prerequisites = [
        prior
        for prior in prior_gates
        if str((local.get("gate_results") or {}).get(prior, {}).get("status")) != "PASS"
    ]
    if missing_prerequisites:
        raise LocalFirstGovernanceError(
            f"gate {gate} cannot run before prior gates pass: {missing_prerequisites}"
        )
    refs = [str(item) for item in evidence_refs if str(item).strip()]
    if not refs:
        raise LocalFirstGovernanceError(f"gate {gate} requires evidence")
    if store.payload.get("status") == "PLANNED":
        store.checkpoint(
            status="RUNNING",
            phase="LOCAL_VALIDATION_STARTED",
            workspace_fingerprint=workspace_fingerprint,
            evidence_refs=evidence_refs,
            metadata={"gate": gate},
        )
    local = local_metadata(store)
    local.setdefault("gate_results", {})[gate] = {
        "status": "PASS" if passed else "FAIL",
        "round": active_round,
        "evidence_refs": refs,
        "workspace_fingerprint": workspace_fingerprint,
        "details": dict(details or {}),
        "recorded_at": _now(),
    }
    _persist_local_metadata(store, local)
    if passed:
        store.mark_condition(f"local_{gate}_green", evidence_refs=refs)
        next_phase = f"LOCAL_{gate.upper()}_GREEN"
        store.checkpoint(
            status="VALIDATING",
            phase=next_phase,
            workspace_fingerprint=workspace_fingerprint,
            evidence_refs=refs,
            metadata={"gate": gate, "result": "PASS"},
        )
        return
    _increment_counter(store, "local_verification_rounds")
    store.checkpoint(
        status="FAILED_RECOVERABLE",
        phase=f"LOCAL_{gate.upper()}_FAILED",
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=refs,
        metadata={"gate": gate, "result": "FAIL"},
    )


def upload_admission(
    store: TaskRunStore,
    *,
    changed_paths: Iterable[str],
    candidate_head_sha: str,
    workspace_fingerprint: str,
    evidence_refs: Iterable[str],
) -> UploadAdmissionDecision:
    local = local_metadata(store)
    active_round = int(local.get("active_local_round") or 0)
    gate_results = local.get("gate_results") if isinstance(local.get("gate_results"), dict) else {}
    missing = tuple(
        condition
        for condition in LOCAL_COMPLETION_CONDITIONS
        if not bool((store.payload.get("conditions") or {}).get(condition, {}).get("satisfied"))
        or str(gate_results.get(condition.removeprefix("local_").removesuffix("_green"), {}).get("status")) != "PASS"
        or int(gate_results.get(condition.removeprefix("local_").removesuffix("_green"), {}).get("round") or 0) != active_round
    )
    normalized_changed = _normalize_paths(changed_paths)
    if not normalized_changed:
        return UploadAdmissionDecision(False, missing, (), "no source changes were detected")
    violations = scope_violations(normalized_changed, store.payload["binding"]["allowed_paths"])
    if missing or violations:
        return UploadAdmissionDecision(
            allowed=False,
            missing_conditions=missing,
            scope_violations=violations,
            reason="local evidence incomplete" if missing else "changed paths exceed the approved scope",
        )
    refs = [str(item) for item in evidence_refs if str(item).strip()]
    if not refs:
        raise UploadAdmissionError("upload admission requires durable evidence")
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", candidate_head_sha):
        raise UploadAdmissionError("candidate_head_sha must be a hexadecimal commit-like identifier")
    changed = list(normalized_changed)
    admission = {
        "status": "ADMITTED",
        "candidate_head_sha": candidate_head_sha.lower(),
        "workspace_fingerprint": workspace_fingerprint,
        "changed_paths": changed,
        "evidence_refs": refs,
        "admitted_at": _now(),
        "admission_fingerprint": fingerprint(
            {
                "task_id": store.payload["task_id"],
                "base_sha": store.payload["binding"]["base_sha"],
                "candidate_head_sha": candidate_head_sha.lower(),
                "workspace_fingerprint": workspace_fingerprint,
                "changed_paths": changed,
            }
        ),
    }
    local["upload_admission"] = admission
    _persist_local_metadata(store, local)
    store.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="READY_FOR_CI",
        workspace_fingerprint=workspace_fingerprint,
        evidence_refs=refs,
        metadata={"candidate_head_sha": candidate_head_sha.lower(), "changed_paths": changed},
    )
    return UploadAdmissionDecision(True, (), (), "local-first upload admission passed")


def bind_ci_run(
    store: TaskRunStore,
    *,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    evidence_refs: Iterable[str],
) -> None:
    local = local_metadata(store)
    admission = local.get("upload_admission") if isinstance(local.get("upload_admission"), dict) else None
    if not admission or admission.get("status") != "ADMITTED":
        raise CIFeedbackBindingError("CI cannot start before local upload admission")
    if str(admission.get("candidate_head_sha")) != str(head_sha).lower():
        raise CIFeedbackBindingError("CI head SHA does not match the locally admitted candidate")
    if int(run_id) < 1 or int(run_attempt) < 1:
        raise CIFeedbackBindingError("CI run identity must be positive")
    refs = [str(item) for item in evidence_refs if str(item).strip()]
    if not refs:
        raise CIFeedbackBindingError("CI binding requires evidence")
    binding = {
        "run_id": int(run_id),
        "run_attempt": int(run_attempt),
        "head_sha": str(head_sha).lower(),
        "bound_at": _now(),
        "binding_fingerprint": fingerprint(
            {
                "task_id": store.payload["task_id"],
                "run_id": int(run_id),
                "run_attempt": int(run_attempt),
                "head_sha": str(head_sha).lower(),
                "admission_fingerprint": admission["admission_fingerprint"],
            }
        ),
    }
    local["ci_binding"] = binding
    _persist_local_metadata(store, local)
    store.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="CI_RUNNING",
        workspace_fingerprint=str(admission["workspace_fingerprint"]),
        evidence_refs=refs,
        metadata=binding,
    )


def classify_ci_failure(*, job_name: str, log_text: str, conclusion: str = "failure") -> CIFailureDecision:
    text = f"{job_name}\n{log_text}".lower()
    if conclusion in {"cancelled", "canceled", "startup_failure", "stale"}:
        return CIFailureDecision("interruption", "ci-reliability-agent", False, True, False, conclusion)
    if any(token in text for token in ("bad credentials", "secret", "api key", "authentication failed", "401 unauthorized")):
        return CIFailureDecision("secret_or_auth", "platform-operator", False, False, False, "secret/auth failures cannot authorize product edits")
    if any(token in text for token in ("permission denied", "resource not accessible", "403 forbidden", "environment approval")):
        return CIFailureDecision("permission", "platform-operator", False, False, False, "permission failures are platform-owned")
    if any(token in text for token in ("no space left", "runner lost communication", "could not resolve host", "network is unreachable", "service unavailable")):
        return CIFailureDecision("environment", "ci-reliability-agent", False, True, False, "runner/network failures are not product-code failures")
    if any(token in text for token in ("timed out", "timeout", "exit code 124")):
        return CIFailureDecision("timeout", "ci-reliability-agent", False, True, False, "timeouts require reliability triage before product edits")
    if any(token in text for token in ("flaky", "rerun passed", "intermittent", "random seed")):
        return CIFailureDecision("flaky", "ci-reliability-agent", False, True, False, "flaky failures may be rerun within a separate retry budget")
    if any(token in text for token in ("test definition", "invalid fixture", "collection error", "duplicate test")):
        return CIFailureDecision("test_defect", "test-maintainer-agent", False, False, False, "test-authority changes require independent review")
    if any(token in text for token in ("assertionerror", "expected", "test failed", "pytest", "syntaxerror", "type error", "mypy", "contract")):
        return CIFailureDecision("code_or_contract", "patch-owner", True, False, True, "return the failure packet to the original patch owner")
    return CIFailureDecision("unknown", "human-triage", False, False, False, "unknown failures block automatic source changes")


def approve_remote_repair(
    store: TaskRunStore,
    *,
    approval: str,
    evidence_refs: Iterable[str],
) -> None:
    if approval not in REMOTE_REPAIR_APPROVAL_VALUES:
        raise LocalFirstGovernanceError("remote repair requires explicit approval")
    refs = [str(item) for item in evidence_refs if str(item).strip()]
    if not refs:
        raise LocalFirstGovernanceError("remote repair approval requires evidence")
    local = local_metadata(store)
    local["remote_repair"] = {
        "status": approval,
        "evidence_refs": refs,
        "approved_at": _now(),
    }
    _persist_local_metadata(store, local)


def _remote_repair_is_approved(local: dict[str, Any]) -> bool:
    remote = local.get("remote_repair") if isinstance(local.get("remote_repair"), dict) else {}
    return str(remote.get("status")) in REMOTE_REPAIR_APPROVAL_VALUES


def record_ci_result(
    store: TaskRunStore,
    *,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    conclusion: str,
    job_name: str,
    log_text: str,
    evidence_refs: Iterable[str],
) -> CIFailureDecision | None:
    local = local_metadata(store)
    binding = local.get("ci_binding") if isinstance(local.get("ci_binding"), dict) else None
    if not binding:
        raise CIFeedbackBindingError("CI result is not bound to an active run")
    expected = (int(binding["run_id"]), int(binding["run_attempt"]), str(binding["head_sha"]))
    actual = (int(run_id), int(run_attempt), str(head_sha).lower())
    if actual != expected:
        raise CIFeedbackBindingError(f"CI result binding changed: expected={expected} actual={actual}")
    refs = [str(item) for item in evidence_refs if str(item).strip()]
    if not refs:
        raise CIFeedbackBindingError("CI result requires evidence")
    if conclusion == "success":
        store.mark_condition(CI_COMPLETION_CONDITION, evidence_refs=refs)
        local["final_ci"] = {
            "run_id": int(run_id),
            "run_attempt": int(run_attempt),
            "head_sha": str(head_sha).lower(),
            "evidence_refs": refs,
            "production_closed": False,
            "completed_at": _now(),
        }
        _persist_local_metadata(store, local)
        store.checkpoint(
            status="VALIDATING",
            phase="CI_CERTIFICATION_GREEN",
            workspace_fingerprint=str(local["upload_admission"]["workspace_fingerprint"]),
            evidence_refs=refs,
            metadata={"run_id": int(run_id), "run_attempt": int(run_attempt)},
        )
        store.complete(
            workspace_fingerprint=str(local["upload_admission"]["workspace_fingerprint"]),
            evidence_refs=refs,
        )
        return None

    decision = classify_ci_failure(job_name=job_name, log_text=log_text, conclusion=conclusion)
    feedback_round = _increment_counter(store, "ci_feedback_rounds")
    feedback = {
        "run_id": int(run_id),
        "run_attempt": int(run_attempt),
        "head_sha": str(head_sha).lower(),
        "conclusion": conclusion,
        "job_name": job_name,
        "kind": decision.kind,
        "owner": decision.owner,
        "product_code_write_allowed": decision.product_code_write_allowed,
        "automatic_retry_allowed": decision.automatic_retry_allowed,
        "remote_repair_allowed": decision.remote_repair_allowed and _remote_repair_is_approved(local),
        "reason": decision.reason,
        "evidence_refs": refs,
        "feedback_round": feedback_round,
        "recorded_at": _now(),
    }
    local = local_metadata(store)
    local.setdefault("ci_feedback", []).append(feedback)
    _persist_local_metadata(store, local)

    if decision.kind == "code_or_contract":
        store.checkpoint(
            status="FAILED_RECOVERABLE",
            phase="CI_FAILURE_RETURNED_TO_PATCH_OWNER",
            workspace_fingerprint=str(local["upload_admission"]["workspace_fingerprint"]),
            evidence_refs=refs,
            metadata=feedback,
        )
    elif decision.automatic_retry_allowed:
        if decision.kind == "flaky":
            _increment_counter(store, "flaky_retries")
        store.checkpoint(
            status="WAITING_EXTERNAL_RESULT",
            phase="CI_RELIABILITY_RETRY_PENDING",
            workspace_fingerprint=str(local["upload_admission"]["workspace_fingerprint"]),
            evidence_refs=refs,
            metadata=feedback,
        )
    else:
        store.checkpoint(
            status="BLOCKED",
            phase="CI_PLATFORM_OR_AUTHORITY_BLOCKED",
            workspace_fingerprint=str(local["upload_admission"]["workspace_fingerprint"]),
            evidence_refs=refs,
            metadata=feedback,
        )
    return decision


def export_status(store: TaskRunStore) -> dict[str, Any]:
    local = local_metadata(store)
    conditions = store.payload.get("conditions") if isinstance(store.payload.get("conditions"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": store.payload["task_id"],
        "status": store.payload["status"],
        "phase": store.payload["phase"],
        "patch_owner": store.payload["binding"]["patch_owner"],
        "branch": store.payload["binding"]["branch"],
        "base_sha": store.payload["binding"]["base_sha"],
        "allowed_paths": store.payload["binding"]["allowed_paths"],
        "conditions": {
            name: bool(row.get("satisfied"))
            for name, row in conditions.items()
            if isinstance(row, dict)
        },
        "budgets": local["budgets"],
        "counters": local["counters"],
        "upload_admission": local.get("upload_admission"),
        "ci_binding": local.get("ci_binding"),
        "ci_feedback": local.get("ci_feedback") or [],
        "remote_repair": local.get("remote_repair"),
        "production_closed": False,
    }
