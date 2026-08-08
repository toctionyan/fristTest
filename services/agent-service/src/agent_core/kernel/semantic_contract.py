from __future__ import annotations

"""Neutral integrity and read projection for a frozen semantic contract.

This module cannot create or mutate turn semantics. Lifecycle remains the sole
owner of normalization, freezing, state transitions and migration.
"""

from copy import deepcopy
from hashlib import sha256
import json
import math
from typing import Any, Iterable

FROZEN_SEMANTIC_CONTRACT_VERSION = "frozen-turn-semantic-contract@1"


def _text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


GOAL_TARGET_COMPATIBILITY_VERSION = "goal-target-compatibility@1"


def _canonical_target_value(value: Any) -> Any:
    """Return an exact JSON-safe target value or raise for non-contract data.

    Target identity is derived only from already-frozen semantic fields. The
    helper performs no domain interpretation, aliasing, similarity matching or
    fallback. Unknown/non-JSON values fail closed rather than being stringified
    into a misleading identity.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("target_identity_non_finite_number")
        return value
    if isinstance(value, list):
        return [_canonical_target_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("target_identity_non_string_key")
        return {key: _canonical_target_value(value[key]) for key in sorted(value)}
    raise ValueError("target_identity_non_json_value")


def derive_goal_target_identity(goal: dict[str, Any] | None) -> dict[str, Any]:
    """Derive one non-authoritative target identity from frozen Goal semantics.

    ``resolved_reference`` remains the stronger historical-reference proof and
    takes precedence over an open ``target_candidate``. This is a pure read
    projection: it does not resolve, select, persist or rewrite a target.
    """

    row = goal if isinstance(goal, dict) else {}
    object_type = _text((row.get("requested_effect") or {}).get("object_type"), limit=160)
    resolved = row.get("resolved_reference") if isinstance(row.get("resolved_reference"), dict) else {}
    result_ref = _text(resolved.get("result_ref"), limit=500)
    members = [str(value) for value in list(resolved.get("member_handles") or []) if str(value)]
    if result_ref and members:
        material: dict[str, Any] = {
            "source": "resolved_reference",
            "object_type": object_type or "unspecified",
            "result_ref": result_ref,
            "member_handles": members,
        }
        if isinstance(resolved.get("position"), int) and not isinstance(resolved.get("position"), bool):
            material["position"] = int(resolved["position"])
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "version": GOAL_TARGET_COMPATIBILITY_VERSION,
            "status": "PROVEN",
            "source": "resolved_reference",
            "object_type": material["object_type"],
            "identity_digest": sha256(encoded.encode("utf-8")).hexdigest(),
        }

    candidate = row.get("target_candidate")
    if isinstance(candidate, dict) and candidate:
        try:
            normalized = _canonical_target_value(candidate)
        except ValueError as exc:
            return {
                "version": GOAL_TARGET_COMPATIBILITY_VERSION,
                "status": "UNKNOWN",
                "source": "target_candidate",
                "object_type": object_type or "unspecified",
                "reason_code": str(exc),
                "identity_digest": None,
            }
        material = {
            "source": "target_candidate",
            "object_type": object_type or "unspecified",
            "value": normalized,
        }
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "version": GOAL_TARGET_COMPATIBILITY_VERSION,
            "status": "PROVEN",
            "source": "target_candidate",
            "object_type": material["object_type"],
            "identity_digest": sha256(encoded.encode("utf-8")).hexdigest(),
        }

    return {
        "version": GOAL_TARGET_COMPATIBILITY_VERSION,
        "status": "UNKNOWN",
        "source": "none",
        "object_type": object_type or "unspecified",
        "reason_code": "frozen_goal_target_identity_absent",
        "identity_digest": None,
    }


def prove_goal_target_compatibility(goals: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Prove whether all supplied frozen Goals have the exact same target.

    Unknown identity is intentionally not treated as compatible. Sharing is an
    optimization; lack of proof therefore falls back to independent execution.
    """

    rows = [row for row in goals if isinstance(row, dict)]
    identities = [
        {
            "goal_id": _text(row.get("goal_id"), limit=200),
            **derive_goal_target_identity(row),
        }
        for row in rows
    ]
    if len(identities) < 2:
        status = "UNKNOWN"
        reason = "insufficient_goal_targets"
    elif any(str(row.get("status") or "") != "PROVEN" for row in identities):
        status = "UNKNOWN"
        reason = "target_identity_unproven"
    else:
        digests = {str(row.get("identity_digest") or "") for row in identities}
        if len(digests) == 1:
            status = "SAME"
            reason = "exact_frozen_target_identity"
        else:
            status = "DIFFERENT"
            reason = "frozen_target_identity_mismatch"
    return {
        "version": GOAL_TARGET_COMPATIBILITY_VERSION,
        "status": status,
        "reason_code": reason,
        "goal_ids": [str(row.get("goal_id") or "") for row in identities],
        "identities": identities,
        "auto_substitution_used": False,
        "similarity_used": False,
    }


