"""Read-only, deterministic stage acceptance reduction.

The reducer consumes only the receipt collection and identities supplied by its
caller.  It has no repository, filesystem, network, workflow, or governance
state dependency, so a preview cannot silently become an acceptance write.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from stage_evidence_receipt import StageEvidenceReceiptError, validate_stage_evidence_receipt


STAGE_ACCEPTANCE_DECISION_SCHEMA = "stage-acceptance-decision@1"
ACCEPTABLE_PREVIEW = "ACCEPTABLE_PREVIEW"
BLOCKED = "BLOCKED"
_COMMON_FIELDS = (
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
    "policy",
)
_EXPECTED_BINDING_FIELDS = frozenset(
    {"artifact", "workflow_run_attempt", "policy", "artifact_digest"}
)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _stable_json(value: Any) -> bytes:
    """Canonicalize valid input, retaining a deterministic marker if malformed."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (StageEvidenceReceiptError, TypeError, ValueError, UnicodeEncodeError):
        return json.dumps(
            {"invalid_input_type": type(value).__name__},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sorted_texts(values: Iterable[str]) -> list[str]:
    return sorted(set(values), key=lambda value: value.encode("utf-8"))


def _receipt_id(receipt: object) -> str | None:
    if not isinstance(receipt, Mapping):
        return None
    artifact = receipt.get("artifact")
    if not isinstance(artifact, Mapping):
        return None
    value = artifact.get("id")
    return value if isinstance(value, str) and value.strip() else None


def _canonical_collection(receipts: Sequence[object]) -> list[bytes]:
    return sorted((_stable_json(item) for item in receipts))


def _input_digest(
    receipts: Sequence[object],
    required_receipt_ids: Sequence[object],
    expected: Mapping[str, Mapping[str, Any]] | None,
    common: Mapping[str, str | None],
) -> str:
    expected_payload = None
    if expected is not None:
        expected_payload = {
            str(key): value for key, value in sorted(expected.items(), key=lambda item: str(item[0]).encode("utf-8"))
        }
    payload = {
        "required_receipt_ids": sorted(
            (_stable_json(value).decode("utf-8") for value in required_receipt_ids),
            key=lambda value: value.encode("utf-8"),
        ),
        "receipts": [item.decode("utf-8", errors="replace") for item in _canonical_collection(receipts)],
        "expected_receipt_bindings": expected_payload,
        "expected_common_binding": dict(common),
    }
    return _digest(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _decision(
    *,
    input_digest: str,
    status: str,
    reasons: Iterable[str],
    receipt_refs: Iterable[str],
) -> dict[str, Any]:
    normalized_reasons = sorted(set(reasons), key=lambda value: value.encode("utf-8"))
    normalized_refs = _sorted_texts(receipt_refs)
    body: dict[str, Any] = {
        "schema": STAGE_ACCEPTANCE_DECISION_SCHEMA,
        "input_digest": input_digest,
        "status": status,
        "reasons": normalized_reasons,
        "receipt_refs": normalized_refs,
    }
    body["decision_id"] = _digest(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    return body


def _binding_value(receipt: Mapping[str, Any], field: str) -> Any:
    if field == "artifact_digest":
        artifact = receipt.get("artifact")
        return artifact.get("digest") if isinstance(artifact, Mapping) else None
    return receipt.get(field)


def _check_expected_binding(
    receipt: Mapping[str, Any],
    receipt_id: str,
    expected: Mapping[str, Any],
    reasons: list[str],
) -> None:
    unknown = set(expected) - _EXPECTED_BINDING_FIELDS
    reasons.extend(
        f"expected_binding_unknown:{receipt_id}:{field}"
        for field in sorted(unknown)
    )
    for field in sorted(set(expected) & _EXPECTED_BINDING_FIELDS):
        expected_value = expected[field]
        actual_value = _binding_value(receipt, field)
        if field == "artifact_digest":
            actual_value = _binding_value(receipt, "artifact_digest")
        if _stable_json(actual_value) != _stable_json(expected_value):
            reasons.append(f"receipt_binding_mismatch:{field}:{receipt_id}")


def reduce_stage_acceptance(
    receipts: Sequence[object],
    *,
    required_receipt_ids: Sequence[object],
    stage_id: str | None = None,
    accepted_state_id: str | None = None,
    product_source_ref: str | None = None,
    protected_snapshot_digest: str | None = None,
    control_plane_ref: str | None = None,
    execution_repo_ref: str | None = None,
    policy: str | None = None,
    expected_receipt_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reduce explicitly supplied receipts into a read-only acceptance preview.

    Receipt identity is the P3-bound ``artifact.id``.  Every required identity
    must be supplied by the caller.  ``expected_receipt_bindings`` can pin the
    per-receipt artifact digest, workflow run/attempt, and policy when those
    values are part of the stage's explicit acceptance contract.
    """

    raw_receipts: Sequence[object]
    if isinstance(receipts, (str, bytes, Mapping)):
        raw_receipts = (receipts,)
    else:
        try:
            raw_receipts = tuple(receipts)
        except TypeError:
            raw_receipts = (receipts,)

    common: dict[str, str | None] = {
        "stage_id": stage_id,
        "accepted_state_id": accepted_state_id,
        "product_source_ref": product_source_ref,
        "protected_snapshot_digest": protected_snapshot_digest,
        "control_plane_ref": control_plane_ref,
        "execution_repo_ref": execution_repo_ref,
        "policy": policy,
    }
    input_digest = _input_digest(
        raw_receipts,
        tuple(required_receipt_ids),
        expected_receipt_bindings,
        common,
    )

    reasons: list[str] = []
    required_ids: list[str] = []
    for index, value in enumerate(required_receipt_ids):
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"required_receipt_id_invalid:{index}")
            continue
        required_ids.append(value)
    required_counts: dict[str, int] = {}
    for value in required_ids:
        required_counts[value] = required_counts.get(value, 0) + 1
    reasons.extend(
        f"required_receipt_id_duplicate:{value}"
        for value, count in required_counts.items()
        if count > 1
    )

    actual_by_id: dict[str, Mapping[str, Any]] = {}
    actual_raw_by_id: dict[str, bytes] = {}
    actual_refs: list[str] = []
    for index, raw in enumerate(raw_receipts):
        identity = _receipt_id(raw)
        if identity is None:
            identity = f"index-{index}"
        actual_refs.append(identity)
        try:
            validated = validate_stage_evidence_receipt(raw)
        except StageEvidenceReceiptError as exc:
            reasons.append(f"receipt_invalid:{identity}:{exc}")
            validated = None
        raw_digest = _stable_json(raw)
        if identity in actual_by_id:
            reasons.append(f"receipt_id_duplicate:{identity}")
            if actual_raw_by_id[identity] != raw_digest:
                reasons.append(f"receipt_id_duplicate_content_mismatch:{identity}")
            continue
        actual_raw_by_id[identity] = raw_digest
        if validated is None:
            continue
        actual_by_id[identity] = validated
        if identity not in required_ids:
            reasons.append(f"receipt_id_unexpected:{identity}")

    missing = set(required_ids) - set(actual_by_id)
    reasons.extend(
        f"required_receipt_missing:{value}"
        for value in sorted(missing, key=lambda item: item.encode("utf-8"))
    )

    if expected_receipt_bindings is not None:
        for key in expected_receipt_bindings:
            if key not in required_ids:
                reasons.append(f"expected_receipt_id_unrequired:{key}")

    for receipt_id in sorted(actual_by_id, key=lambda item: item.encode("utf-8")):
        receipt = actual_by_id[receipt_id]
        for field, expected_value in common.items():
            if expected_value is not None and receipt.get(field) != expected_value:
                reasons.append(f"receipt_binding_mismatch:{field}:{receipt_id}")
        if receipt.get("result") != "PASS":
            reasons.append(f"receipt_result_not_pass:{receipt_id}:{receipt.get('result')}")

        if expected_receipt_bindings is not None:
            expected = expected_receipt_bindings.get(receipt_id)
            if expected is not None:
                _check_expected_binding(receipt, receipt_id, expected, reasons)

    # With no explicit common binding, all receipts still must agree on it.
    for field in _COMMON_FIELDS:
        values = {
            receipt.get(field)
            for receipt in actual_by_id.values()
        }
        if len(values) > 1:
            canonical_values = sorted(
                values,
                key=lambda value: _stable_json(value),
            )
            expected_value = canonical_values[0]
            reasons.extend(
                f"receipt_binding_mismatch:{field}:{receipt_id}"
                for receipt_id, receipt in actual_by_id.items()
                if receipt.get(field) != expected_value
            )

    required_set = set(required_ids)
    complete = (
        bool(required_ids)
        and not (set(required_ids) - required_set)
        and not (set(required_ids) - set(actual_by_id))
        and set(actual_by_id) == required_set
    )
    if not complete:
        reasons.append("required_receipt_set_incomplete")

    status = ACCEPTABLE_PREVIEW if not reasons else BLOCKED
    return _decision(
        input_digest=input_digest,
        status=status,
        reasons=reasons,
        receipt_refs=actual_refs,
    )


def validate_stage_acceptance_decision(payload: object) -> dict[str, Any]:
    """Validate the shape and self-consistency of a reducer decision."""

    if not isinstance(payload, Mapping):
        raise ValueError("stage_acceptance_decision_not_object")
    expected_fields = {
        "schema",
        "input_digest",
        "status",
        "reasons",
        "receipt_refs",
        "decision_id",
    }
    if set(payload) != expected_fields:
        raise ValueError("stage_acceptance_decision_fields_invalid")
    if payload.get("schema") != STAGE_ACCEPTANCE_DECISION_SCHEMA:
        raise ValueError("stage_acceptance_decision_schema_invalid")
    if payload.get("status") not in {ACCEPTABLE_PREVIEW, BLOCKED}:
        raise ValueError("stage_acceptance_decision_status_invalid")
    if not isinstance(payload.get("input_digest"), str) or not payload["input_digest"].startswith("sha256:"):
        raise ValueError("stage_acceptance_decision_input_digest_invalid")
    if not isinstance(payload.get("reasons"), list) or any(not isinstance(item, str) for item in payload["reasons"]):
        raise ValueError("stage_acceptance_decision_reasons_invalid")
    if not isinstance(payload.get("receipt_refs"), list) or any(not isinstance(item, str) for item in payload["receipt_refs"]):
        raise ValueError("stage_acceptance_decision_receipt_refs_invalid")
    expected = dict(payload)
    decision_id = expected.pop("decision_id")
    if decision_id != _digest(
        json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ):
        raise ValueError("stage_acceptance_decision_id_mismatch")
    return dict(payload)


reduce = reduce_stage_acceptance
reduce_stage_acceptance_preview = reduce_stage_acceptance


__all__ = [
    "ACCEPTABLE_PREVIEW",
    "BLOCKED",
    "STAGE_ACCEPTANCE_DECISION_SCHEMA",
    "reduce",
    "reduce_stage_acceptance",
    "reduce_stage_acceptance_preview",
    "validate_stage_acceptance_decision",
]
