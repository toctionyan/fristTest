from __future__ import annotations

"""Read-only compiler from FrozenSemanticContract to a shadow typed Goal Graph."""

from copy import deepcopy
from typing import Any

from agent_core.kernel.semantic_contract import semantic_contract_integrity, semantic_goals

from .contracts import (
    CANONICAL_GOAL_GRAPH_VERSION,
    make_goal_port,
    make_unresolved_target_binding,
    make_verified_historical_target_binding,
    normalize_cardinality,
    normalize_scope,
    requested_effect_subject_type,
    requested_output_ids,
    seal_goal_graph,
)


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _target_port_cardinality(goal: dict[str, Any]) -> str:
    expression = goal.get("reference_expression") if isinstance(goal.get("reference_expression"), dict) else {}
    explicit = normalize_cardinality(expression.get("expected_cardinality"))
    if explicit != "unknown":
        return explicit
    resolved = goal.get("resolved_reference") if isinstance(goal.get("resolved_reference"), dict) else {}
    members = [str(value) for value in list(resolved.get("member_handles") or []) if str(value)]
    if len(members) == 1:
        return "exactly_one"
    if len(members) > 1:
        return "collection"
    return "unknown"


def _target_is_declared(goal: dict[str, Any]) -> bool:
    return any(
        goal.get(key) not in (None, "", [], {})
        for key in (
            "target_candidate",
            "reference_expression",
            "referent_resolution_proof",
            "resolved_reference",
            "input_candidates",
        )
    )


def _proven_resource_type(goal: dict[str, Any], *, result_ref: str, fallback: str) -> str:
    proof = goal.get("referent_resolution_proof") if isinstance(goal.get("referent_resolution_proof"), dict) else {}
    candidate_rows = [
        row
        for row in list(proof.get("candidate_refs") or [])
        if isinstance(row, dict) and str(row.get("result_ref") or "") == result_ref
    ]
    proven: list[str] = []
    for row in candidate_rows:
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        if not bool(checks.get("object_type_proven", False)):
            continue
        if not bool(checks.get("object_type_match", True)):
            continue
        for value in list(row.get("resource_types") or []):
            resource_type = _text(value, limit=200).casefold()
            if resource_type and resource_type not in proven:
                proven.append(resource_type)
    if fallback != "unspecified" and fallback in proven:
        return fallback
    if len(proven) == 1:
        return proven[0]
    return ""


def _compile_target_binding(
    goal: dict[str, Any],
    *,
    resource_type: str,
    port_cardinality: str,
    scope: dict[str, Any],
    semantic_contract_id: str,
    semantic_digest: str,
) -> dict[str, Any]:
    goal_id = _text(goal.get("goal_id"), limit=200)
    resolved = goal.get("resolved_reference") if isinstance(goal.get("resolved_reference"), dict) else {}
    proof = goal.get("referent_resolution_proof") if isinstance(goal.get("referent_resolution_proof"), dict) else {}
    if not resolved:
        return make_unresolved_target_binding(
            goal_id=goal_id,
            resource_type=resource_type,
            cardinality=port_cardinality,
            scope=scope,
            semantic_contract_id=semantic_contract_id,
            semantic_digest=semantic_digest,
            reason_code="FROZEN_TARGET_PROOF_ABSENT",
        )

    result_ref = _text(resolved.get("result_ref"), limit=500)
    members = [str(value) for value in list(resolved.get("member_handles") or []) if str(value)]
    proof_digest = _text(resolved.get("proof_digest"), limit=256)
    if (
        str(proof.get("resolution_status") or "") != "UNIQUE"
        or not result_ref
        or not members
        or not proof_digest
        or _text(proof.get("resolved_result_ref"), limit=500) != result_ref
        or [str(value) for value in list(proof.get("resolved_member_handles") or []) if str(value)] != members
        or _text(proof.get("proof_digest"), limit=256) != proof_digest
    ):
        return make_unresolved_target_binding(
            goal_id=goal_id,
            resource_type=resource_type,
            cardinality=port_cardinality,
            scope=scope,
            semantic_contract_id=semantic_contract_id,
            semantic_digest=semantic_digest,
            reason_code="FROZEN_TARGET_PROOF_MISMATCH",
        )

    proven_resource_type = _proven_resource_type(goal, result_ref=result_ref, fallback=resource_type)
    if not proven_resource_type:
        return make_unresolved_target_binding(
            goal_id=goal_id,
            resource_type=resource_type,
            cardinality=port_cardinality,
            scope=scope,
            semantic_contract_id=semantic_contract_id,
            semantic_digest=semantic_digest,
            reason_code="TARGET_RESOURCE_TYPE_NOT_PROVEN",
        )

    actual_cardinality = "exactly_one" if len(members) == 1 else "collection"
    if port_cardinality not in {"unknown", "one_or_collection", actual_cardinality}:
        return make_unresolved_target_binding(
            goal_id=goal_id,
            resource_type=proven_resource_type,
            cardinality=port_cardinality,
            scope=scope,
            semantic_contract_id=semantic_contract_id,
            semantic_digest=semantic_digest,
            reason_code="TARGET_CARDINALITY_PROOF_MISMATCH",
        )

    return make_verified_historical_target_binding(
        goal_id=goal_id,
        resource_type=proven_resource_type,
        result_ref=result_ref,
        member_handles=members,
        proof_digest=proof_digest,
        scope=scope,
        semantic_contract_id=semantic_contract_id,
        semantic_digest=semantic_digest,
        position=(
            int(resolved["position"])
            if isinstance(resolved.get("position"), int) and not isinstance(resolved.get("position"), bool)
            else None
        ),
    )


