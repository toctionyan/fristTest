from __future__ import annotations

import re
from typing import Any

from .constants import BLOCKED, FAIL, PASS, UPSTREAM_SKIPPED

_PRODUCTION_BUNDLE_GATE_ID = "production-certification-bundle"
_REAL_MODEL_BUNDLE_GATE_ID = "preproduction-real-model-certification-bundle"
_REAL_MODEL_BROWSER_GATE_IDS = (
    "configured-model-browser-conversation",
    "configured-model-browser-campaign",
)
_REAL_MODEL_REQUIRED_GATE_IDS = (_REAL_MODEL_BUNDLE_GATE_ID,) + _REAL_MODEL_BROWSER_GATE_IDS
_REAL_MODEL_IDENTITY_FIELDS = (
    "provider",
    "endpoint",
    "model",
    "credential_fingerprint_sha256_16",
)
_REAL_MODEL_BUNDLE_COMPONENTS = ("smoke", "semantic", "lifecycle")
_REAL_MODEL_MINIMUM_CALLS = {"smoke": 1, "semantic": 12, "lifecycle": 2}



def _dimension_decision(results: list[dict[str, Any]]) -> str:
    if not results:
        return "NOT_ASSESSED"
    statuses = {str(item.get("status") or "") for item in results}
    if FAIL in statuses or UPSTREAM_SKIPPED in statuses:
        return FAIL
    if BLOCKED in statuses:
        return BLOCKED
    return PASS

def _production_certification_dimension(
    results: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    if mode != "release":
        return {
            "status": "NOT_DECLARED",
            "reason": "production certification requires one complete release-mode run",
        }
    gate = next((row for row in results if str(row.get("id") or "") == _PRODUCTION_BUNDLE_GATE_ID), None)
    if gate is None:
        return {"status": FAIL, "reason": "required_production_bundle_gate_missing"}
    status = str(gate.get("status") or "")
    if status == BLOCKED:
        return {"status": BLOCKED, "reason": "production_environment_unavailable", "blocked_gate_ids": [_PRODUCTION_BUNDLE_GATE_ID]}
    if status != PASS:
        return {"status": FAIL, "reason": "production_bundle_gate_not_passed", "failed_gate_ids": [_PRODUCTION_BUNDLE_GATE_ID]}
    metadata = gate.get("metadata") if isinstance(gate.get("metadata"), dict) else {}
    assessment = metadata.get("structured_assessment") if isinstance(metadata.get("structured_assessment"), dict) else {}
    identity = assessment.get("real_model_identity") if isinstance(assessment.get("real_model_identity"), dict) else {}
    valid = (
        assessment.get("contract") == "production-certification-bundle@1"
        and assessment.get("status") == PASS
        and assessment.get("components") == ["real_model", "postgres", "browser"]
        and int(assessment.get("component_count") or 0) == 3
        and bool(re.fullmatch(r"prodcert-[0-9a-f]{32,96}", str(assessment.get("session_id") or "")))
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(assessment.get("workspace_fingerprint_sha256") or "")))
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(assessment.get("toolchain_fingerprint_sha256") or "")))
        and all(str(identity.get(field) or "").strip() for field in _REAL_MODEL_IDENTITY_FIELDS)
        and identity.get("official_endpoint") is True
        and identity.get("https") is True
        and int(assessment.get("real_model_total_attested_calls") or 0) >= 15
        and int(assessment.get("postgres_restart_count") or 0) >= 2
        and bool(re.fullmatch(r"[0-9a-f]{16}", str(assessment.get("postgres_database_instance_fingerprint_sha256_16") or "")))
        and bool(re.fullmatch(r"pgvector/pgvector@sha256:[0-9a-f]{64}", str(assessment.get("postgres_container_image_reference") or "")))
        and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(assessment.get("postgres_container_image_id_sha256") or "")))
        and int(assessment.get("browser_journey_count") or 0) >= 2
        and bool(str(assessment.get("browser_version") or "").strip())
        and assessment.get("evidence_scope") == "single-live-production-certification-session"
    )
    if not valid:
        return {"status": FAIL, "reason": "production_bundle_evidence_invalid", "gate_id": _PRODUCTION_BUNDLE_GATE_ID}
    return {
        "status": PASS,
        "contract": "production-certification-dimension@1",
        "gate_ids": [_PRODUCTION_BUNDLE_GATE_ID],
        "session_id": str(assessment["session_id"]),
        "workspace_fingerprint_sha256": str(assessment["workspace_fingerprint_sha256"]),
        "toolchain_fingerprint_sha256": str(assessment["toolchain_fingerprint_sha256"]),
        "real_model_identity": {field: identity.get(field) for field in (*_REAL_MODEL_IDENTITY_FIELDS, "official_endpoint", "https")},
        "real_model_total_attested_calls": int(assessment["real_model_total_attested_calls"]),
        "postgres_restart_count": int(assessment["postgres_restart_count"]),
        "postgres_container_image_reference": str(assessment["postgres_container_image_reference"]),
        "postgres_container_image_id_sha256": str(assessment["postgres_container_image_id_sha256"]),
        "browser_journey_count": int(assessment["browser_journey_count"]),
        "evidence_scope": "single-live-production-certification-session",
    }

