from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from engineering_autonomy_dispatch import (
    AutonomyDispatchError,
    DISPATCH_PLAN_SCHEMA,
    DISPATCH_RECEIPT_SCHEMA,
    STAGE2_PROTECTED_ENVIRONMENT,
    STAGE2_WORKFLOW,
    validate_owner_authorization_evidence,
)


NETWORK_REQUEST_SCHEMA = "engineering-autonomy-network-request@1"
NETWORK_REQUEST_KINDS = {
    "DISPATCH_STAGE2",
    "RERUN_SOURCE_RUN",
    "NOOP",
}
RECEIPT_STATUSES = {"PENDING", "DISPATCHED", "FAILED"}


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


def _validate_digest_bound_payload(
    payload: Mapping[str, Any],
    *,
    schema: str,
    digest_field: str,
    name: str,
) -> dict[str, Any]:
    result = dict(payload)
    if result.get("schema") != schema:
        raise AutonomyDispatchError(f"unsupported {name} schema")
    expected = _text(result.pop(digest_field))
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise AutonomyDispatchError(f"{name} digest is missing or malformed")
    if _digest(result) != expected:
        raise AutonomyDispatchError(f"{name} digest mismatch")
    result[digest_field] = expected
    return result


def validate_dispatch_plan(
    plan: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
    grant: Mapping[str, Any],
    authorization_evidence: Mapping[str, Any],
    reconcile_outcome: Mapping[str, Any],
    repository: str,
    trusted_workflow_ref: str,
) -> dict[str, Any]:
    """Validate a compiler-produced plan before any network-capable adapter consumes it.

    This validator never creates authority. It proves that the plan is an exact
    projection of already-bound TaskRun, grant, owner authorization, CI lineage,
    and reconciler decision. Any unknown or partially bound plan fails closed.
    """

    authorization = validate_owner_authorization_evidence(
        authorization_evidence,
        task=task,
        grant=grant,
        repository=repository,
        trusted_workflow_ref=trusted_workflow_ref,
    )
    payload = _validate_digest_bound_payload(
        plan,
        schema=DISPATCH_PLAN_SCHEMA,
        digest_field="plan_sha256",
        name="autonomy dispatch plan",
    )
    if any(
        payload.get(field) is not False
        for field in (
            "write_authority_effect",
            "test_authority_effect",
            "merge_allowed",
            "deploy_allowed",
            "production_closed",
        )
    ):
        raise AutonomyDispatchError(
            "autonomy dispatch plan cannot carry write/test/merge/deploy/production authority"
        )
    if payload.get("authority_effect") != "dispatch_plan_only":
        raise AutonomyDispatchError("autonomy dispatch plan authority effect drifted")

    expected = {
        "decision_id": _text(reconcile_outcome.get("decision_id")),
        "authorization_id": _text(authorization.get("authorization_id")),
        "authorization_sha256": _text(authorization.get("authorization_sha256")),
        "grant_id": _text(authorization.get("grant_id")),
        "grant_sha256": _text(authorization.get("grant_sha256")),
        "source_run_id": int(authorization["source_run_id"]),
        "source_run_attempt": int(authorization["source_run_attempt"]),
        "source_head_sha": _text(authorization.get("source_head_sha")),
        "failure_signature": _text(authorization.get("failure_signature")),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise AutonomyDispatchError(f"autonomy dispatch plan binding mismatch: {key}")

    delivery_key = (
        f"{expected['source_run_id']}:"
        f"{expected['source_run_attempt']}:"
        f"{expected['source_head_sha']}"
    )
    if _text(reconcile_outcome.get("delivery_key")) != delivery_key:
        raise AutonomyDispatchError(
            "reconciliation outcome does not match owner-authorized source lineage"
        )
    if any(
        reconcile_outcome.get(field) is not False
        for field in ("merge_allowed", "deploy_allowed", "production_closed")
    ):
        raise AutonomyDispatchError(
            "reconciliation outcome crossed the merge/deploy/production boundary"
        )

    kind = _text(payload.get("kind"))
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping):
        raise AutonomyDispatchError("autonomy dispatch plan inputs must be an object")
    normalized_inputs = {str(key): str(value) for key, value in inputs.items()}
    if "remote_repair_approval" in normalized_inputs:
        raise AutonomyDispatchError(
            "autonomy dispatch plan must never synthesize legacy manual Stage-2 approval"
        )

    if kind == "REQUEST_STAGE2_REPAIR":
        if (
            payload.get("workflow") != STAGE2_WORKFLOW
            or payload.get("required_environment") != STAGE2_PROTECTED_ENVIRONMENT
            or payload.get("product_write_allowed") is not True
            or _text(reconcile_outcome.get("decision")) != "REPAIR_PRODUCT"
            or _text(reconcile_outcome.get("action")) != "repair_meaningful_product_red"
            or reconcile_outcome.get("allowed") is not True
            or reconcile_outcome.get("human_required") is not False
            or _text(reconcile_outcome.get("failure_class")) != "PRODUCT_SOURCE_FAILURE"
            or reconcile_outcome.get("product_write_allowed") is not True
        ):
            raise AutonomyDispatchError(
                "Stage-2 repair plan lacks exact meaningful-product-RED authority"
            )
        required = {
            "source_quality_run_id": str(expected["source_run_id"]),
            "source_quality_run_attempt": str(expected["source_run_attempt"]),
            "autonomy_authorization_id": expected["authorization_id"],
            "autonomy_authorization_sha256": expected["authorization_sha256"],
            "autonomy_grant_id": expected["grant_id"],
            "autonomy_grant_sha256": expected["grant_sha256"],
        }
        if normalized_inputs != required:
            raise AutonomyDispatchError(
                "Stage-2 repair plan inputs do not exactly match the authorized handoff"
            )
    elif kind == "RERUN_SAME_CANDIDATE":
        if (
            payload.get("workflow") is not None
            or payload.get("required_environment") is not None
            or payload.get("product_write_allowed") is not False
            or _text(reconcile_outcome.get("decision")) != "RETRY_CI"
            or _text(reconcile_outcome.get("action")) != "retry_transient_ci"
            or reconcile_outcome.get("allowed") is not True
            or reconcile_outcome.get("human_required") is not False
            or _text(reconcile_outcome.get("failure_class")) != "TRANSIENT_INFRA_FAILURE"
            or reconcile_outcome.get("product_write_allowed") is not False
        ):
            raise AutonomyDispatchError(
                "same-candidate retry plan lacks exact transient-failure authority"
            )
        required = {
            "source_quality_run_id": str(expected["source_run_id"]),
            "source_quality_run_attempt": str(expected["source_run_attempt"]),
        }
        if normalized_inputs != required:
            raise AutonomyDispatchError(
                "same-candidate retry inputs do not match the authorized source attempt"
            )
    elif kind in {"NOOP_ALREADY_DISPATCHED", "NOOP_STOPPED", "NOOP_COMPLETE"}:
        if (
            payload.get("workflow") is not None
            or payload.get("required_environment") is not None
            or payload.get("product_write_allowed") is not False
            or normalized_inputs
        ):
            raise AutonomyDispatchError("NOOP dispatch plan cannot carry a network target")
    else:
        raise AutonomyDispatchError(f"unsupported autonomy dispatch plan kind: {kind!r}")
    return payload


