from __future__ import annotations

from copy import deepcopy


def test_recomputed_frozen_contract_integrity_rejects_dependency_cycle() -> None:
    """A forged/replayed contract cannot bypass the declaration-time DAG gate."""

    from agent_core.kernel.semantic_contract import (
        compute_semantic_digest,
        semantic_contract_integrity,
    )
    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract

    contract = freeze_semantic_contract(
        turn=7,
        user_text="先查资格再办理",
        summary="valid baseline",
        goals=[
            {
                "goal_id": "g1",
                "description": "查询资格",
                "evidence_span": "查资格",
                "requested_effect": {
                    "domain": "refund",
                    "operation": "evaluate_eligibility",
                    "object_type": "order",
                },
                "depends_on": [],
            },
            {
                "goal_id": "g2",
                "description": "办理",
                "evidence_span": "办理",
                "requested_effect": {
                    "domain": "refund",
                    "operation": "create",
                    "object_type": "order",
                },
                "depends_on": ["g1"],
            },
        ],
        alignment_proof={"verdict": "exact", "source": "test"},
    )

    forged = deepcopy(contract)
    forged["goals"][0]["depends_on"] = ["g2"]
    forged.pop("semantic_digest", None)
    forged.pop("semantic_contract_id", None)
    forged["semantic_digest"] = compute_semantic_digest(forged)
    forged["semantic_contract_id"] = f"semantic:7:{forged['semantic_digest'][:20]}"

    result = semantic_contract_integrity(forged)

    assert result["ok"] is False
    assert result["code"] == "SEMANTIC_CONTRACT_GOAL_DEPENDENCY_CYCLE"
    assert result["cycle"][0] == result["cycle"][-1]
    assert set(result["cycle"][:-1]) == {"g1", "g2"}


def test_execution_permit_rejects_tool_allowed_globally_but_not_for_bound_goal() -> None:
    """A Tool exposed for another Goal cannot satisfy the current effect binding."""

    from tests.runtime.test_unified_semantic_planning_contract import _effect_registry, _refund_contract
    from agent_core.lifecycle.semantic_contract import semantic_goals
    from agent_core.runtime.capability_effects import discover_exact_effect_surface
    from agent_core.runtime.capability_gate import build_effects, issue_execution_permit

    registry = _effect_registry()
    contract = _refund_contract()
    effects, _ = build_effects(
        plan_id="plan:stage2-goal-frontier",
        calls=[{"name": "prepare_refund", "args": {"goal_ids": ["goal-refund"]}}],
        capability_registry=registry,
    )
    state = {
        "turn_index": 8,
        "current_user_input": "把键盘退了",
        "frozen_semantic_contract": contract,
        "artifact_ledger": [],
        "capability_surface": discover_exact_effect_surface(registry, semantic_goals(contract)),
        "pretool_execution_policy": {
            "allowed_capability_tools": ["prepare_refund"],
            "goal_policies": [
                {
                    "goal_id": "goal-refund",
                    "allowed_tools": ["evaluate_refund_eligibility"],
                    "status": "FRONTIER_READY",
                    "enforcement": "contract_frontier",
                },
                {
                    "goal_id": "another-goal",
                    "allowed_tools": ["prepare_refund"],
                    "status": "FRONTIER_READY",
                    "enforcement": "contract_frontier",
                },
            ],
        },
        "current_turn_plan": {"effects": effects},
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="prepare_refund",
        args={},
        effect_id=effects[0]["effect_id"],
        capability_registry=registry,
    )

    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_NOT_IN_PRETOOL_FRONTIER"
    proof = decision.match_proof["pretool_execution_frontier"]
    assert proof["required"] is True
    assert proof["allowed"] is False
    assert "tool_not_in_goal_pretool_frontier:goal-refund" in proof["errors"]


def test_execution_permit_allows_tool_in_current_global_and_goal_frontier() -> None:
    """The added defense must not reject a legitimate current-frontier call."""

    from tests.runtime.test_unified_semantic_planning_contract import _effect_registry, _refund_contract
    from agent_core.lifecycle.semantic_contract import semantic_goals
    from agent_core.runtime.capability_effects import discover_exact_effect_surface
    from agent_core.runtime.capability_gate import build_effects, issue_execution_permit

    registry = _effect_registry()
    contract = _refund_contract()
    effects, _ = build_effects(
        plan_id="plan:stage2-frontier-allowed",
        calls=[{"name": "prepare_refund", "args": {"goal_ids": ["goal-refund"]}}],
        capability_registry=registry,
    )
    state = {
        "turn_index": 9,
        "current_user_input": "把键盘退了",
        "frozen_semantic_contract": contract,
        "artifact_ledger": [],
        "capability_surface": discover_exact_effect_surface(registry, semantic_goals(contract)),
        "pretool_execution_policy": {
            "allowed_capability_tools": ["prepare_refund"],
            "goal_policies": [
                {
                    "goal_id": "goal-refund",
                    "allowed_tools": ["prepare_refund"],
                    "status": "FRONTIER_READY",
                    "enforcement": "contract_frontier",
                }
            ],
        },
        "current_turn_plan": {"effects": effects},
    }

    decision = issue_execution_permit(
        state=state,
        tool_name="prepare_refund",
        args={},
        effect_id=effects[0]["effect_id"],
        capability_registry=registry,
    )

    assert decision.permitted is True
    assert decision.execution_permit is not None
    assert decision.match_proof["pretool_execution_frontier"]["allowed"] is True
