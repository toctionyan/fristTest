from __future__ import annotations

"""Neutral integrity and read projection for a frozen semantic contract.

This module cannot create or mutate turn semantics. Lifecycle remains the sole
owner of normalization, freezing, state transitions and migration.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

FROZEN_SEMANTIC_CONTRACT_VERSION = "frozen-turn-semantic-contract@1"


def _text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


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
    "compute_semantic_digest",
    "find_goal_dependency_cycle",
    "semantic_contract_integrity",
    "assert_semantic_contract_integrity",
    "semantic_goals",
]