def _real_model_certification_dimension(
    results: list[dict[str, Any]],
    *,
    mode: str,
) -> dict[str, Any]:
    """Consume one live bundle as the only release real-model authority."""
    if mode != "release":
        return {
            "status": "NOT_DECLARED",
            "reason": "real-model certification requires one complete release-mode run",
        }

    by_id = {str(row.get("id") or ""): row for row in results}
    if _PRODUCTION_BUNDLE_GATE_ID in by_id:
        production = _production_certification_dimension(results, mode=mode)
        if production.get("status") != PASS:
            return {
                "status": production.get("status"),
                "reason": production.get("reason"),
                **({"blocked_gate_ids": production.get("blocked_gate_ids")} if production.get("blocked_gate_ids") else {}),
                **({"failed_gate_ids": production.get("failed_gate_ids")} if production.get("failed_gate_ids") else {}),
            }
        return {
            "status": PASS,
            "contract": "real-model-certification-dimension@3",
            "bundle_contract": "production-certification-bundle@1",
            "gate_ids": [_PRODUCTION_BUNDLE_GATE_ID],
            "identity": production["real_model_identity"],
            "session_id": production["session_id"],
            "workspace_fingerprint_sha256": production["workspace_fingerprint_sha256"],
            "toolchain_fingerprint_sha256": production["toolchain_fingerprint_sha256"],
            "total_attested_model_calls": production["real_model_total_attested_calls"],
            "evidence_scope": "single-live-production-certification-session",
        }
    if _REAL_MODEL_BUNDLE_GATE_ID not in by_id:
        return {
            "status": FAIL,
            "reason": "required_real_model_bundle_gate_missing",
            "missing_gate_ids": [_REAL_MODEL_BUNDLE_GATE_ID],
        }
    missing = [gate_id for gate_id in _REAL_MODEL_BROWSER_GATE_IDS if gate_id not in by_id]
    if missing:
        return {
            "status": FAIL,
            "reason": "required_real_model_gates_missing",
            "missing_gate_ids": missing,
        }

    blocked = [
        gate_id for gate_id in _REAL_MODEL_REQUIRED_GATE_IDS
        if str(by_id[gate_id].get("status") or "") == BLOCKED
    ]
    if blocked:
        return {
            "status": BLOCKED,
            "reason": "real_model_environment_unavailable",
            "blocked_gate_ids": blocked,
        }
    failed = [
        gate_id for gate_id in _REAL_MODEL_REQUIRED_GATE_IDS
        if str(by_id[gate_id].get("status") or "") != PASS
    ]
    if failed:
        return {
            "status": FAIL,
            "reason": "required_real_model_gate_not_passed",
            "failed_gate_ids": failed,
        }

    metadata = by_id[_REAL_MODEL_BUNDLE_GATE_ID].get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    assessment = metadata.get("structured_assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    identity = assessment.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    safe_identity = {
        field: str(identity.get(field) or "").strip()
        for field in _REAL_MODEL_IDENTITY_FIELDS
    }
    components = assessment.get("components")
    components = list(components) if isinstance(components, list) else []
    call_counts = assessment.get("attested_model_calls_by_component")
    call_counts = call_counts if isinstance(call_counts, dict) else {}
    try:
        component_count = int(assessment.get("component_count"))
        total_calls = int(assessment.get("total_attested_model_calls"))
    except (TypeError, ValueError):
        component_count = 0
        total_calls = 0
    valid_call_counts = all(
        isinstance(call_counts.get(name), int)
        and int(call_counts[name]) >= minimum
        for name, minimum in _REAL_MODEL_MINIMUM_CALLS.items()
    )
    valid = (
        str(assessment.get("contract") or "") == "real-model-certification-bundle@1"
        and str(assessment.get("status") or "") == PASS
        and bool(str(assessment.get("session_id") or "").strip())
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(assessment.get("workspace_fingerprint_sha256") or "")))
        and all(safe_identity.values())
        and identity.get("official_endpoint") is True
        and identity.get("https") is True
        and component_count == len(_REAL_MODEL_BUNDLE_COMPONENTS)
        and components == list(_REAL_MODEL_BUNDLE_COMPONENTS)
        and valid_call_counts
        and total_calls >= sum(_REAL_MODEL_MINIMUM_CALLS.values())
    )
    if not valid:
        return {
            "status": FAIL,
            "reason": "real_model_bundle_evidence_invalid",
            "gate_id": _REAL_MODEL_BUNDLE_GATE_ID,
        }

    return {
        "status": PASS,
        "contract": "real-model-certification-dimension@2",
        "bundle_contract": "real-model-certification-bundle@1",
        "gate_ids": list(_REAL_MODEL_REQUIRED_GATE_IDS),
        "identity": safe_identity,
        "session_id": str(assessment["session_id"]),
        "workspace_fingerprint_sha256": str(assessment["workspace_fingerprint_sha256"]),
        "component_count": component_count,
        "components": components,
        "total_attested_model_calls": total_calls,
        "evidence_scope": "single-current-release-bundle",
    }

