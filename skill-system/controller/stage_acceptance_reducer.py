"""Read-only, deterministic stage acceptance reduction.

The reducer consumes only the receipt collection and identities supplied by its
caller.  It has no repository, filesystem, network, workflow, or governance
state dependency, so a preview cannot silently become an acceptance write.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from stage_evidence_receipt import StageEvidenceReceiptError, validate_stage_evidence_receipt
from stage2b1_protected_human_gate import (
    normalize_approval_evidence_bindings,
    Stage2B1ProtectedApprovalProof,
    Stage2B1ProtectedHumanGateError,
    reverify_stage2b1_protected_approval,
)
from stage2b1_provenance import (
    VerifiedArtifactProvenance,
    validate_verified_artifact_provenance,
    verify_artifact_provenance,
)
from stage2b1_external_issuer import (
    ExternalIssuerProof,
    STAGE2B1_PREDICATE_TYPE,
    STAGE2B1_REPOSITORY,
    STAGE2B1_SIGNER_WORKFLOW,
    STAGE2B1_SOURCE_REF,
    P48SourceArtifactProof,
    validate_external_issuer_proof,
    verify_p4_8_payload_attestation,
    verify_p4_8_source_artifact,
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
_EXPECTED_APPROVAL_FIELDS = frozenset(
    {
        *_COMMON_FIELDS,
        "gate_id",
        "gate_sha256",
        "task_id",
        "repository",
        "workflow_id",
        "workflow_path",
        "workflow_ref",
        "ref",
        "head_sha",
        "run_id",
        "run_attempt",
        "environment",
        "environment_id",
        "reviewer_login",
        "reviewer_id",
        "run_actor_login",
        "run_actor_id",
        "status",
        "approval_sha256",
        "evidence_bindings",
    }
)
_TRUSTED_PROOF_PREFIXES = (
    "provenance:",
    "external-issuer:",
    "protected-approval:",
)
_TRUSTED_DECISION_ISSUER = object()
_P3_RECEIPT_POLICY = "stage2b1-p3-evidence-receipt@1"
_P48_WORKFLOW_PATH = STAGE2B1_SIGNER_WORKFLOW.removeprefix(
    STAGE2B1_REPOSITORY + "/"
)


def _commit_sha(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("git-commit-sha1:"):
        return None
    sha = value.removeprefix("git-commit-sha1:")
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        return None
    return sha


class TrustedStageAcceptanceDecision(Mapping[str, Any]):
    """Immutable canonical decision envelope.

    This object is not treated as a security boundary.  Its bytes are frozen
    so callers cannot mutate a decision after reduction; write-capable
    adapters must still call ``reverify_trusted_stage_acceptance_decision``
    with the original evidence and fixed verifiers.
    """

    __slots__ = ("_payload_json",)

    def __init__(self, *, _issuer: object, payload: Mapping[str, Any]) -> None:
        if _issuer is not _TRUSTED_DECISION_ISSUER:
            raise TypeError("trusted stage acceptance decisions are reducer-issued")
        self._payload_json = _canonical_json(payload)

    def __getitem__(self, key: str) -> Any:
        payload = json.loads(self._payload_json.decode("utf-8"))
        return deepcopy(payload[key])

    def __iter__(self):
        return iter(json.loads(self._payload_json.decode("utf-8")))

    def __len__(self) -> int:
        return len(json.loads(self._payload_json.decode("utf-8")))

    def __repr__(self) -> str:
        return f"TrustedStageAcceptanceDecision({self.as_dict()!r})"

    def __deepcopy__(self, memo: dict[int, Any]) -> "TrustedStageAcceptanceDecision":
        return self

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self._payload_json.decode("utf-8"))


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


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sorted_texts(values: Iterable[str]) -> list[str]:
    return sorted(set(values), key=lambda value: value.encode("utf-8"))


def _stable_key(value: object) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else _stable_json(value)


def _receipt_id(receipt: object) -> str | None:
    if not isinstance(receipt, Mapping):
        return None
    artifact = receipt.get("artifact")
    if not isinstance(artifact, Mapping):
        return None
    value = artifact.get("id")
    return value if isinstance(value, str) and value.strip() else None


def _receipt_evidence_bindings(
    receipts_by_id: Mapping[str, Mapping[str, Any]],
    required_ids: Iterable[str],
) -> tuple[Mapping[str, Any], ...]:
    """Build the exact artifact/run scope that a protected approval covers."""

    rows: list[dict[str, Any]] = []
    for receipt_id in sorted(set(required_ids), key=lambda value: value.encode("utf-8")):
        receipt = receipts_by_id.get(receipt_id)
        if not isinstance(receipt, Mapping):
            continue
        artifact = receipt.get("artifact")
        workflow = receipt.get("workflow_run_attempt")
        if not isinstance(artifact, Mapping) or not isinstance(workflow, Mapping):
            continue
        rows.append(
            {
                "receipt_id": receipt_id,
                "artifact_id": artifact.get("id"),
                "artifact_digest": artifact.get("digest"),
                "run_id": workflow.get("run_id"),
                "run_attempt": workflow.get("attempt"),
            }
        )
    return tuple(rows)


def _canonical_collection(receipts: Sequence[object]) -> list[bytes]:
    return sorted((_stable_json(item) for item in receipts))


def _canonical_receipts(receipts: Sequence[object]) -> tuple[object, ...]:
    """Give validation and duplicate handling an order-independent input."""

    return tuple(sorted(receipts, key=_stable_json))


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
    raw_receipts = _canonical_receipts(raw_receipts)

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
    binding: Mapping[str, Any] | None = None,
) -> TrustedStageAcceptanceDecision:
    body: dict[str, Any] = {
        "schema": TRUSTED_STAGE_ACCEPTANCE_DECISION_SCHEMA,
        "input_digest": input_digest,
        "status": status,
        "reasons": sorted(set(reasons), key=lambda value: value.encode("utf-8")),
        "receipt_refs": _sorted_texts(receipt_refs),
        "proof_refs": _sorted_texts(proof_refs),
        "binding": dict(binding or {}),
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
    return TrustedStageAcceptanceDecision(
        _issuer=_TRUSTED_DECISION_ISSUER,
        payload=body,
    )


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
    verified_protected_approval: Stage2B1ProtectedApprovalProof | None = None,
    expected_protected_approval: Mapping[str, Any] | None = None,
    task_id: str | None = None,
) -> TrustedStageAcceptanceDecision:
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
    raw_receipts = _canonical_receipts(raw_receipts)
    required_ids_input = tuple(required_receipt_ids)
    common: dict[str, str | None] = {
        "stage_id": stage_id,
        "accepted_state_id": accepted_state_id,
        "product_source_ref": product_source_ref,
        "protected_snapshot_digest": protected_snapshot_digest,
        "control_plane_ref": control_plane_ref,
        "execution_repo_ref": execution_repo_ref,
    }
    control_sha = _commit_sha(control_plane_ref)
    if control_sha is None:
        reasons = ["control_plane_ref_invalid"]
    else:
        reasons = []
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
    reasons = list(base["reasons"]) + reasons
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
            for value in sorted(unexpected, key=_stable_key)
        )

    if not isinstance(expected_external_issuer_bindings, Mapping):
        reasons.append("expected_external_issuer_bindings_required")
        expected_external_issuer_bindings = {}
    else:
        unexpected = set(expected_external_issuer_bindings) - set(required_ids)
        reasons.extend(
            f"expected_external_issuer_unexpected:{value}"
            for value in sorted(unexpected, key=_stable_key)
        )
        missing = set(required_ids) - set(expected_external_issuer_bindings)
        reasons.extend(
            f"expected_external_issuer_missing:{value}"
            for value in sorted(missing, key=_stable_key)
        )

    for receipt_id in sorted(set(required_ids), key=_stable_key):
        proof = verified_provenance.get(receipt_id)
        receipt = receipt_by_id.get(receipt_id)
        if not isinstance(proof, VerifiedArtifactProvenance):
            reasons.append(f"trusted_provenance_missing:{receipt_id}")
            continue
        try:
            validate_verified_artifact_provenance(proof)
        except ValueError:
            reasons.append(f"trusted_provenance_invalid:{receipt_id}")
            continue
        if proof.repository != STAGE2B1_REPOSITORY:
            reasons.append(f"trusted_provenance_repository_mismatch:{receipt_id}")
        if proof.workflow_path != _P48_WORKFLOW_PATH:
            reasons.append(f"trusted_provenance_workflow_mismatch:{receipt_id}")
        if proof.event != "workflow_dispatch":
            reasons.append(f"trusted_provenance_event_mismatch:{receipt_id}")
        if proof.ref != STAGE2B1_SOURCE_REF:
            reasons.append(f"trusted_provenance_ref_mismatch:{receipt_id}")
        if control_sha is not None and proof.head_sha != control_sha:
            reasons.append(f"trusted_provenance_control_plane_mismatch:{receipt_id}")
        if not proof.artifact_id.isdigit() or proof.artifact_id.startswith("0"):
            reasons.append(f"trusted_provenance_artifact_id_invalid:{receipt_id}")
        expected_name = f"p4-8-evidence-payload-{proof.run_id}-{proof.run_attempt}"
        if proof.artifact_name != expected_name:
            reasons.append(f"trusted_provenance_artifact_name_mismatch:{receipt_id}")
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
            if receipt.get("policy") != _P3_RECEIPT_POLICY:
                reasons.append(f"receipt_policy_invalid:{receipt_id}")
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
                    fixed_issuer = {
                        "repository": STAGE2B1_REPOSITORY,
                        "signer_workflow": STAGE2B1_SIGNER_WORKFLOW,
                        "predicate_type": STAGE2B1_PREDICATE_TYPE,
                        "subject_digest": proof.artifact_digest,
                        "source_digest": control_sha,
                        "source_ref": STAGE2B1_SOURCE_REF,
                    }
                    if set(expected_issuer) != set(fixed_issuer):
                        reasons.append(f"external_issuer_binding_fields_invalid:{receipt_id}")
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
                        if getattr(external_proof, field, None) != fixed_issuer[field]:
                            reasons.append(f"external_issuer_fixed_policy_mismatch:{receipt_id}:{field}")
                proof_refs.append(external_proof.proof_ref)

    approval_payload: Mapping[str, Any] | None = None
    if not isinstance(verified_protected_approval, Stage2B1ProtectedApprovalProof):
        reasons.append("trusted_protected_approval_required")
    else:
        approval = verified_protected_approval
        try:
            approval_payload = approval.as_dict()
        except (TypeError, ValueError, Stage2B1ProtectedHumanGateError):
            reasons.append("trusted_protected_approval_invalid")
        if approval_payload is not None:
            expected_evidence_bindings = list(
                _receipt_evidence_bindings(receipt_by_id, required_ids)
            )
            if approval_payload.get("evidence_bindings") != expected_evidence_bindings:
                reasons.append("trusted_protected_approval_evidence_binding_mismatch")
            for field, expected_value in {
                "stage_id": stage_id,
                "accepted_state_id": accepted_state_id,
                "product_source_ref": product_source_ref,
                "protected_snapshot_digest": protected_snapshot_digest,
                "control_plane_ref": control_plane_ref,
                "execution_repo_ref": execution_repo_ref,
            }.items():
                if expected_value is None or approval_payload.get(field) != expected_value:
                    reasons.append(f"trusted_protected_approval_binding_mismatch:{field}")
            if not isinstance(expected_protected_approval, Mapping):
                reasons.append("trusted_protected_approval_expected_required")
            else:
                missing = _EXPECTED_APPROVAL_FIELDS - set(expected_protected_approval)
                unknown = set(expected_protected_approval) - _EXPECTED_APPROVAL_FIELDS
                reasons.extend(
                    f"trusted_protected_approval_expected_missing:{field}"
                    for field in sorted(missing)
                )
                reasons.extend(
                    f"trusted_protected_approval_expected_unknown:{field}"
                    for field in sorted(unknown)
                )
                for field in sorted(
                    set(expected_protected_approval) & _EXPECTED_APPROVAL_FIELDS
                ):
                    expected_value = expected_protected_approval[field]
                    if approval_payload.get(field) != expected_value:
                        reasons.append(f"trusted_protected_approval_expected_mismatch:{field}")
            approval_sha256 = approval_payload.get("approval_sha256")
            if not isinstance(approval_sha256, str) or not approval_sha256.strip():
                reasons.append("trusted_protected_approval_invalid")
            else:
                proof_refs.append("protected-approval:" + approval_sha256)

    resolved_task_id = task_id
    if approval_payload is not None:
        approval_task_id = approval_payload.get("task_id")
        if not isinstance(approval_task_id, str) or not approval_task_id.strip():
            reasons.append("trusted_protected_approval_task_id_invalid")
        elif resolved_task_id is None:
            resolved_task_id = approval_task_id
        elif resolved_task_id != approval_task_id:
            reasons.append("trusted_protected_approval_task_id_mismatch")

    receipt_bindings = []
    for receipt_id in sorted(receipt_by_id, key=lambda value: value.encode("utf-8")):
        receipt = receipt_by_id[receipt_id]
        receipt_bindings.append(
            {
                "receipt_id": receipt_id,
                "artifact": receipt.get("artifact"),
                "workflow_run_attempt": receipt.get("workflow_run_attempt"),
                "policy": receipt.get("policy"),
            }
        )
    provenance_bindings = []
    for receipt_id in sorted(verified_provenance or {}, key=lambda value: value.encode("utf-8")):
        proof = verified_provenance[receipt_id]
        if isinstance(proof, VerifiedArtifactProvenance):
            provenance_bindings.append(
                {
                    "receipt_id": receipt_id,
                    "artifact_id": proof.artifact_id,
                    "artifact_digest": proof.artifact_digest,
                    "run_id": proof.run_id,
                    "run_attempt": proof.run_attempt,
                    "proof_ref": proof.proof_ref,
                }
            )
    external_bindings = []
    for receipt_id in sorted(verified_external_issuers or {}, key=lambda value: value.encode("utf-8")):
        proof = verified_external_issuers[receipt_id]
        if isinstance(proof, ExternalIssuerProof):
            external_bindings.append(
                {
                    "receipt_id": receipt_id,
                    "subject_digest": proof.subject_digest,
                    "source_digest": proof.source_digest,
                    "source_ref": proof.source_ref,
                    "proof_ref": proof.proof_ref,
                }
            )
    protected_binding = None
    if approval_payload is not None:
        protected_binding = {
            "approval_sha256": approval_payload.get("approval_sha256"),
            "gate_id": approval_payload.get("gate_id"),
            "task_id": approval_payload.get("task_id"),
            "run_id": approval_payload.get("run_id"),
            "run_attempt": approval_payload.get("run_attempt"),
            "evidence_bindings": approval_payload.get("evidence_bindings"),
        }
    decision_binding = {
        "common": dict(common),
        "task_id": resolved_task_id,
        "receipts": receipt_bindings,
        "provenance": provenance_bindings,
        "external_issuer": external_bindings,
        "protected_approval": protected_binding,
    }

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
        binding=decision_binding,
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
        "binding",
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
    binding = payload.get("binding")
    if not isinstance(binding, Mapping):
        raise ValueError("trusted_stage_acceptance_decision_binding_invalid")
    expected_binding_fields = {
        "common",
        "task_id",
        "receipts",
        "provenance",
        "external_issuer",
        "protected_approval",
    }
    if set(binding) != expected_binding_fields:
        raise ValueError("trusted_stage_acceptance_decision_binding_fields_invalid")
    common = binding.get("common")
    if not isinstance(common, Mapping) or set(common) != set(_COMMON_FIELDS):
        raise ValueError("trusted_stage_acceptance_decision_common_binding_invalid")
    if payload["status"] == ACCEPTABLE_PREVIEW:
        task_value = binding.get("task_id")
        if not isinstance(task_value, str) or not task_value.strip():
            raise ValueError("trusted_stage_acceptance_decision_task_binding_invalid")
        protected = binding.get("protected_approval")
        if not isinstance(protected, Mapping):
            raise ValueError("trusted_stage_acceptance_decision_approval_binding_invalid")
        if set(protected) != {
            "approval_sha256",
            "gate_id",
            "task_id",
            "run_id",
            "run_attempt",
            "evidence_bindings",
        }:
            raise ValueError("trusted_stage_acceptance_decision_approval_binding_invalid")
        if protected.get("task_id") != task_value:
            raise ValueError("trusted_stage_acceptance_decision_task_binding_mismatch")
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


def require_reducer_stage_acceptance_decision(
    payload: object,
) -> TrustedStageAcceptanceDecision:
    """Require the in-process decision issued by the trusted reducer."""

    if not isinstance(payload, TrustedStageAcceptanceDecision):
        raise ValueError("trusted_stage_acceptance_decision_must_be_reducer_issued")
    validate_trusted_stage_acceptance_decision(payload)
    return payload


@dataclass(frozen=True)
class TrustedStageAcceptanceVerificationInputs:
    """Explicit evidence needed to independently re-run the trusted reducer."""

    receipts: tuple[object, ...]
    required_receipt_ids: tuple[object, ...]
    expected_receipt_bindings: Mapping[str, Mapping[str, Any]]
    provenance_observations: Mapping[str, Mapping[str, Any]]
    expected_provenance: Mapping[str, Mapping[str, Any]]
    attested_artifact_paths: Mapping[str, str]
    expected_external_issuer_bindings: Mapping[str, Mapping[str, str]]
    protected_approval: Stage2B1ProtectedApprovalProof
    expected_protected_approval: Mapping[str, Any] | None = None


def _exact_evidence_map(
    value: object,
    *,
    required: set[str],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field}_invalid")
    keys = set(value)
    if keys != required or any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{field}_keys_invalid")
    return value


def reverify_trusted_stage_acceptance_decision(
    decision: object,
    *,
    verification: TrustedStageAcceptanceVerificationInputs,
    common_binding: Mapping[str, str],
) -> TrustedStageAcceptanceDecision:
    """Re-run every fixed verifier and compare the complete canonical result.

    This is the actual trust boundary for write-capable adapters.  A caller
    cannot make a forged typed object acceptable by recomputing a digest: the
    reducer is re-executed from explicit receipt/provenance/artifact inputs,
    GitHub attestation is checked again, and protected approval is read again
    from the fixed environment verifier.
    """

    supplied = require_reducer_stage_acceptance_decision(decision)
    if not isinstance(verification, TrustedStageAcceptanceVerificationInputs):
        raise ValueError("trusted_stage_acceptance_verification_inputs_invalid")
    if not isinstance(common_binding, Mapping) or set(common_binding) != set(_COMMON_FIELDS):
        raise ValueError("trusted_stage_acceptance_common_binding_invalid")
    required_ids = tuple(verification.required_receipt_ids)
    receipts = _canonical_receipts(tuple(verification.receipts))
    required = {
        item for item in required_ids if isinstance(item, str) and item.strip()
    }
    if not required or len(required) != len(required_ids):
        raise ValueError("trusted_stage_acceptance_required_receipt_ids_invalid")
    observations = _exact_evidence_map(
        verification.provenance_observations,
        required=required,
        field="trusted_stage_acceptance_provenance_inputs",
    )
    expected_provenance_input = _exact_evidence_map(
        verification.expected_provenance,
        required=required,
        field="trusted_stage_acceptance_expected_provenance",
    )
    artifact_paths = _exact_evidence_map(
        verification.attested_artifact_paths,
        required=required,
        field="trusted_stage_acceptance_attested_artifacts",
    )
    expected_external_input = _exact_evidence_map(
        verification.expected_external_issuer_bindings,
        required=required,
        field="trusted_stage_acceptance_external_bindings",
    )
    expected_receipt_bindings = _exact_evidence_map(
        verification.expected_receipt_bindings,
        required=required,
        field="trusted_stage_acceptance_receipt_bindings",
    )
    if not isinstance(verification.protected_approval, Stage2B1ProtectedApprovalProof):
        raise ValueError("trusted_stage_acceptance_protected_approval_invalid")
    source_sha = _commit_sha(common_binding.get("control_plane_ref"))
    if source_sha is None:
        raise ValueError("trusted_stage_acceptance_control_plane_ref_invalid")
    receipt_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in receipts:
        identity = _receipt_id(raw)
        if identity is None or identity in receipt_by_id:
            continue
        receipt_by_id[identity] = validate_stage_evidence_receipt(raw)
    approval_evidence_bindings = _receipt_evidence_bindings(
        receipt_by_id,
        required,
    )
    if len(approval_evidence_bindings) != len(required):
        raise ValueError("trusted_stage_acceptance_approval_evidence_incomplete")
    approval_binding = {
        **dict(common_binding),
        "evidence_bindings": list(approval_evidence_bindings),
    }

    verified_provenance: dict[str, VerifiedArtifactProvenance] = {}
    verified_external: dict[str, ExternalIssuerProof] = {}
    fixed_expected_external: dict[str, Mapping[str, str]] = {}
    for receipt_id in sorted(required, key=lambda value: value.encode("utf-8")):
        receipt = receipt_by_id.get(receipt_id)
        if receipt is None:
            raise ValueError(f"trusted_stage_acceptance_receipt_missing:{receipt_id}")
        artifact = receipt["artifact"]
        workflow = receipt["workflow_run_attempt"]
        if not isinstance(artifact, Mapping) or not isinstance(workflow, Mapping):
            raise ValueError(f"trusted_stage_acceptance_receipt_shape_invalid:{receipt_id}")
        artifact_id = artifact["id"]
        if not isinstance(artifact_id, str) or not artifact_id.isdigit() or artifact_id.startswith("0"):
            raise ValueError(f"trusted_stage_acceptance_artifact_id_invalid:{receipt_id}")
        expected_name = f"p4-8-evidence-payload-{workflow['run_id']}-{workflow['attempt']}"
        input_expected_provenance = expected_provenance_input[receipt_id]
        if not isinstance(input_expected_provenance, Mapping):
            raise ValueError(f"trusted_stage_acceptance_expected_provenance_invalid:{receipt_id}")
        fixed_values = {
            "receipt_id": receipt_id,
            "artifact_id": artifact_id,
            "artifact_name": expected_name,
            "artifact_digest": artifact["digest"],
            "repository": STAGE2B1_REPOSITORY,
            "workflow_path": _P48_WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "ref": STAGE2B1_SOURCE_REF,
            "head_sha": source_sha,
            "run_id": workflow["run_id"],
            "run_attempt": workflow["attempt"],
        }
        expected_provenance = dict(input_expected_provenance)
        for field, expected_value in fixed_values.items():
            if expected_provenance.get(field) != expected_value:
                raise ValueError(
                    f"trusted_stage_acceptance_provenance_binding_invalid:{receipt_id}:{field}"
                )
        fixed_external = {
            "repository": STAGE2B1_REPOSITORY,
            "signer_workflow": STAGE2B1_SIGNER_WORKFLOW,
            "predicate_type": STAGE2B1_PREDICATE_TYPE,
            "subject_digest": artifact["digest"],
            "source_digest": source_sha,
            "source_ref": STAGE2B1_SOURCE_REF,
        }
        input_expected_external = expected_external_input[receipt_id]
        if not isinstance(input_expected_external, Mapping) or dict(input_expected_external) != fixed_external:
            raise ValueError(
                f"trusted_stage_acceptance_external_binding_invalid:{receipt_id}"
            )
        fixed_expected_external[receipt_id] = fixed_external
        expected_receipt = expected_receipt_bindings[receipt_id]
        if not isinstance(expected_receipt, Mapping) or dict(expected_receipt) != {
            "artifact": dict(artifact),
            "workflow_run_attempt": dict(workflow),
            "policy": receipt["policy"],
        }:
            raise ValueError(
                f"trusted_stage_acceptance_receipt_binding_invalid:{receipt_id}"
            )
        source_proof: P48SourceArtifactProof = verify_p4_8_source_artifact(
            source_run_id=workflow["run_id"],
            source_run_attempt=workflow["attempt"],
            artifact_id=artifact_id,
            expected_head_sha=source_sha,
            expected_artifact_digest=artifact["digest"],
        )
        verified_provenance[receipt_id] = verify_artifact_provenance(
            observations[receipt_id],
            expected=expected_provenance,
        )
        provenance = verified_provenance[receipt_id]
        if (
            provenance.artifact_id != source_proof.artifact_id
            or provenance.artifact_digest != source_proof.artifact_digest
            or provenance.run_id != source_proof.source_run_id
            or provenance.run_attempt != source_proof.source_run_attempt
            or provenance.head_sha != source_proof.source_head_sha
        ):
            raise ValueError(
                f"trusted_stage_acceptance_source_proof_mismatch:{receipt_id}"
            )
        verified_external[receipt_id] = verify_p4_8_payload_attestation(
            artifact_paths[receipt_id],
            source_run_id=source_proof.source_run_id,
            source_run_attempt=source_proof.source_run_attempt,
            source_digest=source_sha,
            subject_digest=artifact["digest"],
        )
    protected = reverify_stage2b1_protected_approval(
        verification.protected_approval,
        binding=approval_binding,
    )
    recomputed = reduce_trusted_stage_acceptance(
        receipts,
        required_receipt_ids=required_ids,
        **dict(common_binding),
        expected_receipt_bindings=expected_receipt_bindings,
        verified_provenance=verified_provenance,
        verified_external_issuers=verified_external,
        expected_external_issuer_bindings=fixed_expected_external,
        verified_protected_approval=protected,
        expected_protected_approval=verification.expected_protected_approval,
        task_id=protected.task_id if hasattr(protected, "task_id") else None,
    )
    if supplied.as_dict() != recomputed.as_dict():
        raise ValueError("trusted_stage_acceptance_decision_reverification_mismatch")
    return recomputed


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
    "TrustedStageAcceptanceDecision",
    "TrustedStageAcceptanceVerificationInputs",
    "require_reducer_stage_acceptance_decision",
    "reverify_trusted_stage_acceptance_decision",
    "validate_stage_acceptance_decision",
    "validate_trusted_stage_acceptance_decision",
]