def _digest_payload(contract: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(contract)
    payload.pop("semantic_digest", None)
    payload.pop("semantic_contract_id", None)
    return payload


def compute_semantic_digest(contract: dict[str, Any]) -> str:
    digest_source = json.dumps(
        _digest_payload(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(digest_source.encode("utf-8")).hexdigest()


def find_goal_dependency_cycle(goals: list[dict[str, Any]]) -> list[str]:
    """Return one deterministic Goal dependency cycle, including its start twice.

    Dependency direction follows the contract representation: ``goal -> goals it
    depends on``.  The helper is deliberately neutral and performs no semantic
    inference; it only validates that a frozen orchestration graph is a DAG.
    Unknown dependencies are ignored here and are reported separately by the
    declaration/freezing integrity checks.
    """

    goal_ids = {
        _text(row.get("goal_id"), limit=200)
        for row in goals
        if isinstance(row, dict) and _text(row.get("goal_id"), limit=200)
    }
    graph: dict[str, tuple[str, ...]] = {}
    for row in goals:
        if not isinstance(row, dict):
            continue
        goal_id = _text(row.get("goal_id"), limit=200)
        if not goal_id:
            continue
        dependencies = tuple(
            dict.fromkeys(
                dependency
                for dependency in (
                    _text(value, limit=200)
                    for value in list(row.get("depends_on") or [])
                )
                if dependency and dependency in goal_ids
            )
        )
        graph[goal_id] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    stack_index: dict[str, int] = {}

    def visit(goal_id: str) -> list[str]:
        if goal_id in visiting:
            start = stack_index[goal_id]
            return [*stack[start:], goal_id]
        if goal_id in visited:
            return []
        visiting.add(goal_id)
        stack_index[goal_id] = len(stack)
        stack.append(goal_id)
        for dependency in graph.get(goal_id, ()):
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        stack_index.pop(goal_id, None)
        visiting.remove(goal_id)
        visited.add(goal_id)
        return []

    for goal_id in sorted(graph):
        cycle = visit(goal_id)
        if cycle:
            return cycle
    return []


def semantic_contract_integrity(contract: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(contract, dict):
        return {"ok": False, "code": "SEMANTIC_CONTRACT_REQUIRED"}
    stored = _text(contract.get("semantic_digest"), limit=128)
    if not stored:
        return {"ok": False, "code": "SEMANTIC_CONTRACT_DIGEST_REQUIRED"}
    computed = compute_semantic_digest(contract)
    if stored != computed:
        return {
            "ok": False,
            "code": "SEMANTIC_CONTRACT_DIGEST_INVALID",
            "stored_digest": stored,
            "computed_digest": computed,
        }
    expected_id = f"semantic:{int(contract.get('turn') or 0)}:{computed[:20]}"
    if _text(contract.get("semantic_contract_id"), limit=300) != expected_id:
        return {
            "ok": False,
            "code": "SEMANTIC_CONTRACT_ID_INVALID",
            "expected_id": expected_id,
        }
    goals = [row for row in list(contract.get("goals") or []) if isinstance(row, dict)]
    goal_ids = [_text(row.get("goal_id"), limit=200) for row in goals]
    if any(not goal_id for goal_id in goal_ids) or len(goal_ids) != len(set(goal_ids)):
        return {"ok": False, "code": "SEMANTIC_CONTRACT_GOAL_IDS_INVALID"}
    known = set(goal_ids)
    for row in goals:
        goal_id = _text(row.get("goal_id"), limit=200)
        unknown = [
            dependency
            for dependency in (
                _text(value, limit=200)
                for value in list(row.get("depends_on") or [])
            )
            if dependency and dependency not in known
        ]
        if unknown:
            return {
                "ok": False,
                "code": "SEMANTIC_CONTRACT_UNKNOWN_GOAL_DEPENDENCY",
                "goal_id": goal_id,
                "unknown_goal_ids": unknown,
            }
    cycle = find_goal_dependency_cycle(goals)
    if cycle:
        return {
            "ok": False,
            "code": "SEMANTIC_CONTRACT_GOAL_DEPENDENCY_CYCLE",
            "cycle": cycle,
        }
    return {"ok": True, "code": "SEMANTIC_CONTRACT_INTEGRITY_OK", "computed_digest": computed}


def assert_semantic_contract_integrity(contract: dict[str, Any] | None) -> None:
    result = semantic_contract_integrity(contract)
    if not result.get("ok"):
        raise ValueError(str(result.get("code") or "SEMANTIC_CONTRACT_INTEGRITY_INVALID"))


def semantic_goals(state_or_contract: dict[str, Any]) -> list[dict[str, Any]]:
    contract = state_or_contract
    if "frozen_semantic_contract" in state_or_contract:
        contract = state_or_contract.get("frozen_semantic_contract") or {}
    if not isinstance(contract, dict) or contract.get("version") != FROZEN_SEMANTIC_CONTRACT_VERSION:
        return []
    if not semantic_contract_integrity(contract).get("ok"):
        return []
    return deepcopy([row for row in list(contract.get("goals") or []) if isinstance(row, dict)])


__all__ = [
    "FROZEN_SEMANTIC_CONTRACT_VERSION",
    "GOAL_TARGET_COMPATIBILITY_VERSION",
    "compute_semantic_digest",
    "derive_goal_target_identity",
    "find_goal_dependency_cycle",
    "prove_goal_target_compatibility",
    "semantic_contract_integrity",
    "assert_semantic_contract_integrity",
    "semantic_goals",
]
