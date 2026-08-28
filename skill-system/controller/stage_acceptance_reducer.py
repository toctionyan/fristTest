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
from stage2b1_protected_approval import VerifiedProtectedApproval
from stage2b1_provenance import VerifiedArtifactProvenance
from stage2b1_external_issuer import (
    ExternalIssuerProof,
    validate_external_issuer_proof,
)


STAGE_ACCEPTANCE_DECISION_SCHEMA = "stage-acceptance-decision@1"
TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA = "stage-acceptance-decision@2"
ACCEPTABLE_PREVIEW = "ACCEPTABLE_PREVIEW"
BLOCKED = "BLOCKED"
_COMMON_FIELDS = (
    "stage_id",
    "accepted_state_id",
    "product_source_ref",
    "protected_snapshot_digest",
    "control_plane_ref",
    "execution_repo_ref",
)
_EXPECTED_BINDING_FIELDS = frozenset({"artifact", "workflow_run_attempt", "policy"})
_TRUSTED_PROOF_PREFIXES = (
    "provenance:",
    "external-issuer:",
    "protected-approval:",
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
    missing = _EXPECTED_BINDING_FIELDS - set(expected)
    unknown = set(expected) - _EXPECTED_BINDING_FIELDS
    reasons.extend(
        f"expected_binding_missing:{receipt_id}:{field}"
        for field in sorted(missing)
    )
    reasons.extend(
        f"expected_binding_unknown:{receipt_id}:{field}"
        for field in sorted(unknown)
    )
    for field in sorted(set(expected) & _EXPECTED_BINDING_FIELDS):
        expected_value = expected[field]
        actual_value = _binding_value(receipt, field)
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
    expected_receipt_bindings: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Reduce explicitly supplied receipts into a read-only acceptance preview.

    Receipt identity is the P3-bound ``artifact.id``.  Every required identity
    must be supplied by the caller.  Each required receipt must also have an
    explicit binding for its artifact, workflow run/attempt, and policy.
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
    }
    input_digest = _input_digest(
        raw_receipts,
        tuple(required_receipt_ids),
        expected_receipt_bindings,
        common,
    )

    reasons: list[str] = []
    reasons.extend(
        f"expected_common_binding_missing:{field}"
        for field, value in common.items()
        if value is None
    )
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

    if not isinstance(expected_receipt_bindings, Mapping):
        reasons.append("expected_receipt_bindings_required")
        expected_receipt_bindings = {}
    else:
        for key in expected_receipt_bindings:
            if key not in required_ids:
                reasons.append(f"expected_receipt_id_unrequired:{key}")
        reasons.extend(
            f"expected_receipt_binding_missing:{value}"
            for value in sorted(
                set(required_ids) - set(expected_receipt_bindings),
                key=lambda item: item.encode("utf-8"),
            )
        )

    for receipt_id in sorted(actual_by_id, key=lambda item: item.encode("utf-8")):
        receipt = actual_by_id[receipt_id]
        for field, expected_value in common.items():
            if expected_value is not None and receipt.get(field) != expected_value:
                reasons.append(f"receipt_binding_mismatch:{field}:{receipt_id}")
        if receipt.get("result") != "PASS":
            reasons.append(f"receipt_result_not_pass:{receipt_id}:{receipt.get('result')}")

        expected = expected_receipt_bindings.get(receipt_id)
        if not isinstance(expected, Mapping):
            reasons.append(f"expected_receipt_binding_invalid:{receipt_id}")
        else:
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


