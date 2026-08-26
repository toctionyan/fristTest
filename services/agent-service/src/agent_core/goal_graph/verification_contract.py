from __future__ import annotations

"""Versioned, replayable evidence contract for shadow Goal Graph verification.

This module deliberately sits beside the existing graph digest helpers.  Existing
graph, binding, and edge digests remain unchanged; this contract only governs the
diagnostic verification evidence attached by the shadow planner.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

VERIFICATION_CONTRACT_VERSION = "typed-goal-graph-verification@1"
VERIFICATION_CONTRACT_V2_VERSION = "typed-goal-graph-verification@2"
VERIFICATION_DIGEST_ALGORITHM = "sha256-canonical-semantic-json@1"
VERIFICATION_AUTHORITY = "shadow_only_diagnostic"

_NON_SEMANTIC_KEYS = frozenset(
    {
        "attempt_id",
        "created_at",
        "execution_id",
        "request_id",
        "run_id",
        "timestamp",
        "trace_id",
        "updated_at",
        "verification_digest",
    }
)
_ORDER_INSENSITIVE_KEYS = frozenset({"errors", "failure_codes"})


def _text(value: Any, *, limit: int = 400) -> str:
    return str(value or "").strip()[:limit]


def _stable_error_code(value: Any) -> str:
    return _text(value, limit=240).split(":", 1)[0]


_FAILURE_RULES = (
    ("GOAL_GRAPH_SCOPE_REQUIRED", "scope_missing", "scope"),
    ("SCOPE_REQUIRED", "scope_missing", "scope"),
    ("SCOPE_MISMATCH", "scope_mismatch", "scope"),
    ("CAPABILITY", "capability_missing", "capability"),
    ("TARGET_BINDING", "target_unbound", "target"),
    ("TARGET_UNRESOLVED", "target_unbound", "target"),
    ("TYPE_MISMATCH", "type_mismatch", "type"),
    ("CARDINALITY", "cardinality_mismatch", "cardinality"),
    ("EDGE_CYCLE", "dependency_cycle", "dependency"),
    ("DEPENDENCY_EDGE_CYCLE", "dependency_cycle", "dependency"),
    ("DEPENDENCY", "dependency_invalid", "dependency"),
    ("REQUIRED_INPUT", "target_unbound", "binding"),
    ("ARTIFACT", "artifact_unverified", "artifact"),
    ("VERSION", "unsupported_schema_version", "schema"),
    ("SCHEMA", "unsupported_schema_version", "schema"),
    ("AUTHORITY", "authority_violation", "authority"),
    ("PERMIT", "authority_violation", "authority"),
    ("DIGEST", "integrity_invalid", "integrity"),
    ("ID_INVALID", "integrity_invalid", "integrity"),
    ("IMMUTABLE", "integrity_invalid", "integrity"),
)


def classify_verification_error(error: Any) -> dict[str, str]:
    """Map an internal verifier error to a stable public taxonomy.

    The dynamic suffix of an internal error is intentionally not copied into the
    evidence.  This keeps diagnostics replayable and prevents user/runtime data
    from becoming part of the evidence contract.
    """

    source_code = _stable_error_code(error)
    upper = source_code.upper()
    for marker, code, category in _FAILURE_RULES:
        if marker in upper:
            return {
                "code": code,
                "category": category,
                "source_code": source_code,
            }
    return {
        "code": "verification_invalid",
        "category": "integrity",
        "source_code": source_code or "UNKNOWN",
    }


def _sort_key(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _canonicalize(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            normalized_key = str(raw_key)
            if normalized_key in _NON_SEMANTIC_KEYS:
                continue
            result[normalized_key] = _canonicalize(
                value[raw_key],
                key=normalized_key,
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        rows = [
            _canonicalize(item)
            for item in value
        ]
        if key in _ORDER_INSENSITIVE_KEYS:
            return sorted(rows, key=_sort_key)
        return rows
    return value


def canonicalize_verification(value: Any) -> Any:
    """Return the semantic canonical form used by the verification digest.

    Only explicitly diagnostic collections are order-independent.  Graph arrays
    and all other arrays retain order because their order may carry semantics.
    """

    return _canonicalize(deepcopy(value))


def canonical_verification_digest(value: Any) -> str:
    canonical = canonicalize_verification(value)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _failure(*, path: str, error: Any) -> dict[str, Any]:
    classification = classify_verification_error(error)
    return {
        "code": classification["code"],
        "category": classification["category"],
        "path": path,
        "expected": "verification_invariant_satisfied",
        "actual": classification["source_code"],
    }


def _summary(value: Any, *, path: str) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    raw_errors = [
        _stable_error_code(error)
        for error in list(source.get("errors") or [])
        if _stable_error_code(error)
    ]
    failures = [
        _failure(path=f"{path}.errors[{index}]", error=error)
        for index, error in enumerate(sorted(set(raw_errors)))
    ]
    summary: dict[str, Any] = {
        "ok": bool(source.get("ok")),
        "code": _stable_error_code(source.get("code") or ""),
        "errors": sorted(set(raw_errors)),
        "failures": failures,
    }
    if isinstance(source.get("derived_dependencies"), dict):
        summary["derived_dependencies"] = {
            str(goal_id): sorted(
                {
                    str(dependency)
                    for dependency in list(dependencies or [])
                    if str(dependency)
                }
            )
            for goal_id, dependencies in sorted(
                source["derived_dependencies"].items(),
                key=lambda item: str(item[0]),
            )
        }
    if source.get("dependency_authority") is not None:
        summary["dependency_authority"] = _text(
            source.get("dependency_authority"),
            limit=200,
        )
    return summary


def build_verification_evidence(
    verification: dict[str, Any] | None,
    *,
    graph: dict[str, Any] | None = None,
    frozen_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project raw verifier output into the stable shadow evidence contract."""

    source = verification if isinstance(verification, dict) else {}
    graph_source = graph if isinstance(graph, dict) else {}
    frozen_source = frozen_contract if isinstance(frozen_contract, dict) else {}
    graph_contract = (
        graph_source.get("source_semantic_contract")
        if isinstance(graph_source.get("source_semantic_contract"), dict)
        else {}
    )
    semantic_contract_id = _text(
        frozen_source.get("semantic_contract_id")
        or graph_contract.get("semantic_contract_id"),
        limit=500,
    )
    semantic_digest = _text(
        frozen_source.get("semantic_digest")
        or graph_contract.get("semantic_digest"),
        limit=128,
    )
    structural = _summary(source.get("structural"), path="structural")
    dataflow = _summary(source.get("dataflow"), path="dataflow")
    # Keep verifier stages in contract order: structural invariants first,
    # followed by dataflow closure diagnostics.  Each stage is already sorted
    # canonically by _summary, so this preserves stable semantic precedence.
    failures = [*structural["failures"], *dataflow["failures"]]
    evidence: dict[str, Any] = {
        "schema_version": VERIFICATION_CONTRACT_VERSION,
        "digest_algorithm": VERIFICATION_DIGEST_ALGORITHM,
        "authority": VERIFICATION_AUTHORITY,
        "ok": bool(source.get("ok")),
        "code": "VERIFICATION_VALID" if bool(source.get("ok")) else "VERIFICATION_INVALID",
        "errors": sorted(
            {
                str(row["actual"])
                for row in failures
                if str(row.get("actual") or "")
            }
        ),
        "failures": failures,
        "structural": structural,
        "dataflow": dataflow,
        "graph_digest": _text(graph_source.get("graph_digest"), limit=128),
        "semantic_contract_id": semantic_contract_id,
        "semantic_digest": semantic_digest,
        "execution_authority_granted": False,
        "tool_dispatch": False,
        "business_payload_included": False,
    }
    evidence["verification_digest"] = canonical_verification_digest(evidence)
    return evidence


