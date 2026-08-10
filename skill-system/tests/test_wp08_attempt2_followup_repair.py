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


def test_blind_dependency_reaudit_reuses_first_grounded_exact_evidence() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    user_text = "查一下耳机物流，再查一下键盘物流"
    goals = [
        {"goal_id": "g1", "evidence_span": "查一下耳机物流", "depends_on": []},
        {"goal_id": "g2", "evidence_span": "查一下键盘物流", "depends_on": []},
    ]
    first = SimpleNamespace(content=json.dumps({
        "verdict": "exact",
        "evidence_spans": ["查一下耳机物流", "查一下键盘物流"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "all_outcomes_grounded",
    }, ensure_ascii=False))
    blind = SimpleNamespace(content=json.dumps({
        "verdict": "exact",
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "pairwise_independent",
    }, ensure_ascii=False))
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[(first, {}), (blind, {})]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=user_text,
            goals=goals,
            known_tools=set(),
        )
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.evidence_spans == ("查一下耳机物流", "查一下键盘物流")
    assert verdict.reason_code == "goal_alignment_candidate_blind_dependency_reaudit_exact"
    assert verdict.details["dependency_proof_complete"] is True
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_pair_decisions"] == [{
        "goal_a_id": "g1",
        "goal_b_id": "g2",
        "relation": "independent",
    }]
    assert verdict.details["candidate_blind_dependency_reaudit"] is True
    assert verdict.details["initial_alignment_reason_code"] == "all_outcomes_grounded"


def _contract(
    *,
    reference_cardinality: str,
    goal_result_cardinality: str = "single",
    member_handles: tuple[str, ...] = ("artifact:order:10001",),
) -> dict:
    from agent_core.kernel.semantic_contract import (
        FROZEN_SEMANTIC_CONTRACT_VERSION,
        compute_semantic_digest,
    )
    from agent_core.context.reference_resolution import (
        normalize_reference_expression,
        resolve_reference_expression,
    )

    expression = normalize_reference_expression(
        {
            "reference_type": "temporal_visible_result",
            "temporal_relation": "latest",
            "evidence_span": "其中",
            "object_type": "order",
            "expected_cardinality": reference_cardinality,
        },
        user_text="其中最贵的是哪个？",
        expected_object_type="order",
        expected_cardinality=reference_cardinality,
    )
    proof = resolve_reference_expression(
        expression,
        visible_result_refs=[{
            "result_ref": "h_result:filtered-scope",
            "source_turn": 3,
            "shape": "collection",
            "member_handles": list(member_handles),
            "canonical_order": list(member_handles),
            "resource_types": ["order"],
            "member_resource_types": ["order"],
            "discourse_recency_rank": 1,
        }],
    )
    assert proof["resolution_status"] == "UNIQUE"
    goal = {
        "goal_id": "g1",
        "description": "从其中找出最贵的订单",
        "evidence_span": "其中最贵的是哪个",
        "requested_effect": {
            "domain": "order",
            "operation": "get_details",
            "object_type": "order",
            "raw_description": "从其中找出最贵的订单",
        },
        "expected_result_cardinality": goal_result_cardinality,
        "required": True,
        "depends_on": [],
        "reference_expression": expression,
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
        "turn": 4,
        "user_text": "其中最贵的是哪个？",
        "summary": "从上轮集合中选一个结果",
        "goals": [goal],
        "goal_changes": [],
        "blocker_resolutions": [],
        "focus_change": None,
        "alignment_proof": {"verdict": "exact"},
        "granularity_proof": {"verdict": "exact"},
        "semantic_rewrite_allowed_after_freeze": False,
    }
    contract["semantic_digest"] = compute_semantic_digest(contract)
    contract["semantic_contract_id"] = f"semantic:4:{contract['semantic_digest'][:20]}"
    return contract


def test_singleton_collection_accepts_only_its_proven_member_for_single_result_goal() -> None:
    from agent_core.runtime.capability_gate import _semantic_reference_binding_proof

    state = {"frozen_semantic_contract": _contract(reference_cardinality="collection")}
    member = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "artifact", "left_handle": "artifact:order:10001"}},
        goal_ids={"g1"},
    )
    assert member["complete"] is True
    assert member["checks"][0]["reason_code"] == "resolved_singleton_collection_member_bound"
    assert member["checks"][0]["canonical_scope"] == "member:artifact:order:10001"

    parent = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "collection", "left_handle": "h_result:filtered-scope"}},
        goal_ids={"g1"},
    )
    assert parent["complete"] is True
    assert parent["checks"][0]["reason_code"] == "resolved_collection_reference_bound"

    wrong = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "artifact", "left_handle": "artifact:order:10002"}},
        goal_ids={"g1"},
    )
    assert wrong["complete"] is False
    assert wrong["checks"][0]["reason_code"] == "resolved_singleton_collection_target_mismatch"


def test_plural_collection_still_rejects_model_selected_member() -> None:
    from agent_core.runtime.capability_gate import _semantic_reference_binding_proof

    state = {"frozen_semantic_contract": _contract(
        reference_cardinality="collection",
        member_handles=("artifact:order:10001", "artifact:order:10002"),
    )}
    one_member = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "artifact", "left_handle": "artifact:order:10001"}},
        goal_ids={"g1"},
    )
    assert one_member["complete"] is False
    assert one_member["checks"][0]["reason_code"] == "resolved_collection_reference_target_mismatch"

    exact_collection = _semantic_reference_binding_proof(
        state,
        {"target": {"mode": "collection", "left_handle": "h_result:filtered-scope"}},
        goal_ids={"g1"},
    )
    assert exact_collection["complete"] is True
    assert exact_collection["checks"][0]["reason_code"] == "resolved_collection_reference_bound"