def _trusted_decision(
    *,
    input_digest: str,
    status: str,
    reasons: Iterable[str],
    receipt_refs: Iterable[str],
    proof_refs: Iterable[str],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA,
        "input_digest": input_digest,
        "status": status,
        "reasons": sorted(set(reasons), key=lambda value: value.encode("utf-8")),
        "receipt_refs": _sorted_texts(receipt_refs),
        "proof_refs": _sorted_texts(proof_refs),
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


def reduce_trusted_stage_acceptance(
    receipts: Sequence[object],
    *,
    required_receipt_ids: Sequence[object],
    stage_id: str | None = None,
    accepted_state_id: str | None = None,
    product_source_ref: str | None = None,
    protected_snapshot_digest: str | None = None,
    control_plane_ref: str | None = None,
    execution_repo_ref: str | None = None,
    expected_receipt_bindings: Mapping[str, Mapping[str, Any]] | None,
    verified_provenance: Mapping[str, VerifiedArtifactProvenance] | None = None,
    verified_external_issuers: Mapping[str, ExternalIssuerProof] | None = None,
    expected_external_issuer_bindings: Mapping[str, Mapping[str, str]] | None = None,
    verified_protected_approval: VerifiedProtectedApproval | None = None,
    expected_protected_approval: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reduce receipts only after fixed provenance and approval verification.

    ``reduce_stage_acceptance`` remains the raw, read-only receipt preview used
    by earlier stages.  This entry point is the only reducer result eligible
    for later acceptance wiring: it accepts typed verifier results, never a
    caller-supplied validator or a boolean trust flag.
    """

    if isinstance(receipts, (str, bytes, Mapping)):
        raw_receipts: tuple[object, ...] = (receipts,)
    else:
        try:
            raw_receipts = tuple(receipts)
        except TypeError:
            raw_receipts = (receipts,)
    required_ids_input = tuple(required_receipt_ids)
    base = reduce_stage_acceptance(
        raw_receipts,
        required_receipt_ids=required_ids_input,
        stage_id=stage_id,
        accepted_state_id=accepted_state_id,
        product_source_ref=product_source_ref,
        protected_snapshot_digest=protected_snapshot_digest,
        control_plane_ref=control_plane_ref,
        execution_repo_ref=execution_repo_ref,
        expected_receipt_bindings=expected_receipt_bindings,
    )
    reasons = list(base["reasons"])
    proof_refs: list[str] = []
    required_ids = [value for value in required_ids_input if isinstance(value, str) and value.strip()]
    receipt_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in raw_receipts:
        identity = _receipt_id(raw)
        if identity is None or identity in receipt_by_id:
            continue
        try:
            receipt_by_id[identity] = validate_stage_evidence_receipt(raw)
        except StageEvidenceReceiptError:
            continue

    if not isinstance(verified_provenance, Mapping):
        reasons.append("trusted_provenance_required")
        verified_provenance = {}
    else:
        unexpected = set(verified_provenance) - set(required_ids)
        reasons.extend(
            f"trusted_provenance_unexpected:{value}"
            for value in sorted(unexpected, key=lambda item: item.encode("utf-8"))
        )

    if not isinstance(verified_external_issuers, Mapping):
        reasons.append("external_issuer_proof_required")
        verified_external_issuers = {}
    else:
        unexpected = set(verified_external_issuers) - set(required_ids)
        reasons.extend(
            f"external_issuer_unexpected:{value}"
            for value in sorted(unexpected, key=lambda item: item.encode("utf-8"))
        )

    for receipt_id in sorted(set(required_ids), key=lambda item: item.encode("utf-8")):
        proof = verified_provenance.get(receipt_id)
        receipt = receipt_by_id.get(receipt_id)
        if not isinstance(proof, VerifiedArtifactProvenance):
            reasons.append(f"trusted_provenance_missing:{receipt_id}")
            continue
        if proof.receipt_id != receipt_id:
            reasons.append(f"trusted_provenance_receipt_mismatch:{receipt_id}")
        if receipt is None:
            reasons.append(f"trusted_provenance_receipt_unavailable:{receipt_id}")
        else:
            artifact = receipt.get("artifact")
            if not isinstance(artifact, Mapping):
                reasons.append(f"trusted_provenance_artifact_missing:{receipt_id}")
            else:
                if proof.artifact_id != artifact.get("id"):
                    reasons.append(f"trusted_provenance_artifact_id_mismatch:{receipt_id}")
                if proof.artifact_digest != artifact.get("digest"):
                    reasons.append(f"trusted_provenance_artifact_digest_mismatch:{receipt_id}")
        expected = expected_receipt_bindings.get(receipt_id) if isinstance(expected_receipt_bindings, Mapping) else None
        if isinstance(expected, Mapping):
            expected_artifact = expected.get("artifact")
            expected_workflow = expected.get("workflow_run_attempt")
            if isinstance(expected_artifact, Mapping) and proof.artifact_id != expected_artifact.get("id"):
                reasons.append(f"trusted_provenance_expected_artifact_mismatch:{receipt_id}")
            if isinstance(expected_workflow, Mapping):
                if proof.run_id != expected_workflow.get("run_id") or proof.run_attempt != expected_workflow.get("attempt"):
                    reasons.append(f"trusted_provenance_expected_run_mismatch:{receipt_id}")
        proof_refs.append(proof.proof_ref)

        external_proof = verified_external_issuers.get(receipt_id)
        if not isinstance(external_proof, ExternalIssuerProof):
            reasons.append(f"external_issuer_missing:{receipt_id}")
        else:
            try:
                validate_external_issuer_proof(external_proof)
            except ValueError:
                reasons.append(f"external_issuer_invalid:{receipt_id}")
            else:
                if external_proof.subject_digest != proof.artifact_digest:
                    reasons.append(f"external_issuer_subject_mismatch:{receipt_id}")
                expected_issuer = (
                    expected_external_issuer_bindings.get(receipt_id)
                    if isinstance(expected_external_issuer_bindings, Mapping)
                    else None
                )
                if not isinstance(expected_issuer, Mapping):
                    reasons.append(f"external_issuer_binding_missing:{receipt_id}")
                else:
                    for field in (
                        "repository",
                        "signer_workflow",
                        "predicate_type",
                        "subject_digest",
                        "source_digest",
                        "source_ref",
                    ):
                        if getattr(external_proof, field, None) != expected_issuer.get(field):
                            reasons.append(f"external_issuer_binding_mismatch:{receipt_id}:{field}")
                proof_refs.append(external_proof.proof_ref)

    if not isinstance(verified_protected_approval, VerifiedProtectedApproval):
        reasons.append("trusted_protected_approval_required")
    else:
        approval = verified_protected_approval
        for field, expected_value in {
            "stage_id": stage_id,
            "accepted_state_id": accepted_state_id,
            "product_source_ref": product_source_ref,
            "protected_snapshot_digest": protected_snapshot_digest,
            "control_plane_ref": control_plane_ref,
            "execution_repo_ref": execution_repo_ref,
        }.items():
            if expected_value is None or getattr(approval, field) != expected_value:
                reasons.append(f"trusted_protected_approval_binding_mismatch:{field}")
        if isinstance(expected_protected_approval, Mapping):
            for field, expected_value in expected_protected_approval.items():
                if not hasattr(approval, field) or getattr(approval, field) != expected_value:
                    reasons.append(f"trusted_protected_approval_expected_mismatch:{field}")
        proof_refs.append(approval.proof_ref)

    trusted_input = {
        "base_input_digest": base["input_digest"],
        "provenance_refs": sorted(set(proof_refs), key=lambda value: value.encode("utf-8")),
    }
    trusted_input_digest = _digest(
        json.dumps(
            trusted_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    return _trusted_decision(
        input_digest=trusted_input_digest,
        status=ACCEPTABLE_PREVIEW if not reasons else BLOCKED,
        reasons=reasons,
        receipt_refs=base["receipt_refs"],
        proof_refs=proof_refs,
    )


def validate_trusted_stage_acceptance_decision(payload: object) -> dict[str, Any]:
    """Validate a decision produced by ``reduce_trusted_stage_acceptance``."""

    if not isinstance(payload, Mapping):
        raise ValueError("trusted_stage_acceptance_decision_not_object")
    expected_fields = {
        "schema",
        "input_digest",
        "status",
        "reasons",
        "receipt_refs",
        "proof_refs",
        "decision_id",
    }
    if set(payload) != expected_fields:
        raise ValueError("trusted_stage_acceptance_decision_fields_invalid")
    if payload.get("schema") != TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA:
        raise ValueError("trusted_stage_acceptance_decision_schema_invalid")
    if payload.get("status") not in {ACCEPTABLE_PREVIEW, BLOCKED}:
        raise ValueError("trusted_stage_acceptance_decision_status_invalid")
    for field in ("input_digest",):
        value = payload.get(field)
        if (
            not isinstance(value, str)
            or not value.startswith("sha256:")
            or len(value) != len("sha256:") + 64
        ):
            raise ValueError(f"trusted_stage_acceptance_decision_{field}_invalid")
    for field in ("reasons", "receipt_refs", "proof_refs"):
        value = payload.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"trusted_stage_acceptance_decision_{field}_invalid")
        if len(value) != len(set(value)):
            raise ValueError(f"trusted_stage_acceptance_decision_{field}_duplicate")
    proof_refs = payload["proof_refs"]
    if any(not item.startswith(_TRUSTED_PROOF_PREFIXES) for item in proof_refs):
        raise ValueError("trusted_stage_acceptance_decision_proof_ref_invalid")
    if payload["status"] == ACCEPTABLE_PREVIEW:
        missing = [
            prefix
            for prefix in _TRUSTED_PROOF_PREFIXES
            if not any(item.startswith(prefix) for item in proof_refs)
        ]
        if missing:
            raise ValueError(
                "trusted_stage_acceptance_decision_proof_ref_incomplete:"
                + ",".join(missing)
            )
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
        raise ValueError("trusted_stage_acceptance_decision_id_mismatch")
    return dict(payload)


reduce = reduce_stage_acceptance
reduce_stage_acceptance_preview = reduce_stage_acceptance


__all__ = [
    "ACCEPTABLE_PREVIEW",
    "BLOCKED",
    "STAGE_ACCEPTANCE_DECISION_SCHEMA",
    "TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA",
    "reduce",
    "reduce_stage_acceptance",
    "reduce_stage_acceptance_preview",
    "reduce_trusted_stage_acceptance",
    "validate_stage_acceptance_decision",
    "validate_trusted_stage_acceptance_decision",
]
