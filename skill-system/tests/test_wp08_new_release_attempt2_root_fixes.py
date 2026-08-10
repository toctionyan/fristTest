from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent-service"
SRC = AGENT / "src"
for path in (AGENT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {"goal_id": goal_id, "evidence_span": span, "depends_on": depends_on}


def _response(*, spans: list[str], edges: list[dict[str, str]], reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=json.dumps({
        "verdict": "exact",
        "outcome_spans": spans,
        "dependency_edges": edges,
        "reason_code": reason,
    }, ensure_ascii=False))


def _dependency_response(*, edges: list[dict[str, str]], reason: str) -> SimpleNamespace:
    return SimpleNamespace(content=json.dumps({
        "verdict": "exact",
        "dependency_edges": edges,
        "reason_code": reason,
    }, ensure_ascii=False))


def test_first_pass_dependency_cannot_freeze_without_candidate_blind_second_audit() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    user_text = "查一下鼠标订单，然后帮我申请退款"
    spans = ["查一下鼠标订单", "帮我申请退款"]
    asserted = [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]
    calls = [
        (_response(spans=spans, edges=asserted, reason="first_pass_execution_order_confusion"), {}),
        (_dependency_response(edges=[], reason="second_pass_shared_literal_target_is_independent"), {}),
    ]
    goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
    assert invoke.call_count == 2
    assert verdict.verdict == "mixed"
    assert verdict.reason_code == "blind_inventory_dependency_graph_mismatch"
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["blind_self_audit_attempted"] is True
    assert verdict.details["dependency_basis_audited"] is True


def test_true_result_dependency_survives_required_second_blind_audit() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    user_text = "查一下键盘订单，再看看它能不能退款"
    spans = ["查一下键盘订单", "它能不能退款"]
    edge = [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]
    audited_edge = [{
        **edge[0],
        "basis_kind": "result_reference",
        "basis_span": "它",
    }]
    calls = [
        (_response(spans=spans, edges=edge, reason="pronoun_result_dependency"), {}),
        (_dependency_response(edges=audited_edge, reason="pronoun_result_dependency_reaudited"), {}),
    ]
    goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=calls
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["blind_self_audit_attempted"] is True
    assert verdict.details["dependency_basis_audited"] is True
    assert verdict.details["dependency_edges"] == [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]
    assert verdict.details["dependency_edge_basis"] == audited_edge


def _contract(*, reference_cardinality: str, goal_result_cardinality: str = "single") -> dict:
    from agent_core.kernel.semantic_contract import (
        FROZEN_SEMANTIC_CONTRACT_VERSION,
        compute_semantic_digest,
    )
    from agent_core.context.reference_resolution import (
        normalize_reference_expression,
        resolve_reference_expression,
    )

    reference_expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "evidence_span": "它",
            "object_type": "order",
            "expected_cardinality": reference_cardinality,
        },
        user_text="它现在是什么状态？",
        expected_object_type="order",
        expected_cardinality=reference_cardinality,
    )
    proof = resolve_reference_expression(
        reference_expression,
        visible_result_refs=[{
            "result_ref": "h_result:latest-singleton",
            "source_turn": 4,
            "shape": "collection",
            "member_handles": ["artifact:order:10001"],
            "canonical_order": ["artifact:order:10001"],
            "resource_types": ["order"],
            "member_resource_types": ["order"],
            "discourse_recency_rank": 1,
        }],
    )
    assert proof["resolution_status"] == "UNIQUE"
    goal = {
        "goal_id": "g1",
        "description": "查询它现在的状态",
        "evidence_span": "它现在是什么状态",
        "requested_effect": {
            "domain": "order",
            "operation": "get_details",
            "object_type": "order",
            "raw_description": "查询它现在的状态",
        },
        "expected_result_cardinality": goal_result_cardinality,
        "required": True,
        "depends_on": [],
        "reference_expression": reference_expression,
        "referent_resolution_proof": proof,
        "resolved_reference": {
            "result_ref": proof["resolved_result_ref"],
            "member_handles": list(proof["resolved_member_handles"]),
            "proof_digest": proof["proof_digest"],
        },
    }
    contract = {
        "version": FROZEN_SEMANTIC_CONTRACT_VERSION,
        "authority": "sole_formal_turn_semantics",
        "immutable": True,
        "turn": 5,
        "user_text": "它现在是什么状态？",
        "summary": "查询单个订单状态",
        "goals": [goal],
        "goal_changes": [],
        "blocker_resolutions": [],
        "focus_change": None,
        "alignment_proof": {"verdict": "exact"},
        "granularity_proof": {"verdict": "exact"},
        "semantic_rewrite_allowed_after_freeze": False,
    }
    contract["semantic_digest"] = compute_semantic_digest(contract)
    contract["semantic_contract_id"] = f"semantic:5:{contract['semantic_digest'][:20]}"
    return contract


