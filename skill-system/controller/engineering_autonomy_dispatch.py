from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from autonomy_grant import AutonomyGrantError, task_binding_fingerprint, validate_autonomy_grant
from task_run import TaskRunStore


OWNER_AUTHORIZATION_SCHEMA = "engineering-owner-autonomy-authorization@1"
DISPATCH_PLAN_SCHEMA = "engineering-autonomy-dispatch-plan@1"
DISPATCH_RECEIPT_SCHEMA = "engineering-autonomy-dispatch-receipt@1"
RECONCILE_DECISION_SCHEMA = "engineering-reconcile-decision@1"
TRUSTED_AUTHORIZATION_WORKFLOW = ".github/workflows/engineering-autonomy-authorize.yml"
STAGE2_WORKFLOW = ".github/workflows/governed-ci-repair-stage2.yml"
STAGE2_PROTECTED_ENVIRONMENT = "production-certification"


class AutonomyDispatchError(RuntimeError):
    """Fail-closed error for trusted owner authorization and network dispatch planning."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


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


def _task_binding(task: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = task.get("binding")
    if not isinstance(binding, Mapping) or not binding:
        raise AutonomyDispatchError("owner authorization requires an immutable TaskRun binding")
    return binding


def _validate_trusted_workflow_ref(value: object) -> str:
    text = _text(value)
    pattern = re.compile(
        rf"^{re.escape(TRUSTED_AUTHORIZATION_WORKFLOW)}@(?P<sha>[0-9a-f]{{40}})$"
    )
    if not pattern.fullmatch(text):
        raise AutonomyDispatchError(
            "owner authorization must originate from the trusted engineering-autonomy-authorize workflow at an exact 40-hex ref"
        )
    return text


def _validate_bound_grant(
    task: Mapping[str, Any],
    grant: Mapping[str, Any],
    *,
    repository: str,
) -> dict[str, Any]:
    binding = _task_binding(task)
    try:
        validated = validate_autonomy_grant(
            grant,
            task=task,
            repository=_text(repository),
            branch=_text(binding.get("branch")),
            base_sha=_text(binding.get("base_sha")),
        )
    except (AutonomyGrantError, TypeError, ValueError) as exc:
        raise AutonomyDispatchError(f"autonomy grant validation failed: {exc}") from exc

    metadata = task.get("metadata") if isinstance(task.get("metadata"), Mapping) else {}
    bound = metadata.get("engineering_autonomy")
    if not isinstance(bound, Mapping):
        raise AutonomyDispatchError("TaskRun has no bound engineering autonomy grant")
    if bound.get("status") != "ACTIVE":
        raise AutonomyDispatchError("TaskRun engineering autonomy grant is not active")
    if (
        _text(bound.get("grant_id")) != _text(validated.get("grant_id"))
        or _text(bound.get("grant_sha256")) != _text(validated.get("grant_sha256"))
        or _text(bound.get("task_binding_fingerprint")) != task_binding_fingerprint(task)
        or _text(bound.get("repository")) != _text(repository)
    ):
        raise AutonomyDispatchError("TaskRun autonomy binding does not match the supplied grant")
    return validated


def _authority_flags_are_closed(payload: Mapping[str, Any]) -> bool:
    return all(
        payload.get(field) is False
        for field in (
            "write_authority_effect",
            "test_authority_effect",
            "merge_allowed",
            "deploy_allowed",
            "production_closed",
        )
    )


def build_owner_authorization_evidence(
    *,
    task: Mapping[str, Any],
    grant: Mapping[str, Any],
    repository: str,
    source_run_id: int,
    source_run_attempt: int,
    source_head_sha: str,
    failure_signature: str,
    actor: str,
    event_name: str,
    trusted_workflow_ref: str,
    authorization_id: str,
) -> dict[str, Any]:
    """Build owner-authorization evidence; this does not execute or expand any authority.

    The trusted workflow is the evidence producer. A future network adapter may
    persist this document, but candidate-controlled pull_request events are never
    accepted as owner authorization.
    """

    repo = _text(repository)
    if not repo or "/" not in repo:
        raise AutonomyDispatchError("repository must be owner/name")
    if _text(event_name) != "workflow_dispatch":
        raise AutonomyDispatchError("owner autonomy authorization requires a workflow_dispatch event")
    actor_name = _text(actor)
    if not actor_name:
        raise AutonomyDispatchError("owner autonomy authorization requires an actor")
    workflow_ref = _validate_trusted_workflow_ref(trusted_workflow_ref)
    auth_id = _text(authorization_id)
    if not auth_id:
        raise AutonomyDispatchError("owner autonomy authorization requires authorization_id")
    failure = _text(failure_signature)
    if not failure:
        raise AutonomyDispatchError("owner autonomy authorization requires a failure signature")
    run_id = _positive_int(source_run_id, name="source_run_id")
    run_attempt = _positive_int(source_run_attempt, name="source_run_attempt")
    head_sha = _sha(source_head_sha, name="source_head_sha")
    validated_grant = _validate_bound_grant(task, grant, repository=repo)

    payload: dict[str, Any] = {
        "schema": OWNER_AUTHORIZATION_SCHEMA,
        "authorization_id": auth_id,
        "status": "ACTIVE",
        "repository": repo,
        "task_id": _text(task.get("task_id")),
        "task_binding_fingerprint": task_binding_fingerprint(task),
        "source_run_id": run_id,
        "source_run_attempt": run_attempt,
        "source_head_sha": head_sha,
        "failure_signature": failure,
        "grant_id": validated_grant["grant_id"],
        "grant_sha256": validated_grant["grant_sha256"],
        "actor": actor_name,
        "event_name": "workflow_dispatch",
        "trusted_workflow_ref": workflow_ref,
        "issued_at": _now(),
        "authority_effect": "autonomy_continuation_authorization_only",
        "write_authority_effect": False,
        "test_authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    payload["authorization_sha256"] = _digest(payload)
    return payload


def validate_owner_authorization_evidence(
    evidence: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
    grant: Mapping[str, Any],
    repository: str,
    trusted_workflow_ref: str,
) -> dict[str, Any]:
    payload = dict(evidence)
    if payload.get("schema") != OWNER_AUTHORIZATION_SCHEMA:
        raise AutonomyDispatchError("unsupported owner autonomy authorization schema")
    if payload.get("status") != "ACTIVE":
        raise AutonomyDispatchError("owner autonomy authorization is not active")
    expected_digest = _text(payload.pop("authorization_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise AutonomyDispatchError("owner autonomy authorization digest is missing or invalid")
    if _digest(payload) != expected_digest:
        raise AutonomyDispatchError("owner autonomy authorization digest mismatch")
    payload["authorization_sha256"] = expected_digest

    repo = _text(repository)
    expected_workflow_ref = _validate_trusted_workflow_ref(trusted_workflow_ref)
    if _text(payload.get("event_name")) != "workflow_dispatch":
        raise AutonomyDispatchError("owner autonomy authorization event drifted from workflow_dispatch")
    observed_workflow_ref = _validate_trusted_workflow_ref(payload.get("trusted_workflow_ref"))
    if observed_workflow_ref != expected_workflow_ref:
        raise AutonomyDispatchError("owner autonomy authorization trusted workflow binding mismatch")
    if not _text(payload.get("actor")):
        raise AutonomyDispatchError("owner autonomy authorization actor is missing")
    if not _text(payload.get("authorization_id")):
        raise AutonomyDispatchError("owner autonomy authorization id is missing")
    if not _text(payload.get("failure_signature")):
        raise AutonomyDispatchError("owner autonomy authorization failure signature is missing")
    _positive_int(payload.get("source_run_id"), name="source_run_id")
    _positive_int(payload.get("source_run_attempt"), name="source_run_attempt")
    _sha(payload.get("source_head_sha"), name="source_head_sha")

    validated_grant = _validate_bound_grant(task, grant, repository=repo)
    expected = {
        "repository": repo,
        "task_id": _text(task.get("task_id")),
        "task_binding_fingerprint": task_binding_fingerprint(task),
        "grant_id": _text(validated_grant.get("grant_id")),
        "grant_sha256": _text(validated_grant.get("grant_sha256")),
    }
    for key, value in expected.items():
        if _text(payload.get(key)) != value:
            raise AutonomyDispatchError(f"owner autonomy authorization binding mismatch: {key}")
    if payload.get("authority_effect") != "autonomy_continuation_authorization_only":
        raise AutonomyDispatchError("owner autonomy authorization authority effect drifted")
    if not _authority_flags_are_closed(payload):
        raise AutonomyDispatchError("owner autonomy authorization cannot carry write/test/merge/deploy/production authority")
    return payload


def _validate_reconcile_outcome(
    outcome: Mapping[str, Any],
    *,
    authorization: Mapping[str, Any],
    task_id: str,
) -> dict[str, Any]:
    payload = dict(outcome)
    if payload.get("schema") != RECONCILE_DECISION_SCHEMA:
        raise AutonomyDispatchError("unsupported engineering reconciliation decision schema")
    if _text(payload.get("task_id")) != task_id:
        raise AutonomyDispatchError("reconciliation decision task binding mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", _text(payload.get("decision_id"))):
        raise AutonomyDispatchError("reconciliation decision_id is missing or malformed")
    if any(payload.get(field) is not False for field in ("merge_allowed", "deploy_allowed", "production_closed")):
        raise AutonomyDispatchError("reconciliation decision attempted to cross merge/deploy/production boundary")
    delivery_key = (
        f"{int(authorization['source_run_id'])}:"
        f"{int(authorization['source_run_attempt'])}:"
        f"{authorization['source_head_sha']}"
    )
    if _text(payload.get("delivery_key")) != delivery_key:
        raise AutonomyDispatchError("reconciliation decision does not match authorized source CI lineage")
    return payload


def _matching_prior_receipt(
    receipts: Iterable[Mapping[str, Any]],
    *,
    decision_id: str,
    authorization_sha256: str,
) -> bool:
    for receipt in receipts:
        if not isinstance(receipt, Mapping) or _text(receipt.get("decision_id")) != decision_id:
            continue
        if receipt.get("schema") != DISPATCH_RECEIPT_SCHEMA:
            raise AutonomyDispatchError("matching dispatch receipt has an unsupported schema")
        if _text(receipt.get("authorization_sha256")) != authorization_sha256:
            raise AutonomyDispatchError("matching dispatch receipt belongs to different owner authorization evidence")
        status = _text(receipt.get("status")).upper()
        if status == "DISPATCHED":
            return True
        if status not in {"PENDING", "FAILED"}:
            raise AutonomyDispatchError("matching dispatch receipt has an unsupported status")
    return False


def _plan_base(
    *,
    outcome: Mapping[str, Any],
    authorization: Mapping[str, Any],
    kind: str,
    workflow: str | None,
    required_environment: str | None,
    inputs: Mapping[str, str] | None = None,
    product_write_allowed: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": DISPATCH_PLAN_SCHEMA,
        "kind": kind,
        "decision_id": _text(outcome.get("decision_id")),
        "authorization_id": _text(authorization.get("authorization_id")),
        "authorization_sha256": _text(authorization.get("authorization_sha256")),
        "grant_id": _text(authorization.get("grant_id")),
        "grant_sha256": _text(authorization.get("grant_sha256")),
        "source_run_id": int(authorization["source_run_id"]),
        "source_run_attempt": int(authorization["source_run_attempt"]),
        "source_head_sha": _text(authorization.get("source_head_sha")),
        "failure_signature": _text(authorization.get("failure_signature")),
        "workflow": workflow,
        "required_environment": required_environment,
        "inputs": dict(inputs or {}),
        "product_write_allowed": bool(product_write_allowed),
        "authority_effect": "dispatch_plan_only",
        "write_authority_effect": False,
        "test_authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    payload["plan_sha256"] = _digest(payload)
    return payload


def compile_dispatch_plan(
    store: TaskRunStore,
    grant: Mapping[str, Any],
    authorization_evidence: Mapping[str, Any],
    *,
    reconcile_outcome: Mapping[str, Any],
    repository: str,
    trusted_workflow_ref: str,
    current_head_sha: str,
    prior_dispatch_receipts: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compile one trusted network action plan without performing that action.

    This compiler deliberately does not synthesize the legacy
    ``remote_repair_approval=explicitly-approved`` input. The Stage2 workflow must
    gain a separate, exact autonomy-evidence validation path before a future
    adapter is permitted to dispatch a repair request automatically.
    """

    authorization = validate_owner_authorization_evidence(
        authorization_evidence,
        task=store.payload,
        grant=grant,
        repository=repository,
        trusted_workflow_ref=trusted_workflow_ref,
    )
    outcome = _validate_reconcile_outcome(
        reconcile_outcome,
        authorization=authorization,
        task_id=_text(store.payload.get("task_id")),
    )
    decision_id = _text(outcome.get("decision_id"))
    if _matching_prior_receipt(
        prior_dispatch_receipts,
        decision_id=decision_id,
        authorization_sha256=_text(authorization.get("authorization_sha256")),
    ):
        return _plan_base(
            outcome=outcome,
            authorization=authorization,
            kind="NOOP_ALREADY_DISPATCHED",
            workflow=None,
            required_environment=None,
        )

    decision = _text(outcome.get("decision"))
    action = _text(outcome.get("action")) or None
    allowed = outcome.get("allowed") is True
    human_required = outcome.get("human_required") is True

    if decision.startswith("STOP_"):
        if allowed or action is not None:
            raise AutonomyDispatchError("STOP reconciliation decision cannot carry an allowed network action")
        return _plan_base(
            outcome=outcome,
            authorization=authorization,
            kind="NOOP_STOPPED",
            workflow=None,
            required_environment=None,
        )

    if decision == "COMPLETE":
        if action is not None:
            raise AutonomyDispatchError("COMPLETE reconciliation decision cannot carry a network action")
        return _plan_base(
            outcome=outcome,
            authorization=authorization,
            kind="NOOP_COMPLETE",
            workflow=None,
            required_environment=None,
        )

    current_head = _sha(current_head_sha, name="current_head_sha")
    if current_head != _text(authorization.get("source_head_sha")):
        raise AutonomyDispatchError(
            "current branch head no longer matches the owner-authorized CI candidate"
        )

    if decision == "REPAIR_PRODUCT":
        if (
            action != "repair_meaningful_product_red"
            or not allowed
            or human_required
            or _text(outcome.get("failure_class")) != "PRODUCT_SOURCE_FAILURE"
            or outcome.get("product_write_allowed") is not True
        ):
            raise AutonomyDispatchError("REPAIR_PRODUCT decision lacks exact meaningful-RED/write-authority proof")
        inputs = {
            "source_quality_run_id": str(authorization["source_run_id"]),
            "source_quality_run_attempt": str(authorization["source_run_attempt"]),
            "autonomy_authorization_id": _text(authorization.get("authorization_id")),
            "autonomy_authorization_sha256": _text(authorization.get("authorization_sha256")),
            "autonomy_grant_id": _text(authorization.get("grant_id")),
            "autonomy_grant_sha256": _text(authorization.get("grant_sha256")),
        }
        return _plan_base(
            outcome=outcome,
            authorization=authorization,
            kind="REQUEST_STAGE2_REPAIR",
            workflow=STAGE2_WORKFLOW,
            required_environment=STAGE2_PROTECTED_ENVIRONMENT,
            inputs=inputs,
            product_write_allowed=True,
        )

    if decision == "RETRY_CI":
        if (
            action != "retry_transient_ci"
            or not allowed
            or human_required
            or _text(outcome.get("failure_class")) != "TRANSIENT_INFRA_FAILURE"
            or outcome.get("product_write_allowed") is not False
        ):
            raise AutonomyDispatchError("RETRY_CI decision lacks exact same-candidate transient-failure proof")
        return _plan_base(
            outcome=outcome,
            authorization=authorization,
            kind="RERUN_SAME_CANDIDATE",
            workflow=None,
            required_environment=None,
            inputs={
                "source_quality_run_id": str(authorization["source_run_id"]),
                "source_quality_run_attempt": str(authorization["source_run_attempt"]),
            },
            product_write_allowed=False,
        )

    raise AutonomyDispatchError(f"unsupported reconciliation decision for network dispatch: {decision!r}")
