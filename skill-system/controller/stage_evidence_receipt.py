"""Immutable evidence receipts for governed stage execution.

The receipt is intentionally standalone.  It records the exact identities that
produced a stage result, but it does not write or infer TaskRun, active-change,
baseline, or acceptance state.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


STAGE_EVIDENCE_RECEIPT_SCHEMA = "stage-evidence-receipt@1"
RECEIPT_SCHEMA = STAGE_EVIDENCE_RECEIPT_SCHEMA
ALLOWED_RESULTS = frozenset({"PASS", "FAIL", "BLOCKED_BY_ENVIRONMENT"})

_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "stage_id",
        "accepted_state_id",
        "product_source_ref",
        "protected_snapshot_digest",
        "control_plane_ref",
        "execution_repo_ref",
        "workflow_run_attempt",
        "artifact",
        "result",
        "producer",
        "policy",
        "receipt_digest",
    }
)
_RECEIPT_BODY_FIELDS = _RECEIPT_FIELDS - {"receipt_digest"}
_WORKFLOW_FIELDS = frozenset({"run_id", "attempt"})
_ARTIFACT_FIELDS = frozenset({"id", "digest"})
_COMMIT_REF = re.compile(r"git-commit-sha1:[0-9a-f]{40}\Z")
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class StageEvidenceReceiptError(ValueError):
    """Raised when a stage evidence receipt is absent, malformed, or stale."""


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the exact compact UTF-8 JSON representation used for hashing."""

    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise StageEvidenceReceiptError("receipt_payload_not_canonicalizable") from exc
    if encoded.endswith(b"\n"):
        raise StageEvidenceReceiptError("receipt_canonical_json_has_trailing_newline")
    return encoded


def _sha256_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "receipt_digest"}


def receipt_digest(payload: Mapping[str, Any]) -> str:
    """Compute the receipt digest over every receipt field except itself."""

    return _sha256_digest(canonical_json_bytes(_body(payload)))


def _text(value: object, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field}_invalid")


def _exact_fields(
    value: object,
    expected: frozenset[str],
    field: str,
    errors: list[str],
) -> bool:
    if not isinstance(value, Mapping):
        errors.append(f"{field}_not_object")
        return False
    actual = set(value)
    errors.extend(f"{field}_missing:{name}" for name in sorted(expected - actual))
    errors.extend(f"{field}_unknown:{name}" for name in sorted(actual - expected))
    return actual == set(expected)