def test_semantic_binding_uses_referent_cardinality_not_goal_output_cardinality() -> None:
    from agent_core.runtime.capability_gate import _semantic_reference_binding_proof

    single_state = {"frozen_semantic_contract": _contract(reference_cardinality="single")}
    member = _semantic_reference_binding_proof(
        single_state,
        {"target": {"mode": "artifact", "left_handle": "artifact:order:10001"}},
        goal_ids={"g1"},
    )
    assert member["complete"] is True
    assert member["checks"][0]["reason_code"] == "resolved_single_reference_member_bound"
    parent = _semantic_reference_binding_proof(
        single_state,
        {"target": {"mode": "collection", "left_handle": "h_result:latest-singleton"}},
        goal_ids={"g1"},
    )
    assert parent["complete"] is False
    assert parent["checks"][0]["reason_code"] == "resolved_single_reference_requires_member_handle"

    # A Goal may return one selected answer while the historical referent is a
    # collection source. In that case the exact collection ResultRef remains the
    # correct binding; this proves the two cardinalities are intentionally separate.
    collection_state = {
        "frozen_semantic_contract": _contract(
            reference_cardinality="collection",
            goal_result_cardinality="single",
        )
    }
    collection = _semantic_reference_binding_proof(
        collection_state,
        {"target": {"mode": "collection", "left_handle": "h_result:latest-singleton"}},
        goal_ids={"g1"},
    )
    assert collection["complete"] is True
    assert collection["checks"][0]["expected_cardinality"] == "collection"
    assert collection["checks"][0]["goal_result_cardinality"] == "single"


def test_single_latest_reference_resolves_verified_member_without_runtime_guessing() -> None:
    from agent_core.context.reference_resolution import (
        normalize_reference_expression,
        resolve_reference_expression,
    )
    expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "expected_cardinality": "single",
            "evidence_span": "它",
        },
        user_text="它现在是什么状态？",
        expected_object_type="order",
        expected_cardinality="single",
    )
    proof = resolve_reference_expression(expression, visible_result_refs=[{
        "result_ref": "h_result:latest-singleton",
        "source_turn": 4,
        "shape": "collection",
        "member_handles": ["artifact:order:10001"],
        "canonical_order": ["artifact:order:10001"],
        "resource_types": ["order"],
        "member_resource_types": ["order"],
        "discourse_recency_rank": 1,
    }])
    assert proof["resolution_status"] == "UNIQUE"
    assert proof["resolved_member_handles"] == ["artifact:order:10001"]
    assert proof["auto_substitution_used"] is False


def test_prompt_surfaces_state_execution_support_and_single_referent_rules_consistently() -> None:
    granularity = (SRC / "agent_core/lifecycle/goal_granularity.py").read_text(encoding="utf-8")
    alignment = (SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    protocol = (SRC / "agent_core/lifecycle/protocol.py").read_text(encoding="utf-8")
    dialogue = (SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    reference = (SRC / "agent_core/context/reference_resolution.py").read_text(encoding="utf-8")
    gate = (SRC / "agent_core/runtime/capability_gate.py").read_text(encoding="utf-8")

    assert "execution-support dataflow" in granularity
    assert "semantic depends_on is not execution-support dataflow" in alignment
    assert "执行支持数据流" in protocol
    assert "执行支持数据流" in dialogue
    assert "reference_expression.expected_cardinality" in dialogue
    assert "expected_cardinality belongs to the historical referent" in reference
    assert 'reference_cardinality = str(reference_expression.get("expected_cardinality") or "unknown")' in gate
    assert "resolved_single_reference_requires_member_handle" in gate


def test_release_envelopes_remain_unchanged() -> None:
    smoke = (AGENT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    config = (SRC / "agent_core/config.py").read_text(encoding="utf-8")
    browser = (AGENT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
    assert 'model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")' in smoke
    assert '_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0' in config
    assert '_bounded_int_env("MODEL_MAX_RETRIES", 1' in config
    assert '{ timeout: 120_000 }' in browser
