from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services" / "agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for value in (AGENT_ROOT, AGENT_SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _state(text: str, span: str) -> dict:
    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract

    contract = freeze_semantic_contract(
        turn=3,
        user_text=text,
        summary=text,
        goals=[{
            "goal_id": "g1",
            "description": text,
            "evidence_span": text,
            "requested_effect": {
                "domain": "order",
                "operation": "query_logistics",
                "object_type": "order",
            },
            "expected_result_cardinality": "collection",
            "required": True,
            "depends_on": [],
            "target_candidate": {
                "scope_constraints": [{"evidence_span": span}],
            },
        }],
        alignment_proof={"verdict": "exact", "source": "test"},
    )
    return {"current_user_input": text, "frozen_semantic_contract": contract}


def test_scope_constraint_candidate_accepts_only_literal_goal_local_evidence() -> None:
    from agent_core.lifecycle.goal_planning import _normalize_target_candidate_scope_constraints

    candidate, errors = _normalize_target_candidate_scope_constraints(
        {"scope_constraints": [{"evidence_span": "待发货"}]},
        user_text="把待发货的订单取消，已签收的看看能不能退款",
        goal_evidence_span="把待发货的订单取消",
        goal_id="g1",
    )
    assert errors == []
    assert candidate == {"scope_constraints": [{"evidence_span": "待发货"}]}

    _, cross_goal_errors = _normalize_target_candidate_scope_constraints(
        {"scope_constraints": [{"evidence_span": "已签收"}]},
        user_text="把待发货的订单取消，已签收的看看能不能退款",
        goal_evidence_span="把待发货的订单取消",
        goal_id="g1",
    )
    assert cross_goal_errors == ["scope_constraint_evidence_not_in_goal:g1:0"]


def test_formal_scope_constraint_requires_real_parameter_binding() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("哪些还在路上？", "在路上")
    missing = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": []},
        visible_reference={"checks": []},
    )
    assert missing["complete"] is False
    assert missing["errors"] == ["formal_goal_scope_constraint_unbound:g1:0"]

    bound = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": [{
            "status": "covered",
            "source_span": "在路上",
            "parameter_path": "query.delivery_status",
            "provenance": "candidate_constraint_binding",
        }]},
        visible_reference={"checks": []},
    )
    assert bound["complete"] is True
    assert bound["checks"][0]["parameter_matches"][0]["parameter_path"] == "query.delivery_status"


def test_structural_target_mode_cannot_fake_scope_binding() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("哪些还在路上？", "在路上")
    proof = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": [{
            "status": "covered",
            "source_span": "在路上",
            "parameter_path": "target.mode",
            "provenance": "candidate_constraint_binding",
        }]},
        visible_reference={"checks": []},
    )
    assert proof["complete"] is False


def test_runtime_target_evidence_can_bind_scope_without_domain_keyword_rules() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("哪些还在路上？", "还在路上")
    proof = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": [{
            "status": "covered",
            "source_span": "在路上",
            "parameter_path": "target.status",
            "provenance": "runtime_target_evidence",
        }]},
        visible_reference={"checks": []},
    )
    assert proof["complete"] is True


def test_current_turn_verified_lineage_can_carry_scope_into_later_action() -> None:
    from agent_core.runtime.capability_gate import _formal_goal_scope_coverage_proof

    state = _state("把待发货的订单取消", "待发货")
    proof = _formal_goal_scope_coverage_proof(
        state,
        goal_ids={"g1"},
        parameterization={"bindings": []},
        visible_reference={
            "checks": [{
                "validated_ref": {
                    "reference_kind": "current_turn_verified_observation",
                    "source_operation": {
                        "target": {
                            "mode": "all_orders",
                            "status": "待处理值",
                            "status_span": "待发货",
                        }
                    },
                }
            }]
        },
    )
    assert proof["complete"] is True
    assert proof["checks"][0]["verified_lineage_spans"] == ["待发货"]


def test_core_scope_bridge_is_domain_neutral_and_keeps_condition_separate() -> None:
    goal_source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    gate_source = (AGENT_SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")
    semantic_source = (AGENT_SRC / "agent_core/runtime/semantic_capability_verifier.py").read_text(encoding="utf-8")
    start = goal_source.index("blind_dependency_instruction =")
    end = goal_source.index("prompt = {", start)
    policy = goal_source[start:end]
    assert "target_candidate.scope_constraints" in policy
    assert "Goal.condition" in policy
    assert "separate condition/dependency algebra" in policy
    assert "requested_effect" in policy
    assert "def _formal_goal_scope_coverage_proof" in gate_source
    assert "language_interpretation_used" in gate_source
    assert "target_candidate" in semantic_source
    scope_start = gate_source.index("def _literal_scope_overlap")
    scope_end = gate_source.index("def _visible_reference_proof", scope_start)
    scope_bridge = gate_source[scope_start:scope_end]
    for forbidden in ("待发货", "已签收", "在路上", "运输中", "快递员"):
        assert forbidden not in policy
        assert forbidden not in scope_bridge



def test_issue_execution_permit_fails_closed_when_frozen_scope_is_unbound() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch
    import agent_core.runtime.capability_gate as gate

    contract = SimpleNamespace(key="order.query_logistics:order", execution_kind="read")
    registry = SimpleNamespace(contract_for_tool=lambda _name: contract)
    state = {
        "current_turn_plan": {
            "effects": [{"effect_id": "effect-1", "goal_ids": ["g1"]}],
        },
    }
    with patch.object(gate, "normalize_tool_arguments", return_value=({}, {})), patch.object(
        gate, "validate_tool_arguments", return_value=[]
    ), patch.object(
        gate, "_parameterization_proof", return_value={"parameterization_complete": True, "bindings": [], "errors": []}
    ), patch.object(
        gate, "_visible_reference_proof", return_value={"complete": True, "checks": [], "errors": []}
    ), patch.object(
        gate, "_explicit_member_scope_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_derived_collection_scope_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_formal_goal_condition_coverage_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_formal_goal_scope_coverage_proof", return_value={
            "complete": False,
            "errors": ["formal_goal_scope_constraint_unbound:g1:0"],
        }
    ), patch.object(
        gate, "_semantic_reference_binding_proof", return_value={"complete": True, "errors": []}
    ), patch.object(
        gate, "_pretool_frontier_proof", return_value={"allowed": True, "errors": [], "reason_code": "allowed"}
    ), patch.object(
        gate, "semantic_goals", return_value=[]
    ), patch.object(
        gate, "goal_effect_match_proof", return_value={"allowed": True, "errors": []}
    ):
        decision = gate.issue_execution_permit(
            state=state,
            tool_name="query_logistics",
            args={},
            effect_id="effect-1",
            capability_registry=registry,
        )

    assert decision.permitted is False
    assert decision.rejection["code"] == "CAPABILITY_SCOPE_CONSTRAINT_UNBOUND"
    assert "formal_goal_scope_constraint_unbound:g1:0" in decision.match_proof["constraint_errors"]