def _validate_body(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    actual = set(payload)
    errors.extend(f"receipt_missing:{name}" for name in sorted(_RECEIPT_BODY_FIELDS - actual))
    errors.extend(f"receipt_unknown:{name}" for name in sorted(actual - _RECEIPT_BODY_FIELDS - {"receipt_digest"}))

    if payload.get("schema") != STAGE_EVIDENCE_RECEIPT_SCHEMA:
        errors.append("schema_invalid")

    for field in (
        "stage_id",
        "accepted_state_id",
        "control_plane_ref",
        "execution_repo_ref",
        "producer",
        "policy",
    ):
        _text(payload.get(field), field, errors)

    product_source_ref = payload.get("product_source_ref")
    if not isinstance(product_source_ref, str) or _COMMIT_REF.fullmatch(product_source_ref) is None:
        errors.append("product_source_ref_invalid")

    protected_snapshot_digest = payload.get("protected_snapshot_digest")
    if not isinstance(protected_snapshot_digest, str) or _SHA256_DIGEST.fullmatch(
        protected_snapshot_digest
    ) is None:
        errors.append("protected_snapshot_digest_invalid")

    result = payload.get("result")
    if not isinstance(result, str) or result not in ALLOWED_RESULTS:
        errors.append("result_invalid")

    workflow = payload.get("workflow_run_attempt")
    workflow_valid = _exact_fields(workflow, _WORKFLOW_FIELDS, "workflow_run_attempt", errors)
    if workflow_valid:
        for field in ("run_id", "attempt"):
            value = workflow[field]  # type: ignore[index]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"workflow_run_attempt_{field}_invalid")

    artifact = payload.get("artifact")
    artifact_valid = _exact_fields(artifact, _ARTIFACT_FIELDS, "artifact", errors)
    if artifact_valid:
        artifact_id = artifact["id"]  # type: ignore[index]
        artifact_digest = artifact["digest"]  # type: ignore[index]
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            errors.append("artifact_id_invalid")
        if not isinstance(artifact_digest, str) or _SHA256_DIGEST.fullmatch(artifact_digest) is None:
            errors.append("artifact_digest_invalid")

    return list(dict.fromkeys(errors))


def validate_stage_evidence_receipt(payload: object) -> dict[str, Any]:
    """Validate and return a detached receipt copy.

    Validation is fail-closed: top-level and nested objects are exact, the
    digest is recomputed from canonical JSON, and no caller-owned mapping is
    returned or mutated.
    """

    if not isinstance(payload, Mapping):
        raise StageEvidenceReceiptError("receipt_not_object")

    actual = set(payload)
    errors: list[str] = []
    errors.extend(f"receipt_missing:{name}" for name in sorted(_RECEIPT_FIELDS - actual))
    errors.extend(f"receipt_unknown:{name}" for name in sorted(actual - _RECEIPT_FIELDS))
    errors.extend(_validate_body(payload))

    supplied_digest = payload.get("receipt_digest")
    if not isinstance(supplied_digest, str) or _SHA256_DIGEST.fullmatch(supplied_digest) is None:
        errors.append("receipt_digest_invalid")
    else:
        try:
            expected_digest = receipt_digest(payload)
        except StageEvidenceReceiptError:
            errors.append("receipt_digest_uncomputable")
        else:
            if supplied_digest != expected_digest:
                errors.append("receipt_digest_mismatch")

    errors = list(dict.fromkeys(errors))
    if errors:
        raise StageEvidenceReceiptError(";".join(errors))
    return dict(payload)


def build_stage_evidence_receipt(
    *,
    stage_id: str,
    accepted_state_id: str,
    product_source_ref: str,
    protected_snapshot_digest: str,
    control_plane_ref: str,
    execution_repo_ref: str,
    workflow_run_attempt: Mapping[str, Any],
    artifact: Mapping[str, Any],
    result: str,
    producer: str,
    policy: str,
) -> dict[str, Any]:
    """Build a validated immutable stage evidence receipt."""

    payload: dict[str, Any] = {
        "schema": STAGE_EVIDENCE_RECEIPT_SCHEMA,
        "stage_id": stage_id,
        "accepted_state_id": accepted_state_id,
        "product_source_ref": product_source_ref,
        "protected_snapshot_digest": protected_snapshot_digest,
        "control_plane_ref": control_plane_ref,
        "execution_repo_ref": execution_repo_ref,
        "workflow_run_attempt": dict(workflow_run_attempt),
        "artifact": dict(artifact),
        "result": result,
        "producer": producer,
        "policy": policy,
    }
    errors = _validate_body(payload)
    if errors:
        raise StageEvidenceReceiptError(";".join(errors))
    payload["receipt_digest"] = receipt_digest(payload)
    return validate_stage_evidence_receipt(payload)


def write_stage_evidence_receipt(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """Validate and write canonical receipt JSON without a trailing newline."""

    receipt = validate_stage_evidence_receipt(payload)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(receipt))
    return target


def load_stage_evidence_receipt(path: str | Path) -> dict[str, Any]:
    """Load and validate a receipt from JSON bytes."""

    try:
        payload = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageEvidenceReceiptError("receipt_json_invalid") from exc
    return validate_stage_evidence_receipt(payload)


# Short aliases keep the module convenient for controller call sites without
# introducing another receipt implementation or a second registry.
build_receipt = build_stage_evidence_receipt
validate_receipt = validate_stage_evidence_receipt


__all__ = [
    "ALLOWED_RESULTS",
    "RECEIPT_SCHEMA",
    "STAGE_EVIDENCE_RECEIPT_SCHEMA",
    "StageEvidenceReceiptError",
    "build_receipt",
    "build_stage_evidence_receipt",
    "canonical_json_bytes",
    "load_stage_evidence_receipt",
    "receipt_digest",
    "validate_receipt",
    "validate_stage_evidence_receipt",
    "write_stage_evidence_receipt",
]
