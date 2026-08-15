from __future__ import annotations

import json

from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection
from agent_core.lifecycle.goal_granularity import (
    MUTUALLY_EXCLUSIVE_OUTCOME_REASON,
    MUTUALLY_EXCLUSIVE_OUTCOME_RULE,
    _build_inventory_authority,
    _evaluate_blind_inventory,
)


def _authority(*, user_text: str, outcome_spans: tuple[str, ...], reason_code: str) -> dict:
    return _build_inventory_authority(
        user_text=user_text,
        outcome_spans=outcome_spans,
        reason_code=reason_code,
        blind_self_audit_attempted=False,
    )


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_nested_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_nested_keys(item))
    return keys


def test_release50_mutually_exclusive_alternatives_are_one_resolution_outcome() -> None:
    user_text = "把订单取消掉，然后继续发货"
    authority = _authority(
        user_text=user_text,
        outcome_spans=(user_text,),
        reason_code=MUTUALLY_EXCLUSIVE_OUTCOME_REASON,
    )

    split = _evaluate_blind_inventory(
        user_text=user_text,
        goals=[
            {"goal_id": "g1", "evidence_span": "把订单取消掉"},
            {"goal_id": "g2", "evidence_span": "继续发货"},
        ],
        outcome_spans=(user_text,),
        authority=authority,
        authority_reused=False,
    )
    assert split.verdict == "over_split"
    assert split.reason_code == MUTUALLY_EXCLUSIVE_OUTCOME_REASON
    assert split.details["inventory_outcome_count"] == 1
    assert split.details["declared_goal_count"] == 2

    resolved = _evaluate_blind_inventory(
        user_text=user_text,
        goals=[{"goal_id": "g1", "evidence_span": user_text}],
        outcome_spans=(user_text,),
        authority=authority,
        authority_reused=True,
    )
    assert resolved.verdict == "exact"
    assert resolved.reason_code == MUTUALLY_EXCLUSIVE_OUTCOME_REASON
    assert resolved.details["inventory_outcome_count"] == 1
    assert resolved.details["declared_goal_count"] == 1


def test_release50_ordinary_independent_outcomes_remain_separate() -> None:
    user_text = "查一下我的订单，再查下物流到哪了"
    outcome_spans = ("查一下我的订单", "查下物流到哪了")
    authority = _authority(
        user_text=user_text,
        outcome_spans=outcome_spans,
        reason_code="blind_inventory_exact",
    )
    verdict = _evaluate_blind_inventory(
        user_text=user_text,
        goals=[
            {"goal_id": "g1", "evidence_span": "查一下我的订单"},
            {"goal_id": "g2", "evidence_span": "查下物流到哪了"},
        ],
        outcome_spans=outcome_spans,
        authority=authority,
        authority_reused=False,
    )
    assert verdict.verdict == "exact"
    assert verdict.details["inventory_outcome_count"] == 2
    assert verdict.details["declared_goal_count"] == 2


def test_release50_conflict_boundary_is_capability_independent() -> None:
    rule = MUTUALLY_EXCLUSIVE_OUTCOME_RULE.casefold()
    assert "same identified target" in rule
    assert "mutually exclusive" in rule
    assert "smallest single literal contiguous user_text span" in rule
    assert "never infer a conflict from tool/capability availability" in rule
    assert "effects on different identified targets" in rule


def test_release50_conflict_reason_reaches_writer_as_violation_only_evidence() -> None:
    raw = {
        "ok": False,
        "code": "GOAL_DECLARATION_OVER_SPLIT",
        "message": "redeclare",
        "data": {
            "errors": ["GOAL_DECLARATION_OVER_SPLIT"],
            "current_user_input": "把订单取消掉，然后继续发货",
            "repair_contract": {
                "authority": "current_user_input_only",
                "required_action": "redeclaration",
            },
            "granularity_proof": {
                "verdict": "over_split",
                "reason_code": MUTUALLY_EXCLUSIVE_OUTCOME_REASON,
                "findings": [
                    {
                        "goal_id": "g2",
                        "reason": "declared_goal_not_uniquely_mapped_to_blind_outcome",
                        "evidence_span": "继续发货",
                    }
                ],
                "details": {
                    "inventory_authority": {
                        "oracle_replacement_goal": "must_not_reach_writer",
                    }
                },
            },
            "independent_verifier_feedback": {
                "authority": "candidate_blind_goal_inventory",
                "required_action": "redeclaration",
                "recommended_role": "clarification",
                "replacement_goal": "must_not_reach_writer",
            },
        },
    }

    projected = _semantic_writer_declaration_result_projection(raw)
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["required_action"] == "redeclaration_from_current_user_input"
    assert feedback["violation"] == {
        "field": "goal_inventory",
        "reason_code": MUTUALLY_EXCLUSIVE_OUTCOME_REASON,
        "evidence_spans": ["继续发货"],
    }
    assert "rederive_semantics_from_current_user_input" in feedback["constraints"]
    leaked_keys = _nested_keys(projected)
    assert "oracle_replacement_goal" not in leaked_keys
    assert "recommended_role" not in leaked_keys
    assert "replacement_goal" not in leaked_keys
    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)
    assert "must_not_reach_writer" not in serialized
