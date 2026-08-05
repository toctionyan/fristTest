from __future__ import annotations

import pytest


def test_goal_declaration_rejects_two_node_dependency_cycle() -> None:
    from agent_core.kernel.capability_registry import CapabilityRegistry
    from agent_core.lifecycle.goal_planning import validate_goal_declaration

    class ExactVerifier:
        def verify(self, *, user_text, goals, known_tools):
            return {
                "verdict": "exact",
                "evidence_spans": [goal["evidence_span"] for goal in goals],
                "missing_spans": [],
                "reason_code": "stage1_probe",
                "source": "test",
                "independent": True,
                "details": {},
            }

    result, plan = validate_goal_declaration(
        state={
            "current_user_input": "先查退款资格，符合就办理",
            "turn_index": 1,
            "goal_alignment_verifier": ExactVerifier(),
        },
        args={
            "summary": "cyclic dependency probe",
            "goals": [
                {
                    "goal_id": "g1",
                    "description": "查询退款资格",
                    "evidence_span": "查退款资格",
                    "requested_effect": {"domain": "refund", "operation": "evaluate_eligibility", "object_type": "order"},
                    "depends_on": ["g2"],
                },
                {
                    "goal_id": "g2",
                    "description": "办理退款",
                    "evidence_span": "符合就办理",
                    "requested_effect": {"domain": "refund", "operation": "create", "object_type": "order"},
                    "depends_on": ["g1"],
                },
            ],
        },
        capability_registry=CapabilityRegistry([], allow_empty=True),
    )

    assert result["ok"] is False
    assert plan is None
    assert any("goal_dependency_cycle" in error.lower() for error in result["data"]["errors"])


def test_semantic_contract_freeze_rejects_three_node_dependency_cycle() -> None:
    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract

    with pytest.raises(ValueError, match="goal_dependency_cycle"):
        freeze_semantic_contract(
            turn=1,
            user_text="执行三个相互依赖的任务",
            summary="cycle",
            goals=[
                {"goal_id": "g1", "description": "one", "evidence_span": "三个", "requested_effect": {"domain": "x", "operation": "one", "object_type": "item"}, "depends_on": ["g2"]},
                {"goal_id": "g2", "description": "two", "evidence_span": "三个", "requested_effect": {"domain": "x", "operation": "two", "object_type": "item"}, "depends_on": ["g3"]},
                {"goal_id": "g3", "description": "three", "evidence_span": "三个", "requested_effect": {"domain": "x", "operation": "three", "object_type": "item"}, "depends_on": ["g1"]},
            ],
            alignment_proof={"verdict": "exact", "source": "stage1_probe"},
        )


def test_execution_permit_rejects_tool_outside_current_pretool_frontier() -> None:
    from tests.runtime.test_unified_semantic_planning_contract import _effect_registry, _refund_contract
    from agent_core.lifecycle.semantic_contract import semantic_goals
    from agent_core.runtime.capability_effects import discover_exact_effect_surface
    from agent_core.runtime.capability_gate import build_effects, issue_execution_permit

    registry = _effect_registry()
    contract = _refund_contract()
    effects, _ = build_effects(
        plan_id="plan:stage1-frontier",
        calls=[{"name": "prepare_refund", "args": {"goal_ids": ["goal-refund"]}}],
        capability_registry=registry,
    )
    state = {
        "turn_index": 4,
        "current_user_input": "把键盘退了",
        "frozen_semantic_contract": contract,
        "artifact_ledger": [],
        "capability_surface": discover_exact_effect_surface(registry, semantic_goals(contract)),
        "pretool_execution_policy": {"allowed_capability_tools": ["list_orders"]},
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


def test_fuzzy_entity_recall_cannot_create_verified_singleton_write_target() -> None:
    from agent_modules.ecommerce.shared.context import _match_orders_with_proof

    rows = [
        {"order_id": "10002", "product_name": "机械键盘", "product_id": "kb-mech"},
        {"order_id": "11003", "product_name": "键盘 Pro", "product_id": "kb-pro"},
        {"order_id": "11004", "product_name": "键盘保护套", "product_id": "kb-cover"},
    ]
    # Fuzzy recall may still rank one best candidate for a read-side hint, but
    # its proof must remain candidate-only and cannot authorize a write target.
    matched, proof = _match_orders_with_proof(rows, "机械键帽坏了")

    assert [row["order_id"] for row in matched] == ["10002"]
    assert proof["basis"] == "fuzzy_lexical_recall"
    assert proof["candidate_only"] is True
    assert proof["verified_for_write"] is False


def test_dependent_goal_reuses_verified_upstream_typed_output() -> None:
    from tests.runtime.test_stage4_goal_output_refs import _state_and_output
    from agent_core.lifecycle.pretool_execution_policy import build_pretool_execution_policy

    state, registry, _order, _eligibility = _state_and_output()
    policy = build_pretool_execution_policy(state=state, capability_registry=registry)
    by_goal = {row["goal_id"]: row for row in policy["goal_policies"]}

    assert by_goal["refund"]["allowed_tools"] == ["prepare_refund_from_eligibility"]
    assert by_goal["refund"]["completed_tools"] == ["evaluate_refund_eligibility"]

