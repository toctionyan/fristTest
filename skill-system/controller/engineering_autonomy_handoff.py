from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from engineering_autonomy_dispatch import (
    AutonomyDispatchError,
    build_owner_authorization_evidence,
    compile_dispatch_plan,
)
from engineering_autonomy_network import compile_network_request


HANDOFF_BUNDLE_SCHEMA = "engineering-autonomy-handoff-bundle@1"
HANDOFF_RESULT_SCHEMA = "engineering-autonomy-handoff-result@1"


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha(value: object, *, name: str) -> str:
    text = _text(value).lower()
    if not re.fullmatch(r"[0-9a-f]{7,64}", text):
        raise AutonomyDispatchError(f"{name} must be a hexadecimal commit-like identifier")
    return text


def _positive_int(value: object, *, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AutonomyDispatchError(f"{name} must be an integer") from exc
    if result < 1:
        raise AutonomyDispatchError(f"{name} must be positive")
    return result


def _mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AutonomyDispatchError(f"{name} must be an object")
    return value


def _validate_persisted_reconcile_outcome(
    task: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> None:
    metadata = _mapping(task.get("metadata"), name="TaskRun metadata")
    reconciler = _mapping(
        metadata.get("engineering_reconciler"),
        name="TaskRun engineering reconciler metadata",
    )
    if reconciler.get("schema") != "engineering-task-reconciler@1":
        raise AutonomyDispatchError("TaskRun engineering reconciler schema is missing or invalid")
    decisions = _mapping(
        reconciler.get("decisions"),
        name="TaskRun engineering reconciler decisions",
    )
    delivery_key = _text(outcome.get("delivery_key"))
    entry = decisions.get(delivery_key)
    if not isinstance(entry, Mapping):
        raise AutonomyDispatchError(
            "reconciliation outcome is not durably persisted on this TaskRun"
        )
    persisted = entry.get("outcome")
    if not isinstance(persisted, Mapping) or dict(persisted) != dict(outcome):
        raise AutonomyDispatchError(
            "supplied reconciliation outcome differs from the TaskRun persisted decision"
        )


def _validate_local_first_lineage(
    task: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> tuple[int, int, str]:
    metadata = _mapping(task.get("metadata"), name="TaskRun metadata")
    local = _mapping(metadata.get("local_first"), name="TaskRun local-first metadata")
    binding = _mapping(local.get("ci_binding"), name="TaskRun local-first CI binding")
    run_id = _positive_int(binding.get("run_id"), name="bound source run id")
    run_attempt = _positive_int(
        binding.get("run_attempt"), name="bound source run attempt"
    )
    head_sha = _sha(binding.get("head_sha"), name="bound source head sha")
    delivery_key = f"{run_id}:{run_attempt}:{head_sha}"
    if _text(outcome.get("delivery_key")) != delivery_key:
        raise AutonomyDispatchError(
            "TaskRun local-first CI binding differs from the persisted reconciliation outcome"
        )

    feedback = local.get("ci_feedback")
    if not isinstance(feedback, list):
        raise AutonomyDispatchError("TaskRun local-first CI feedback is malformed")
    decision = _text(outcome.get("decision"))
    matching = [
        row
        for row in feedback
        if isinstance(row, Mapping)
        and int(row.get("run_id") or 0) == run_id
        and int(row.get("run_attempt") or 0) == run_attempt
        and _text(row.get("head_sha")) == head_sha
    ]
    if decision == "REPAIR_PRODUCT":
        if not any(
            _text(row.get("kind")) == "code_or_contract"
            and row.get("product_code_write_allowed") is True
            for row in matching
        ):
            raise AutonomyDispatchError(
                "product repair handoff lacks persisted meaningful code/contract CI feedback"
            )
    elif decision == "RETRY_CI":
        if not any(
            row.get("automatic_retry_allowed") is True
            and row.get("product_code_write_allowed") is False
            for row in matching
        ):
            raise AutonomyDispatchError(
                "CI retry handoff lacks persisted retryable non-product-write feedback"
            )
    elif decision.startswith("STOP_") or decision == "COMPLETE":
        pass
    else:
        raise AutonomyDispatchError(
            f"unsupported persisted reconciliation decision: {decision!r}"
        )
    return run_id, run_attempt, head_sha


def validate_handoff_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(bundle)
    if payload.get("schema") != HANDOFF_BUNDLE_SCHEMA:
        raise AutonomyDispatchError("unsupported engineering autonomy handoff bundle schema")
    task = _mapping(payload.get("task"), name="handoff TaskRun")
    grant = _mapping(payload.get("grant"), name="handoff autonomy grant")
    outcome = _mapping(payload.get("reconcile_outcome"), name="handoff reconcile outcome")
    if _text(task.get("task_id")) != _text(outcome.get("task_id")):
        raise AutonomyDispatchError("handoff TaskRun and reconciliation outcome task mismatch")
    if not _text(payload.get("failure_signature")):
        raise AutonomyDispatchError("handoff bundle requires failure_signature")
    source_pr_number = _positive_int(
        payload.get("source_pr_number"), name="source_pr_number"
    )
    run_id, run_attempt, head_sha = _validate_local_first_lineage(task, outcome)
    _validate_persisted_reconcile_outcome(task, outcome)
    return {
        "schema": HANDOFF_BUNDLE_SCHEMA,
        "task": dict(task),
        "grant": dict(grant),
        "reconcile_outcome": dict(outcome),
        "failure_signature": _text(payload.get("failure_signature")),
        "source_pr_number": source_pr_number,
        "source_run_id": run_id,
        "source_run_attempt": run_attempt,
        "source_head_sha": head_sha,
    }


def compile_trusted_handoff(
    bundle: Mapping[str, Any],
    *,
    repository: str,
    actor: str,
    event_name: str,
    trusted_workflow_ref: str,
    authorization_id: str,
    handoff_run_id: int,
    handoff_run_attempt: int,
    observed_pr_number: int,
    observed_pr_head_sha: str,
    observed_pr_draft: bool,
    observed_pr_state: str,
) -> dict[str, Any]:
    """Turn one owner-dispatched, exact-current Draft PR handoff into network data.

    The handoff workflow remains a continuation authority only. This function
    requires the product/retry decision to have already been persisted by the
    existing EngineeringTaskController on the same local-first TaskRun.
    """

    payload = validate_handoff_bundle(bundle)
    if _text(event_name) != "workflow_dispatch":
        raise AutonomyDispatchError("trusted autonomy handoff requires workflow_dispatch")
    if not _text(actor):
        raise AutonomyDispatchError("trusted autonomy handoff requires an actor")
    if int(observed_pr_number) != int(payload["source_pr_number"]):
        raise AutonomyDispatchError("observed Draft PR number differs from handoff bundle")
    if _text(observed_pr_state).lower() != "open" or observed_pr_draft is not True:
        raise AutonomyDispatchError("autonomy handoff requires the source PR to remain open and Draft")
    if _sha(observed_pr_head_sha, name="observed PR head sha") != payload["source_head_sha"]:
        raise AutonomyDispatchError(
            "source Draft PR head drifted from the reconciled CI candidate"
        )

    task = payload["task"]
    grant = payload["grant"]
    outcome = payload["reconcile_outcome"]
    authorization = build_owner_authorization_evidence(
        task=task,
        grant=grant,
        repository=repository,
        source_run_id=payload["source_run_id"],
        source_run_attempt=payload["source_run_attempt"],
        source_head_sha=payload["source_head_sha"],
        failure_signature=payload["failure_signature"],
        actor=actor,
        event_name=event_name,
        trusted_workflow_ref=trusted_workflow_ref,
        authorization_id=authorization_id,
    )
    plan = compile_dispatch_plan(
        # compile_dispatch_plan only requires TaskRunStore.payload and does not
        # create or expand authority. The tiny view below intentionally presents
        # the exact already-validated durable payload without synthesizing state.
        _TaskPayloadView(task),
        grant,
        authorization,
        reconcile_outcome=outcome,
        repository=repository,
        trusted_workflow_ref=trusted_workflow_ref,
        current_head_sha=payload["source_head_sha"],
    )
    request = compile_network_request(
        plan,
        task=task,
        grant=grant,
        authorization_evidence=authorization,
        reconcile_outcome=outcome,
        repository=repository,
        trusted_workflow_ref=trusted_workflow_ref,
        handoff_run_id=handoff_run_id,
        handoff_run_attempt=handoff_run_attempt,
    )
    result = {
        "schema": HANDOFF_RESULT_SCHEMA,
        "source_pr_number": payload["source_pr_number"],
        "source_run_id": payload["source_run_id"],
        "source_run_attempt": payload["source_run_attempt"],
        "source_head_sha": payload["source_head_sha"],
        "task": task,
        "grant": grant,
        "reconcile_outcome": outcome,
        "authorization": authorization,
        "plan": plan,
        "network_request": request,
        "write_authority_effect": False,
        "test_authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    result["result_sha256"] = _digest(result)
    return result


class _TaskPayloadView:
    """Read-only adapter for the compiler's existing TaskRunStore interface."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