def compile_frozen_semantic_contract(
    frozen_contract: dict[str, Any] | None,
    *,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a deterministic, non-executable Goal Graph projection.

    The compiler never performs capability discovery, tool selection, semantic
    rewriting, target guessing, or execution authorization. Legacy ``depends_on``
    values are preserved only as non-authoritative dependency claims; Stage 1
    intentionally creates no dataflow edge from those claims.
    """

    integrity = semantic_contract_integrity(frozen_contract)
    if not integrity.get("ok"):
        raise ValueError(f"SEMANTIC_CONTRACT_INVALID:{integrity.get('code') or 'UNKNOWN'}")
    semantic = deepcopy(frozen_contract or {})
    semantic_contract_id = _text(semantic.get("semantic_contract_id"), limit=500)
    semantic_digest = _text(semantic.get("semantic_digest"), limit=128)
    graph_scope = normalize_scope(scope)

    compiled_goals: list[dict[str, Any]] = []
    for goal in semantic_goals(semantic):
        goal_id = _text(goal.get("goal_id"), limit=200)
        requested_effect = deepcopy(goal.get("requested_effect") or {})
        resource_type = requested_effect_subject_type(requested_effect)
        result_cardinality = normalize_cardinality(goal.get("expected_result_cardinality"))

        output_ids = requested_output_ids(requested_effect)
        if not output_ids:
            operation = _text(requested_effect.get("operation"), limit=240).casefold() or "result"
            output_ids = [f"open:{operation}"]
        output_ports = [
            make_goal_port(
                goal_id=goal_id,
                name=f"result:{output_id}",
                direction="output",
                type_name=resource_type,
                cardinality=result_cardinality,
                required=bool(goal.get("required", True)),
                semantic_output_id=output_id,
            )
            for output_id in output_ids
        ]

        input_ports: list[dict[str, Any]] = []
        target_binding: dict[str, Any] | None = None
        if _target_is_declared(goal):
            target_cardinality = _target_port_cardinality(goal)
            input_ports.append(
                make_goal_port(
                    goal_id=goal_id,
                    name="target",
                    direction="input",
                    type_name=resource_type,
                    cardinality=target_cardinality,
                    required=bool(goal.get("required", True)),
                )
            )
            target_binding = _compile_target_binding(
                goal,
                resource_type=resource_type,
                port_cardinality=target_cardinality,
                scope=graph_scope,
                semantic_contract_id=semantic_contract_id,
                semantic_digest=semantic_digest,
            )

        compatibility: dict[str, Any] = {
            "legacy_dependency_claims": [
                _text(value, limit=200)
                for value in list(goal.get("depends_on") or [])
                if _text(value, limit=200)
            ],
            "dependency_claims_authoritative": False,
            "target_candidate_authoritative": False,
        }
        if goal.get("target_candidate") not in (None, "", [], {}):
            compatibility["target_candidate_claim"] = deepcopy(goal.get("target_candidate"))

        compiled_goal: dict[str, Any] = {
            "goal_id": goal_id,
            "required": bool(goal.get("required", True)),
            "requested_effect": requested_effect,
            "evidence_span": _text(goal.get("evidence_span"), limit=2000),
            "input_ports": input_ports,
            "output_ports": output_ports,
            "target_binding": target_binding,
            "compatibility": compatibility,
        }
        compiled_goals.append(compiled_goal)

    graph = {
        "version": CANONICAL_GOAL_GRAPH_VERSION,
        "authority": "shadow_typed_dataflow_projection",
        "immutable": True,
        "shadow_only": True,
        "runtime_behavior_change": False,
        "source_semantic_contract": {
            "version": _text(semantic.get("version"), limit=200),
            "semantic_contract_id": semantic_contract_id,
            "semantic_digest": semantic_digest,
            "turn": int(semantic.get("turn") or 0),
        },
        "scope": graph_scope,
        "goals": compiled_goals,
        "edges": [],
        "compiler_guarantees": {
            "input_authority": "frozen_semantic_contract_only",
            "semantic_rewrite_used": False,
            "target_guessing_used": False,
            "execution_authority_granted": False,
        },
    }
    return seal_goal_graph(graph)


__all__ = ["compile_frozen_semantic_contract"]
