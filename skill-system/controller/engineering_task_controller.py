from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from autonomy_grant import authorize_autonomous_action
from failure_recovery_policy import AUTO_DIAGNOSE, decide_recovery
from local_first_governance import (
    CIFeedbackBindingError,
    LocalFirstGovernanceError,
    local_metadata,
    record_ci_result,
)
from task_run import TaskRunStore


ENGINEERING_RECONCILER_SCHEMA = "engineering-task-reconciler@1"
ENGINEERING_DECISION_SCHEMA = "engineering-reconcile-decision@1"
_TERMINAL_CONCLUSIONS = {
    "success",
    "failure",
    "cancelled",
    "canceled",
    "timed_out",
    "action_required",
    "startup_failure",
    "stale",
}
_VERDICTS = {"PASS", "FAIL", "UNKNOWN"}
_STAGE1_RETRYABLE_FAILURES = {
    "environment": "ENVIRONMENT_FAILURE",
    "timeout": "ENVIRONMENT_FAILURE",
    "cancelled": "TRANSIENT_INFRA_FAILURE",
    "canceled": "TRANSIENT_INFRA_FAILURE",
    "startup_failure": "TRANSIENT_INFRA_FAILURE",
    "stale": "TRANSIENT_INFRA_FAILURE",
}


class EngineeringReconcileError(RuntimeError):
    """Raised when terminal CI evidence cannot be bound safely to the governed task."""


@dataclass(frozen=True)
class CIObservation:
    run_id: int
    run_attempt: int
    head_sha: str
    conclusion: str
    job_name: str
    log_text: str
    evidence_refs: tuple[str, ...]


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha(value: object, *, name: str) -> str:
    text = _text(value).lower()
    if not re.fullmatch(r"[0-9a-f]{7,64}", text):
        raise EngineeringReconcileError(f"{name} must be a hexadecimal commit-like identifier")
    return text


def _normalize_verdict(value: object, *, name: str) -> str:
    verdict = _text(value).upper() or "UNKNOWN"
    if verdict not in _VERDICTS:
        raise EngineeringReconcileError(f"unsupported {name}: {verdict}")
    return verdict


def _validate_observation(observation: CIObservation) -> tuple[int, int, str, str, tuple[str, ...]]:
    try:
        run_id = int(observation.run_id)
        run_attempt = int(observation.run_attempt)
    except (TypeError, ValueError) as exc:
        raise EngineeringReconcileError("CI run identity must be numeric") from exc
    if run_id < 1 or run_attempt < 1:
        raise EngineeringReconcileError("CI run identity must be positive")
    head_sha = _sha(observation.head_sha, name="CI head_sha")
    conclusion = _text(observation.conclusion).lower()
    if conclusion not in _TERMINAL_CONCLUSIONS:
        raise EngineeringReconcileError(f"CI observation is not terminal: {conclusion!r}")
    refs = tuple(_text(ref) for ref in observation.evidence_refs if _text(ref))
    if not refs:
        raise EngineeringReconcileError("terminal CI observation requires durable evidence")
    if not _text(observation.job_name):
        raise EngineeringReconcileError("terminal CI observation requires job_name")
    return run_id, run_attempt, head_sha, conclusion, refs


def _binding_identity(store: TaskRunStore) -> tuple[int, int, str]:
    local = local_metadata(store)
    binding = local.get("ci_binding") if isinstance(local.get("ci_binding"), Mapping) else None
    if not binding:
        raise EngineeringReconcileError("TaskRun has no active CI binding")
    try:
        return (
            int(binding.get("run_id")),
            int(binding.get("run_attempt")),
            _sha(binding.get("head_sha"), name="bound CI head_sha"),
        )
    except (TypeError, ValueError) as exc:
        raise EngineeringReconcileError("TaskRun CI binding is malformed") from exc


def _reconciler_metadata(store: TaskRunStore) -> dict[str, Any]:
    metadata = store.payload.get("metadata") if isinstance(store.payload.get("metadata"), Mapping) else {}
    current = metadata.get("engineering_reconciler")
    if current is None:
        return {
            "schema": ENGINEERING_RECONCILER_SCHEMA,
            "decisions": {},
            "production_closed": False,
        }
    if not isinstance(current, Mapping) or current.get("schema") != ENGINEERING_RECONCILER_SCHEMA:
        raise EngineeringReconcileError("engineering reconciler metadata is malformed or has unknown schema")
    decisions = current.get("decisions")
    if not isinstance(decisions, Mapping):
        raise EngineeringReconcileError("engineering reconciler decisions are malformed")
    result = dict(current)
    result["decisions"] = dict(decisions)
    return result