def replay_verification_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    source = evidence if isinstance(evidence, dict) else {}
    expected = _text(source.get("verification_digest"), limit=128)
    actual = canonical_verification_digest(source)
    errors: list[str] = []
    if source.get("schema_version") != VERIFICATION_CONTRACT_VERSION:
        errors.append("VERIFICATION_SCHEMA_VERSION_INVALID")
    if source.get("digest_algorithm") != VERIFICATION_DIGEST_ALGORITHM:
        errors.append("VERIFICATION_DIGEST_ALGORITHM_INVALID")
    if source.get("authority") != VERIFICATION_AUTHORITY:
        errors.append("VERIFICATION_AUTHORITY_INVALID")
    if source.get("execution_authority_granted") is not False:
        errors.append("VERIFICATION_EXECUTION_AUTHORITY_FORBIDDEN")
    if source.get("tool_dispatch") is not False:
        errors.append("VERIFICATION_TOOL_DISPATCH_FORBIDDEN")
    if source.get("business_payload_included") is not False:
        errors.append("VERIFICATION_BUSINESS_PAYLOAD_FORBIDDEN")
    return {
        "ok": bool(expected) and expected == actual and not errors,
        "schema_version": _text(source.get("schema_version"), limit=120),
        "digest_algorithm": _text(source.get("digest_algorithm"), limit=160),
        "expected_digest": expected,
        "actual_digest": actual,
        "errors": errors,
    }


__all__ = [
    "VERIFICATION_AUTHORITY",
    "VERIFICATION_CONTRACT_VERSION",
    "VERIFICATION_DIGEST_ALGORITHM",
    "build_verification_evidence",
    "canonical_verification_digest",
    "canonicalize_verification",
    "classify_verification_error",
    "replay_verification_evidence",
]