def compile_network_request(
    plan: Mapping[str, Any],
    *,
    task: Mapping[str, Any],
    grant: Mapping[str, Any],
    authorization_evidence: Mapping[str, Any],
    reconcile_outcome: Mapping[str, Any],
    repository: str,
    trusted_workflow_ref: str,
    handoff_run_id: int,
    handoff_run_attempt: int,
) -> dict[str, Any]:
    """Compile the sole network request allowed by a validated autonomy plan.

    The returned object is data only. A trusted workflow/host adapter may execute
    it after independently checking the exact current GitHub run/head identity.
    """

    validated = validate_dispatch_plan(
        plan,
        task=task,
        grant=grant,
        authorization_evidence=authorization_evidence,
        reconcile_outcome=reconcile_outcome,
        repository=repository,
        trusted_workflow_ref=trusted_workflow_ref,
    )
    handoff_id = _positive_int(handoff_run_id, name="handoff_run_id")
    handoff_attempt = _positive_int(handoff_run_attempt, name="handoff_run_attempt")
    kind = _text(validated.get("kind"))

    if kind == "REQUEST_STAGE2_REPAIR":
        request = {
            "schema": NETWORK_REQUEST_SCHEMA,
            "kind": "DISPATCH_STAGE2",
            "repository": _text(repository),
            "workflow": STAGE2_WORKFLOW,
            "ref": "main",
            "source_run_id": int(validated["source_run_id"]),
            "source_run_attempt": int(validated["source_run_attempt"]),
            "source_head_sha": _sha(
                validated.get("source_head_sha"), name="source_head_sha"
            ),
            "handoff_run_id": handoff_id,
            "handoff_run_attempt": handoff_attempt,
            "inputs": {
                "source_run_id": str(validated["source_run_id"]),
                "source_run_attempt": str(validated["source_run_attempt"]),
                "autonomy_handoff_run_id": str(handoff_id),
                "autonomy_handoff_run_attempt": str(handoff_attempt),
                "autonomy_authorization_id": _text(
                    validated.get("authorization_id")
                ),
                "autonomy_authorization_sha256": _text(
                    validated.get("authorization_sha256")
                ),
                "autonomy_grant_id": _text(validated.get("grant_id")),
                "autonomy_grant_sha256": _text(validated.get("grant_sha256")),
                "autonomy_plan_sha256": _text(validated.get("plan_sha256")),
                "repair_round": "1",
            },
            "required_environment": STAGE2_PROTECTED_ENVIRONMENT,
            "product_write_allowed": True,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
    elif kind == "RERUN_SAME_CANDIDATE":
        request = {
            "schema": NETWORK_REQUEST_SCHEMA,
            "kind": "RERUN_SOURCE_RUN",
            "repository": _text(repository),
            "workflow": None,
            "ref": None,
            "source_run_id": int(validated["source_run_id"]),
            "source_run_attempt": int(validated["source_run_attempt"]),
            "source_head_sha": _sha(
                validated.get("source_head_sha"), name="source_head_sha"
            ),
            "handoff_run_id": handoff_id,
            "handoff_run_attempt": handoff_attempt,
            "inputs": {},
            "required_environment": None,
            "product_write_allowed": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
    else:
        request = {
            "schema": NETWORK_REQUEST_SCHEMA,
            "kind": "NOOP",
            "repository": _text(repository),
            "workflow": None,
            "ref": None,
            "source_run_id": int(validated["source_run_id"]),
            "source_run_attempt": int(validated["source_run_attempt"]),
            "source_head_sha": _sha(
                validated.get("source_head_sha"), name="source_head_sha"
            ),
            "handoff_run_id": handoff_id,
            "handoff_run_attempt": handoff_attempt,
            "inputs": {},
            "required_environment": None,
            "product_write_allowed": False,
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
        }
    request["request_sha256"] = _digest(request)
    return request


def validate_network_request(
    request: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _validate_digest_bound_payload(
        request,
        schema=NETWORK_REQUEST_SCHEMA,
        digest_field="request_sha256",
        name="autonomy network request",
    )
    if any(
        payload.get(field) is not False
        for field in ("merge_allowed", "deploy_allowed", "production_closed")
    ):
        raise AutonomyDispatchError(
            "autonomy network request cannot cross merge/deploy/production boundary"
        )
    kind = _text(payload.get("kind"))
    if kind not in NETWORK_REQUEST_KINDS:
        raise AutonomyDispatchError(f"unsupported autonomy network request kind: {kind!r}")
    if int(payload.get("source_run_id") or 0) != int(plan.get("source_run_id") or 0):
        raise AutonomyDispatchError("network request source run does not match dispatch plan")
    if int(payload.get("source_run_attempt") or 0) != int(
        plan.get("source_run_attempt") or 0
    ):
        raise AutonomyDispatchError(
            "network request source attempt does not match dispatch plan"
        )
    if _text(payload.get("source_head_sha")) != _text(plan.get("source_head_sha")):
        raise AutonomyDispatchError("network request source head does not match dispatch plan")
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping):
        raise AutonomyDispatchError("network request inputs must be an object")
    if "remote_repair_approval" in inputs:
        raise AutonomyDispatchError(
            "autonomy network request must not synthesize legacy manual approval"
        )
    if kind == "DISPATCH_STAGE2":
        if (
            plan.get("kind") != "REQUEST_STAGE2_REPAIR"
            or payload.get("workflow") != STAGE2_WORKFLOW
            or payload.get("ref") != "main"
            or payload.get("required_environment") != STAGE2_PROTECTED_ENVIRONMENT
            or payload.get("product_write_allowed") is not True
        ):
            raise AutonomyDispatchError("Stage-2 network request drifted from repair plan")
        required_inputs = {
            "source_run_id",
            "source_run_attempt",
            "autonomy_handoff_run_id",
            "autonomy_handoff_run_attempt",
            "autonomy_authorization_id",
            "autonomy_authorization_sha256",
            "autonomy_grant_id",
            "autonomy_grant_sha256",
            "autonomy_plan_sha256",
            "repair_round",
        }
        if set(map(str, inputs.keys())) != required_inputs:
            raise AutonomyDispatchError("Stage-2 network request input contract drifted")
        if str(inputs.get("repair_round")) != "1":
            raise AutonomyDispatchError("autonomy Stage-2 handoff must begin at repair round 1")
    elif kind == "RERUN_SOURCE_RUN":
        if (
            plan.get("kind") != "RERUN_SAME_CANDIDATE"
            or payload.get("workflow") is not None
            or payload.get("ref") is not None
            or payload.get("required_environment") is not None
            or payload.get("product_write_allowed") is not False
            or dict(inputs)
        ):
            raise AutonomyDispatchError("same-candidate retry request drifted from retry plan")
    elif kind == "NOOP":
        if not _text(plan.get("kind")).startswith("NOOP_"):
            raise AutonomyDispatchError("NOOP network request requires a NOOP dispatch plan")
        if (
            payload.get("workflow") is not None
            or payload.get("ref") is not None
            or payload.get("required_environment") is not None
            or payload.get("product_write_allowed") is not False
            or dict(inputs)
        ):
            raise AutonomyDispatchError("NOOP network request cannot carry a target")
    return payload


def build_dispatch_receipt(
    *,
    plan: Mapping[str, Any],
    network_request: Mapping[str, Any],
    status: str,
    network_ref: str = "",
    error_code: str = "",
) -> dict[str, Any]:
    """Build a durable idempotency receipt after or before one network action."""

    plan_digest = _text(plan.get("plan_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", plan_digest):
        raise AutonomyDispatchError("dispatch receipt requires a digest-bound plan")
    request = validate_network_request(network_request, plan=plan)
    normalized_status = _text(status).upper()
    if normalized_status not in RECEIPT_STATUSES:
        raise AutonomyDispatchError("unsupported autonomy dispatch receipt status")
    ref = _text(network_ref)
    code = _text(error_code)
    if normalized_status == "DISPATCHED" and not ref:
        raise AutonomyDispatchError("DISPATCHED receipt requires a durable network reference")
    if normalized_status == "FAILED" and not code:
        raise AutonomyDispatchError("FAILED receipt requires an error code")
    if normalized_status == "PENDING" and (ref or code):
        raise AutonomyDispatchError("PENDING receipt cannot claim a result")

    receipt = {
        "schema": DISPATCH_RECEIPT_SCHEMA,
        "decision_id": _text(plan.get("decision_id")),
        "authorization_id": _text(plan.get("authorization_id")),
        "authorization_sha256": _text(plan.get("authorization_sha256")),
        "grant_id": _text(plan.get("grant_id")),
        "grant_sha256": _text(plan.get("grant_sha256")),
        "plan_sha256": plan_digest,
        "request_sha256": _text(request.get("request_sha256")),
        "source_run_id": int(plan.get("source_run_id") or 0),
        "source_run_attempt": int(plan.get("source_run_attempt") or 0),
        "source_head_sha": _text(plan.get("source_head_sha")),
        "status": normalized_status,
        "network_kind": _text(request.get("kind")),
        "network_ref": ref,
        "error_code": code,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    receipt["receipt_sha256"] = _digest(receipt)
    return receipt


def validate_dispatch_receipt(
    receipt: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    network_request: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _validate_digest_bound_payload(
        receipt,
        schema=DISPATCH_RECEIPT_SCHEMA,
        digest_field="receipt_sha256",
        name="autonomy dispatch receipt",
    )
    request = validate_network_request(network_request, plan=plan)
    expected = {
        "decision_id": _text(plan.get("decision_id")),
        "authorization_id": _text(plan.get("authorization_id")),
        "authorization_sha256": _text(plan.get("authorization_sha256")),
        "grant_id": _text(plan.get("grant_id")),
        "grant_sha256": _text(plan.get("grant_sha256")),
        "plan_sha256": _text(plan.get("plan_sha256")),
        "request_sha256": _text(request.get("request_sha256")),
        "source_run_id": int(plan.get("source_run_id") or 0),
        "source_run_attempt": int(plan.get("source_run_attempt") or 0),
        "source_head_sha": _text(plan.get("source_head_sha")),
        "network_kind": _text(request.get("kind")),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise AutonomyDispatchError(f"autonomy dispatch receipt binding mismatch: {key}")
    if _text(payload.get("status")).upper() not in RECEIPT_STATUSES:
        raise AutonomyDispatchError("autonomy dispatch receipt status is unsupported")
    if any(
        payload.get(field) is not False
        for field in ("merge_allowed", "deploy_allowed", "production_closed")
    ):
        raise AutonomyDispatchError(
            "autonomy dispatch receipt cannot cross merge/deploy/production boundary"
        )
    return payload
