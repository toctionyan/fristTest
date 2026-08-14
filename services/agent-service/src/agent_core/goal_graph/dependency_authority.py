from __future__ import annotations

"""Immutable evidence contract for a future dependency-authority cutover.

This module never chooses tools, blocks execution, creates permits or changes
runtime dependency authority.  It only seals the already-audited Stage2C
comparison together with the identities that a later explicit cutover would
have to prove again.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

DEPENDENCY_AUTHORITY_ATTESTATION_VERSION = "typed-dependency-authority-attestation@1"
DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY = "immutable_audit_evidence_not_cutover_authority"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _normalized_completed_goal_ids(values: Iterable[str]) -> list[str]:
    return sorted({_text(value, limit=200) for value in values if _text(value, limit=200)})


def _shadow_integrity_errors(shadow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    stored = _text(shadow.get("shadow_digest"), limit=128)
    if not stored:
        errors.append("DEPENDENCY_SHADOW_DIGEST_REQUIRED")
    else:
        payload = deepcopy(shadow)
        payload.pop("shadow_digest", None)
        if stored != _digest(payload):
            errors.append("DEPENDENCY_SHADOW_DIGEST_INVALID")

    if _text(shadow.get("authority"), limit=240) != "audit_only_current_dependency_enforcement_unchanged":
        errors.append("DEPENDENCY_SHADOW_AUTHORITY_INVALID")
    if bool(shadow.get("cutover_performed")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_PERFORM_CUTOVER")
    if bool(shadow.get("changes_current_dependency_blocking")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_CHANGE_BLOCKING")
    if bool(shadow.get("changes_allowed_capability_tools")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_CHANGE_TOOL_SURFACE")
    if bool(shadow.get("blocks_execution")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_BLOCK_EXECUTION")
    if bool(shadow.get("creates_permit")):
        errors.append("DEPENDENCY_SHADOW_MUST_NOT_CREATE_PERMIT")
    return errors


def build_dependency_authority_attestation(
    *,
    dependency_shadow: dict[str, Any],
    semantic_contract_id: str,
    semantic_digest: str,
    capability_registry_version: str,
    completed_goal_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Seal Stage2C evidence without granting dependency authority."""

    shadow = deepcopy(dependency_shadow) if isinstance(dependency_shadow, dict) else {}
    completed = _normalized_completed_goal_ids(completed_goal_ids)
    evidence_errors = _shadow_integrity_errors(shadow)

    semantic_contract_id = _text(semantic_contract_id, limit=500)
    semantic_digest = _text(semantic_digest, limit=128)
    registry_version = _text(capability_registry_version, limit=300)
    graph_id = _text(shadow.get("typed_graph_id"), limit=500)
    graph_digest = _text(shadow.get("typed_graph_digest"), limit=128)
    coverage_digest = _text(shadow.get("typed_coverage_digest"), limit=128)

    if not semantic_contract_id:
        evidence_errors.append("SEMANTIC_CONTRACT_ID_REQUIRED")
    if not semantic_digest:
        evidence_errors.append("SEMANTIC_DIGEST_REQUIRED")
    if not registry_version:
        evidence_errors.append("CAPABILITY_REGISTRY_VERSION_REQUIRED")
    if not graph_id:
        evidence_errors.append("TYPED_GRAPH_ID_REQUIRED")
    if not graph_digest:
        evidence_errors.append("TYPED_GRAPH_DIGEST_REQUIRED")
    if not coverage_digest:
        evidence_errors.append("TYPED_COVERAGE_DIGEST_REQUIRED")

    source_status = _text(shadow.get("status"), limit=120)
    source_cutover_eligible = bool(shadow.get("cutover_eligible"))
    if evidence_errors:
        eligibility_status = "EVIDENCE_INVALID"
    elif source_status == "MATCHED" and source_cutover_eligible:
        eligibility_status = "ELIGIBLE_EVIDENCE_ONLY"
    else:
        eligibility_status = "NOT_ELIGIBLE"

    completion_snapshot = {
        "completed_goal_ids": completed,
        "authority": "validated_goal_lifecycle_projection",
    }
    payload: dict[str, Any] = {
        "version": DEPENDENCY_AUTHORITY_ATTESTATION_VERSION,
        "authority": DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY,
        "immutable": True,
        "eligibility_status": eligibility_status,
        "source_dependency_shadow_status": source_status or None,
        "source_dependency_shadow_digest": shadow.get("shadow_digest"),
        "source_shadow_cutover_eligible": source_cutover_eligible,
        "semantic_contract_id": semantic_contract_id or None,
        "semantic_digest": semantic_digest or None,
        "typed_graph_id": graph_id or None,
        "typed_graph_digest": graph_digest or None,
        "typed_coverage_digest": coverage_digest or None,
        "capability_registry_version": registry_version or None,
        "completion_snapshot": completion_snapshot,
        "completion_snapshot_digest": _digest(completion_snapshot),
        "evidence_errors": sorted(set(evidence_errors)),
        "cutover_authority_granted": False,
        "cutover_performed": False,
        "changes_current_dependency_blocking": False,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
    }
    payload["attestation_digest"] = _digest(payload)
    return payload