def _quality_dimensions(results: list[dict[str, Any]], *, mode: str = "quick") -> dict[str, Any]:
    """Expose functional, architecture and explicit real-model certification truth."""

    functional_categories = {
        "contract",
        "counterexample-regression",
        "frontend-test",
        "integration",
        "preproduction",
        "presentation",
        "unit-contract",
    }
    functional_results = [
        row for row in results if str(row.get("category") or "") in functional_categories
    ]

    architecture_gate = next(
        (row for row in results if str(row.get("id") or "") == "architecture-convergence"),
        None,
    )
    structured = None
    if architecture_gate is not None:
        candidate = (architecture_gate.get("metadata") or {}).get("structured_assessment")
        if isinstance(candidate, dict):
            structured = candidate
    architecture = {
        "status": (
            str(structured.get("architecture_status"))
            if structured and structured.get("architecture_status")
            else str(architecture_gate.get("status"))
            if architecture_gate is not None
            else "NOT_ASSESSED"
        ),
        "debt_status": (
            str(structured.get("architecture_debt_status"))
            if structured and structured.get("architecture_debt_status")
            else None
        ),
        "gate_status": (
            str(architecture_gate.get("status"))
            if architecture_gate is not None
            else "NOT_ASSESSED"
        ),
    }
    return {
        "functional": {"status": _dimension_decision(functional_results)},
        "architecture": architecture,
        "real_model_certification": _real_model_certification_dimension(results, mode=mode),
        "production_certification": _production_certification_dimension(results, mode=mode),
    }