def _delivery_key(*, run_id: int, run_attempt: int, head_sha: str) -> str:
    return f"{run_id}:{run_attempt}:{head_sha}"


def _observation_fingerprint(
    *,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    conclusion: str,
    product_verdict: str,
    transport_verdict: str,
) -> str:
    return _digest(
        {
            "run_id": run_id,
            "run_attempt": run_attempt,
            "head_sha": head_sha,
            "conclusion": conclusion,
            "product_verdict": product_verdict,
            "transport_verdict": transport_verdict,
        }
    )


def _persist_outcome(
    store: TaskRunStore,
    *,
    key: str,
    fingerprint: str,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    reconciler = _reconciler_metadata(store)
    decisions = dict(reconciler.get("decisions") or {})
    decisions[key] = {
        "observation_fingerprint": fingerprint,
        "outcome": dict(outcome),
    }
    reconciler["decisions"] = decisions
    reconciler["last_delivery_key"] = key
    reconciler["production_closed"] = False
    store.set_metadata(engineering_reconciler=reconciler)
    return dict(outcome)


def _existing_outcome(
    store: TaskRunStore,
    *,
    key: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    reconciler = _reconciler_metadata(store)
    entry = (reconciler.get("decisions") or {}).get(key)
    if entry is None:
        return None
    if not isinstance(entry, Mapping) or _text(entry.get("observation_fingerprint")) != fingerprint:
        raise EngineeringReconcileError(
            "conflicting terminal observation arrived for an already reconciled CI run attempt"
        )
    outcome = entry.get("outcome")
    if not isinstance(outcome, Mapping):
        raise EngineeringReconcileError("persisted engineering reconciliation outcome is malformed")
    duplicate = dict(outcome)
    duplicate["duplicate"] = True
    return duplicate


def _base_outcome(
    store: TaskRunStore,
    *,
    key: str,
    decision: str,
    action: str | None,
    allowed: bool,
    human_required: bool,
    failure_class: str | None,
    reason: str,
    product_write_allowed: bool = False,
) -> dict[str, Any]:
    task_id = _text(store.payload.get("task_id"))
    decision_id = _digest(
        {
            "task_id": task_id,
            "delivery_key": key,
            "decision": decision,
            "action": action,
            "failure_class": failure_class,
        }
    )
    return {
        "schema": ENGINEERING_DECISION_SCHEMA,
        "decision_id": decision_id,
        "task_id": task_id,
        "delivery_key": key,
        "decision": decision,
        "action": action,
        "allowed": bool(allowed),
        "human_required": bool(human_required),
        "failure_class": failure_class,
        "reason": reason,
        "product_write_allowed": bool(product_write_allowed),
        "authority_effect": "automation_continuation_only" if action else "none",
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
        "duplicate": False,
    }


def _require_current_head(authority_context: Mapping[str, Any], *, expected_head: str) -> None:
    current_head = _sha(authority_context.get("current_head_sha"), name="current_head_sha")
    if current_head != expected_head:
        raise EngineeringReconcileError(
            f"current branch head drifted from reconciled candidate: expected={expected_head} actual={current_head}"
        )


def _active_autonomy_probe(
    store: TaskRunStore,
    grant: Mapping[str, Any],
    *,
    repository: str,
) -> tuple[bool, bool, str]:
    probe = authorize_autonomous_action(
        store,
        grant,
        repository=repository,
        action="analyze_failure",
        context={},
    )
    return bool(probe.allowed), bool(probe.human_required), probe.reason


def _retry_count(store: TaskRunStore) -> int:
    reconciler = _reconciler_metadata(store)
    count = 0
    for entry in (reconciler.get("decisions") or {}).values():
        if not isinstance(entry, Mapping):
            continue
        outcome = entry.get("outcome")
        if isinstance(outcome, Mapping) and outcome.get("action") == "retry_transient_ci" and outcome.get("allowed") is True:
            count += 1
    return count


def _checkpoint_transport_failure(
    store: TaskRunStore,
    *,
    refs: Iterable[str],
    observation: CIObservation,
) -> None:
    local = local_metadata(store)
    admission = local.get("upload_admission") if isinstance(local.get("upload_admission"), Mapping) else {}
    store.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="CI_TRANSPORT_FAILURE_RECONCILED",
        workspace_fingerprint=_text(admission.get("workspace_fingerprint")),
        evidence_refs=refs,
        metadata={
            "run_id": int(observation.run_id),
            "run_attempt": int(observation.run_attempt),
            "head_sha": _text(observation.head_sha).lower(),
            "job_name": _text(observation.job_name),
            "classification": "transport_only",
        },
    )


def reconcile_ci_terminal(
    store: TaskRunStore,
    grant: Mapping[str, Any],
    *,
    repository: str,
    observation: CIObservation,
    product_verdict: str,
    transport_verdict: str,
    authority_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile one terminal CI attempt into one bounded next action.

    The controller is the durable decision boundary only. It never invokes GitHub,
    edits source, changes an oracle, merges, deploys, or reaches production. A
    separate adapter may execute an allowed returned action, but must preserve the
    exact TaskRun/run/head/grant bindings represented here.
    """

    run_id, run_attempt, head_sha, conclusion, refs = _validate_observation(observation)
    product = _normalize_verdict(product_verdict, name="product_verdict")
    transport = _normalize_verdict(transport_verdict, name="transport_verdict")
    expected = _binding_identity(store)
    actual = (run_id, run_attempt, head_sha)
    if actual != expected:
        raise EngineeringReconcileError(f"terminal CI binding mismatch: expected={expected} actual={actual}")

    key = _delivery_key(run_id=run_id, run_attempt=run_attempt, head_sha=head_sha)
    fingerprint = _observation_fingerprint(
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=head_sha,
        conclusion=conclusion,
        product_verdict=product,
        transport_verdict=transport,
    )
    existing = _existing_outcome(store, key=key, fingerprint=fingerprint)
    if existing is not None:
        return existing

    context = dict(authority_context or {})
    if conclusion == "success":
        if product != "PASS" or transport != "PASS":
            raise EngineeringReconcileError("successful CI conclusion requires terminal product and transport PASS evidence")
        try:
            record_ci_result(
                store,
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                conclusion="success",
                job_name=_text(observation.job_name),
                log_text=observation.log_text,
                evidence_refs=refs,
            )
        except (CIFeedbackBindingError, LocalFirstGovernanceError) as exc:
            raise EngineeringReconcileError(str(exc)) from exc
        outcome = _base_outcome(
            store,
            key=key,
            decision="COMPLETE",
            action=None,
            allowed=True,
            human_required=False,
            failure_class=None,
            reason="existing local-first completion contract accepted exact terminal CI PASS evidence",
        )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    if product == "PASS" and transport == "FAIL":
        _checkpoint_transport_failure(store, refs=refs, observation=observation)
        outcome = _base_outcome(
            store,
            key=key,
            decision="STOP_TRANSPORT_FAILURE",
            action=None,
            allowed=False,
            human_required=True,
            failure_class="TRANSPORT_FAILURE",
            reason="product gates are green; orchestration/status transport failure cannot authorize product repair",
        )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    if product != "FAIL":
        _checkpoint_transport_failure(store, refs=refs, observation=observation)
        outcome = _base_outcome(
            store,
            key=key,
            decision="STOP_INSUFFICIENT_EVIDENCE",
            action=None,
            allowed=False,
            human_required=True,
            failure_class=None,
            reason="terminal failure lacks authoritative product-failure evidence; fail closed",
        )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    autonomy_allowed, autonomy_human, autonomy_reason = _active_autonomy_probe(
        store,
        grant,
        repository=repository,
    )
    if not autonomy_allowed:
        outcome = _base_outcome(
            store,
            key=key,
            decision="STOP_AUTONOMY",
            action=None,
            allowed=False,
            human_required=autonomy_human,
            failure_class=None,
            reason=autonomy_reason,
        )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    _require_current_head(context, expected_head=head_sha)
    try:
        failure = record_ci_result(
            store,
            run_id=run_id,
            run_attempt=run_attempt,
            head_sha=head_sha,
            conclusion=conclusion,
            job_name=_text(observation.job_name),
            log_text=observation.log_text,
            evidence_refs=refs,
        )
    except (CIFeedbackBindingError, LocalFirstGovernanceError) as exc:
        outcome = _base_outcome(
            store,
            key=key,
            decision="STOP_LOCAL_GOVERNANCE",
            action=None,
            allowed=False,
            human_required=True,
            failure_class=None,
            reason=f"existing local-first governance rejected CI feedback: {exc}",
        )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    if failure is None:
        raise EngineeringReconcileError("non-success CI observation unexpectedly produced a success result")

    if failure.kind == "code_or_contract":
        local = local_metadata(store)
        counters = local.get("counters") if isinstance(local.get("counters"), Mapping) else {}
        try:
            next_repair_round = int(counters.get("local_repair_rounds") or 0) + 1
        except (TypeError, ValueError):
            next_repair_round = -1
        authorization = authorize_autonomous_action(
            store,
            grant,
            repository=repository,
            action="repair_meaningful_product_red",
            context={
                "failure_class": "PRODUCT_SOURCE_FAILURE",
                "underlying_write_authority": context.get("underlying_write_authority") is True,
                "exact_write_scope": context.get("exact_write_scope") is True,
                "repair_round": next_repair_round,
            },
        )
        if authorization.allowed:
            outcome = _base_outcome(
                store,
                key=key,
                decision="REPAIR_PRODUCT",
                action="repair_meaningful_product_red",
                allowed=True,
                human_required=False,
                failure_class="PRODUCT_SOURCE_FAILURE",
                reason=authorization.reason,
                product_write_allowed=True,
            )
        else:
            outcome = _base_outcome(
                store,
                key=key,
                decision="STOP_REPAIR_AUTHORITY",
                action=None,
                allowed=False,
                human_required=authorization.human_required,
                failure_class="PRODUCT_SOURCE_FAILURE",
                reason=authorization.reason,
            )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    if failure.kind == "test_defect":
        outcome = _base_outcome(
            store,
            key=key,
            decision="STOP_TEST_AUTHORITY",
            action=None,
            allowed=False,
            human_required=True,
            failure_class="TEST_DEFECT",
            reason=failure.reason,
        )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    if failure.automatic_retry_allowed:
        validation_retry = _retry_count(store) + 1
        authorization = authorize_autonomous_action(
            store,
            grant,
            repository=repository,
            action="retry_transient_ci",
            context={
                "failure_class": "TRANSIENT_INFRA_FAILURE",
                "same_candidate": True,
                "validation_retry": validation_retry,
            },
        )
        if authorization.allowed:
            outcome = _base_outcome(
                store,
                key=key,
                decision="RETRY_CI",
                action="retry_transient_ci",
                allowed=True,
                human_required=False,
                failure_class="TRANSIENT_INFRA_FAILURE",
                reason=authorization.reason,
            )
        else:
            outcome = _base_outcome(
                store,
                key=key,
                decision="STOP_RETRY_BUDGET",
                action=None,
                allowed=False,
                human_required=authorization.human_required,
                failure_class="TRANSIENT_INFRA_FAILURE",
                reason=authorization.reason,
            )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    outcome = _base_outcome(
        store,
        key=key,
        decision="STOP_NON_PRODUCT_AUTHORITY",
        action=None,
        allowed=False,
        human_required=True,
        failure_class=failure.kind.upper(),
        reason=failure.reason,
    )
    return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)


def _validate_stage1_failure(
    store: TaskRunStore,
    failure_case: Mapping[str, Any],
    *,
    repository: str,
    current_head_sha: str,
) -> tuple[int, int, str, str, str]:
    if _text(store.payload.get("task_kind")) != "github-governed-repair":
        raise EngineeringReconcileError("Stage-1 reconciliation requires the existing github-governed-repair TaskRun")
    payload = dict(failure_case)
    if payload.get("schema") != "github-failure-ingest@1" or payload.get("status") != "INGESTED":
        raise EngineeringReconcileError("invalid Stage-1 failure-case contract")
    repo = _text(repository)
    if _text(payload.get("repository")) != repo:
        raise EngineeringReconcileError("Stage-1 failure repository does not match requested repository")
    if payload.get("production_closed") is not False:
        raise EngineeringReconcileError("Stage-1 failure evidence crossed production boundary")
    if payload.get("same_repository") is not True:
        raise EngineeringReconcileError("cross-repository Stage-1 failure cannot enter autonomous reconciliation")

    binding = store.payload.get("binding")
    if not isinstance(binding, Mapping):
        raise EngineeringReconcileError("Stage-1 TaskRun immutable binding is missing")
    expected = {
        "repository": payload.get("repository"),
        "workflow_name": payload.get("workflow_name"),
        "workflow_run_id": payload.get("workflow_run_id"),
        "workflow_run_attempt": payload.get("workflow_run_attempt"),
        "head_sha": payload.get("head_sha"),
        "failure_signature": payload.get("failure_signature"),
        "branch": payload.get("repair_branch"),
        "base_sha": payload.get("head_sha"),
    }
    for field, value in expected.items():
        if _text(binding.get(field)) != _text(value):
            raise EngineeringReconcileError(f"Stage-1 TaskRun binding mismatch: {field}")

    try:
        run_id = int(payload.get("workflow_run_id"))
        run_attempt = int(payload.get("workflow_run_attempt"))
    except (TypeError, ValueError) as exc:
        raise EngineeringReconcileError("Stage-1 run identity must be numeric") from exc
    if run_id < 1 or run_attempt < 1:
        raise EngineeringReconcileError("Stage-1 run identity must be positive")
    head_sha = _sha(payload.get("head_sha"), name="Stage-1 head_sha")
    if _sha(current_head_sha, name="current_head_sha") != head_sha:
        raise EngineeringReconcileError("current branch head drifted from Stage-1 failure candidate")
    failure_signature = _text(payload.get("failure_signature"))
    if not re.fullmatch(r"[0-9a-f]{64}", failure_signature):
        raise EngineeringReconcileError("Stage-1 failure signature is missing or malformed")
    classification = _text(payload.get("classification")).lower()
    if not classification:
        raise EngineeringReconcileError("Stage-1 failure classification is missing")
    return run_id, run_attempt, head_sha, failure_signature, classification


def reconcile_stage1_failure(
    store: TaskRunStore,
    grant: Mapping[str, Any],
    *,
    repository: str,
    failure_case: Mapping[str, Any],
    current_head_sha: str,
) -> dict[str, Any]:
    """Normalize trusted Stage-1 evidence into the existing M3 decision contract.

    This is a source adapter inside the same EngineeringTaskController. It does not
    create a second TaskRun, change Stage-1 lifecycle ownership, execute GitHub
    network calls, or mint write/test/merge/deploy/production authority.
    """

    run_id, run_attempt, head_sha, failure_signature, classification = _validate_stage1_failure(
        store,
        failure_case,
        repository=repository,
        current_head_sha=current_head_sha,
    )
    recovery_hint = failure_case.get("recovery_policy") if isinstance(failure_case.get("recovery_policy"), Mapping) else {}
    fingerprint = _digest(
        {
            "source": "stage1",
            "run_id": run_id,
            "run_attempt": run_attempt,
            "head_sha": head_sha,
            "failure_signature": failure_signature,
            "classification": classification,
            "repair_allowed": failure_case.get("repair_allowed") is True,
            "recovery_disposition": recovery_hint.get("disposition"),
        }
    )
    key = _delivery_key(run_id=run_id, run_attempt=run_attempt, head_sha=head_sha)
    existing = _existing_outcome(store, key=key, fingerprint=fingerprint)
    if existing is not None:
        return existing

    autonomy_allowed, autonomy_human, autonomy_reason = _active_autonomy_probe(
        store,
        grant,
        repository=repository,
    )
    if not autonomy_allowed:
        outcome = _base_outcome(
            store,
            key=key,
            decision="STOP_AUTONOMY",
            action=None,
            allowed=False,
            human_required=autonomy_human,
            failure_class=None,
            reason=autonomy_reason,
        )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    if classification == "code_or_contract":
        candidate_paths = failure_case.get("candidate_paths")
        exact_stage1_write_authority = bool(
            failure_case.get("repair_allowed") is True
            and isinstance(candidate_paths, list)
            and candidate_paths
            and failure_case.get("same_repository") is True
        )
        authorization = authorize_autonomous_action(
            store,
            grant,
            repository=repository,
            action="repair_meaningful_product_red",
            context={
                "failure_class": "PRODUCT_SOURCE_FAILURE",
                "underlying_write_authority": exact_stage1_write_authority,
                "exact_write_scope": exact_stage1_write_authority,
                "repair_round": 1,
            },
        )
        if authorization.allowed:
            outcome = _base_outcome(
                store,
                key=key,
                decision="REPAIR_PRODUCT",
                action="repair_meaningful_product_red",
                allowed=True,
                human_required=False,
                failure_class="PRODUCT_SOURCE_FAILURE",
                reason=authorization.reason,
                product_write_allowed=True,
            )
        else:
            outcome = _base_outcome(
                store,
                key=key,
                decision="STOP_REPAIR_AUTHORITY",
                action=None,
                allowed=False,
                human_required=authorization.human_required,
                failure_class="PRODUCT_SOURCE_FAILURE",
                reason=authorization.reason,
            )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    retry_failure_class = _STAGE1_RETRYABLE_FAILURES.get(classification)
    if retry_failure_class:
        authorization = authorize_autonomous_action(
            store,
            grant,
            repository=repository,
            action="retry_transient_ci",
            context={
                "failure_class": retry_failure_class,
                "same_candidate": True,
                "validation_retry": _retry_count(store) + 1,
            },
        )
        if authorization.allowed:
            outcome = _base_outcome(
                store,
                key=key,
                decision="RETRY_CI",
                action="retry_transient_ci",
                allowed=True,
                human_required=False,
                failure_class=retry_failure_class,
                reason=authorization.reason,
            )
        else:
            outcome = _base_outcome(
                store,
                key=key,
                decision="STOP_RETRY_BUDGET",
                action=None,
                allowed=False,
                human_required=authorization.human_required,
                failure_class=retry_failure_class,
                reason=authorization.reason,
            )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    recovery = decide_recovery(
        repair_route=(failure_case.get("repair_route") if isinstance(failure_case.get("repair_route"), Mapping) else {}),
        classification=classification,
        diagnosis_attempt=int(recovery_hint.get("diagnosis_attempt") or 0),
        max_diagnosis_attempts=int(recovery_hint.get("max_diagnosis_attempts") or 2),
        retry_count=int(recovery_hint.get("retry_count") or 0),
        max_retry_count=int(recovery_hint.get("max_retry_count") or 3),
    )
    if recovery.get("disposition") == AUTO_DIAGNOSE:
        authorization = authorize_autonomous_action(
            store,
            grant,
            repository=repository,
            action="analyze_failure",
            context={
                "failure_class": "INSUFFICIENT_EVIDENCE",
                "read_only": True,
                "source_write_allowed": False,
            },
        )
        if authorization.allowed:
            outcome = _base_outcome(
                store,
                key=key,
                decision="ANALYZE_FAILURE",
                action="analyze_failure",
                allowed=True,
                human_required=False,
                failure_class="INSUFFICIENT_EVIDENCE",
                reason=recovery["reason"],
                product_write_allowed=False,
            )
        else:
            outcome = _base_outcome(
                store,
                key=key,
                decision="STOP_DIAGNOSIS_AUTHORITY",
                action=None,
                allowed=False,
                human_required=authorization.human_required,
                failure_class="INSUFFICIENT_EVIDENCE",
                reason=authorization.reason,
            )
        return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)

    if classification == "protected_baseline_drift":
        failure_class = "PROTECTED_BASELINE_DRIFT"
        reason = "protected baseline drift requires governed baseline acceptance; autonomous product repair is not authorized"
    elif classification == "unknown_failure_without_gate_evidence":
        failure_class = "INSUFFICIENT_EVIDENCE"
        reason = recovery["reason"]
    else:
        failure_class = classification.upper()
        reason = recovery["reason"] or "Stage-1 failure class has no bounded autonomous continuation contract"
    outcome = _base_outcome(
        store,
        key=key,
        decision="STOP_NON_PRODUCT_AUTHORITY",
        action=None,
        allowed=False,
        human_required=True,
        failure_class=failure_class,
        reason=reason,
    )
    return _persist_outcome(store, key=key, fingerprint=fingerprint, outcome=outcome)