def dependency_authority_attestation_integrity(attestation: dict[str, Any] | None) -> dict[str, Any]:
    row = deepcopy(attestation) if isinstance(attestation, dict) else {}
    errors: list[str] = []
    if row.get("version") != DEPENDENCY_AUTHORITY_ATTESTATION_VERSION:
        errors.append("ATTESTATION_VERSION_INVALID")
    if row.get("authority") != DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY:
        errors.append("ATTESTATION_AUTHORITY_INVALID")
    if row.get("immutable") is not True:
        errors.append("ATTESTATION_IMMUTABLE_REQUIRED")

    stored = _text(row.get("attestation_digest"), limit=128)
    if not stored:
        errors.append("ATTESTATION_DIGEST_REQUIRED")
    else:
        payload = deepcopy(row)
        payload.pop("attestation_digest", None)
        if stored != _digest(payload):
            errors.append("ATTESTATION_DIGEST_INVALID")

    completion_snapshot = row.get("completion_snapshot") if isinstance(row.get("completion_snapshot"), dict) else {}
    expected_completion_digest = _digest(completion_snapshot)
    if _text(row.get("completion_snapshot_digest"), limit=128) != expected_completion_digest:
        errors.append("COMPLETION_SNAPSHOT_DIGEST_INVALID")

    for field in (
        "semantic_contract_id",
        "semantic_digest",
        "typed_graph_id",
        "typed_graph_digest",
        "typed_coverage_digest",
        "capability_registry_version",
        "source_dependency_shadow_digest",
    ):
        if not _text(row.get(field), limit=500):
            errors.append(f"{field.upper()}_REQUIRED")

    for field in (
        "cutover_authority_granted",
        "cutover_performed",
        "changes_current_dependency_blocking",
        "changes_allowed_capability_tools",
        "blocks_execution",
        "creates_permit",
        "mutates_semantics",
        "mutates_business_state",
    ):
        if bool(row.get(field)):
            errors.append(f"{field.upper()}_MUST_BE_FALSE")

    eligibility_status = _text(row.get("eligibility_status"), limit=120)
    if eligibility_status not in {"ELIGIBLE_EVIDENCE_ONLY", "NOT_ELIGIBLE", "EVIDENCE_INVALID"}:
        errors.append("ELIGIBILITY_STATUS_INVALID")
    if eligibility_status == "ELIGIBLE_EVIDENCE_ONLY":
        if _text(row.get("source_dependency_shadow_status"), limit=120) != "MATCHED":
            errors.append("ELIGIBLE_REQUIRES_MATCHED_SHADOW")
        if row.get("source_shadow_cutover_eligible") is not True:
            errors.append("ELIGIBLE_REQUIRES_SOURCE_ELIGIBILITY")
        if list(row.get("evidence_errors") or []):
            errors.append("ELIGIBLE_REQUIRES_NO_EVIDENCE_ERRORS")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "version": row.get("version"),
        "attestation_digest": stored or None,
    }


__all__ = [
    "DEPENDENCY_AUTHORITY_ATTESTATION_AUTHORITY",
    "DEPENDENCY_AUTHORITY_ATTESTATION_VERSION",
    "build_dependency_authority_attestation",
    "dependency_authority_attestation_integrity",
]
