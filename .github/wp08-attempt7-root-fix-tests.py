#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()
TEST = ROOT / "skill-system/tests/test_wp08_attempt7_root_fixes.py"

TEST.write_text(r'''from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for path in (AGENT_ROOT, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_smoke():
    path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_attempt7_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {"goal_id": goal_id, "evidence_span": span, "depends_on": depends_on}


def test_frozen_candidate_blind_authority_is_reused_without_reinvoking_model() -> None:
    from agent_core.lifecycle.goal_granularity import (
        _build_inventory_authority,
        verify_goal_granularity,
    )

    user_text = "查一下我的订单，再查下物流到哪了"
    spans = ("查一下我的订单", "再查下物流到哪了")
    authority = _build_inventory_authority(
        user_text=user_text,
        outcome_spans=spans,
        dependency_edges=(),
        reason_code="shared_scope_independent",
        blind_self_audit_attempted=True,
    )
    state = {
        "current_user_input": user_text,
        "current_turn_plan": {"goal_granularity_inventory_authority": authority},
    }
    goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], [])]
    with patch(
        "agent_core.lifecycle.goal_granularity.ModelGoalGranularityVerifier.verify",
        side_effect=AssertionError("frozen authority must avoid another blind model call"),
    ):
        verdict = verify_goal_granularity(state=state, goals=goals)
    assert verdict.exact
    assert verdict.details["inventory_authority_reused"] is True
    assert verdict.details["inventory_authority"]["integrity_digest"] == authority["integrity_digest"]


def test_same_frozen_authority_rejects_old_candidate_then_accepts_repair() -> None:
    from agent_core.lifecycle.goal_granularity import (
        _build_inventory_authority,
        verify_goal_granularity,
    )

    user_text = "查一下我的订单，再查下物流到哪了"
    spans = ("查一下我的订单", "再查下物流到哪了")
    authority = _build_inventory_authority(
        user_text=user_text,
        outcome_spans=spans,
        dependency_edges=(),
        reason_code="shared_scope_independent",
        blind_self_audit_attempted=True,
    )
    state = {"current_user_input": user_text, "current_turn_plan": {"goal_granularity_inventory_authority": authority}}
    wrong = verify_goal_granularity(
        state=state,
        goals=[_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])],
    )
    assert wrong.verdict == "mixed"
    assert wrong.reason_code == "blind_inventory_dependency_graph_mismatch"
    correct = verify_goal_granularity(
        state=state,
        goals=[_goal("g1", spans[0], []), _goal("g2", spans[1], [])],
    )
    assert correct.exact
    assert correct.details["inventory_authority"]["integrity_digest"] == authority["integrity_digest"]


def test_true_pronoun_result_dependency_remains_required() -> None:
    from agent_core.lifecycle.goal_granularity import _build_inventory_authority, verify_goal_granularity

    user_text = "查一下键盘订单，再看看它能不能退款"
    spans = ("查一下键盘订单", "它能不能退款")
    authority = _build_inventory_authority(
        user_text=user_text,
        outcome_spans=spans,
        dependency_edges=((1, 0),),
        reason_code="pronoun_requires_current_turn_result",
        blind_self_audit_attempted=False,
    )
    state = {"current_user_input": user_text, "current_turn_plan": {"goal_granularity_inventory_authority": authority}}
    assert verify_goal_granularity(
        state=state,
        goals=[_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])],
    ).exact
    rejected = verify_goal_granularity(
        state=state,
        goals=[_goal("g1", spans[0], []), _goal("g2", spans[1], [])],
    )
    assert rejected.verdict == "mixed"


def test_inventory_authority_fails_closed_on_tamper_and_cross_turn_text() -> None:
    from agent_core.lifecycle.goal_granularity import (
        _build_inventory_authority,
        _validate_inventory_authority,
        verify_goal_granularity,
    )

    authority = _build_inventory_authority(
        user_text="查订单",
        outcome_spans=("查订单",),
        dependency_edges=(),
        reason_code="one_outcome",
        blind_self_audit_attempted=False,
    )
    tampered = dict(authority)
    tampered["outcome_spans"] = ["查订单", "伪造"]
    _, _, _, error = _validate_inventory_authority(user_text="查订单", authority=tampered)
    assert error == "goal_granularity_inventory_authority_digest_invalid"

    state = {"current_user_input": "查物流", "current_turn_plan": {"goal_granularity_inventory_authority": authority}}
    verdict = verify_goal_granularity(state=state, goals=[_goal("g1", "查物流", [])])
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_granularity_inventory_authority_user_text_mismatch"


def test_runtime_persists_rejected_blind_authority_in_turn_plan() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/tool_execution_runtime.py").read_text(encoding="utf-8")
    assert '"goal_granularity_inventory_authority": deepcopy(inventory_authority)' in source
    dialogue = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    assert 'prior = state.get("current_turn_plan")' in dialogue
    assert 'key not in {"tool_calls", "raw_model_content", "loop_step", "iteration_id", "user_text"}' in dialogue


def test_preprod_semantic_repair_reuses_the_same_runtime_authority() -> None:
    smoke = _load_smoke()
    authority = {"version": "test-authority", "integrity_digest": "test"}
    captured = {}

    def fake_validate_goal_declaration(*, state, args, capability_registry):
        captured["state"] = state
        captured["args"] = args
        captured["capability_registry"] = capability_registry
        return {"ok": False, "code": "TEST"}, None

    with patch.object(smoke, "get_runtime_registry", return_value=SimpleNamespace(capabilities=object())), patch.object(
        smoke, "validate_goal_declaration", side_effect=fake_validate_goal_declaration
    ):
        smoke._production_goal_declaration_evaluation(
            user_text="查订单",
            goals=[{"goal_id": "g1"}],
            inventory_authority=authority,
        )
    assert captured["state"]["current_turn_plan"]["goal_granularity_inventory_authority"] == authority


def _catalog() -> dict:
    return json.loads((AGENT_ROOT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json").read_text(encoding="utf-8"))


def _case(case_id: str) -> dict:
    return next(row for row in _catalog()["cases"] if row["id"] == case_id)


def _turn(case_id: str) -> dict:
    return _case(case_id)["execution_contract"]["turn_contracts"][0]


def _scripted_goals(turn: dict) -> list[dict]:
    call = next(
        call
        for call in turn["model_steps"][0]["tool_calls"]
        if call["name"] == "declare_turn_goals"
    )
    return call["args"]["goals"]


def test_shared_scope_ellipsis_oracles_are_independent_not_execution_dataflow() -> None:
    for case_id in (
        "semantic_multi_orders_logistics",
        "semantic_query_then_refund_draft",
        "semantic_order_detail_and_invoice",
    ):
        turn = _turn(case_id)
        assert turn["goal_oracle"][1]["depends_on"] == []
        assert _scripted_goals(turn)[1]["depends_on"] == []


def test_true_result_reference_oracle_is_still_dependent() -> None:
    turn = _turn("semantic_query_then_refund_consult")
    assert turn["goal_oracle"][1]["evidence_span"] == "它能不能退款"
    assert turn["goal_oracle"][1]["depends_on"] == ["g1"]
    assert _scripted_goals(turn)[1]["depends_on"] == ["g1"]


def test_multi_target_write_keeps_target_resolution_as_support_step_not_user_goal() -> None:
    turn = _turn("semantic_multi_target_cancel_boundary")
    assert len(turn["goal_oracle"]) == 1
    goal = turn["goal_oracle"][0]
    assert goal["oracle_id"] == "g1"
    assert goal["evidence_span"] == "把这些订单都取消"
    assert goal["requested_effect"]["operation"] == "cancel"
    assert goal["depends_on"] == []
    scripted = _scripted_goals(turn)
    assert len(scripted) == 1
    assert scripted[0]["goal_id"] == "g1"
    assert scripted[0]["depends_on"] == []
    list_call = next(
        call
        for step in turn["model_steps"]
        for call in step["tool_calls"]
        if call["name"] == "list_orders"
    )
    assert list_call["args"]["goal_ids"] == ["g1"]
    assert turn["expected"]["goal_count"] == 1


def test_shared_scope_ellipsis_rule_is_consistent_across_semantic_surfaces() -> None:
    granularity = (AGENT_SRC / "agent_core/lifecycle/goal_granularity.py").read_text(encoding="utf-8")
    alignment = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    dialogue = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    protocol = (AGENT_SRC / "agent_core/lifecycle/protocol.py").read_text(encoding="utf-8")
    assert "inherit that stated scope as ellipsis" in granularity
    assert "inherit that stated scope as ellipsis" in alignment
    assert "后文只是省略重复对象" in dialogue
    assert "后文只省略重复对象" in protocol


def test_dependency_diagnostic_retains_blind_edges_and_reuse_state() -> None:
    smoke = _load_smoke()
    result = {
        "code": "GOAL_DECLARATION_GRANULARITY_MIXED",
        "data": {
            "granularity_proof": {
                "verdict": "mixed",
                "reason_code": "blind_inventory_dependency_graph_mismatch",
                "details": {
                    "inventory_outcome_count": 2,
                    "declared_goal_count": 2,
                    "matched_outcome_count": 2,
                    "outcome_spans": ["查订单", "查物流"],
                    "dependency_edges": [{"dependent_span": "查物流", "requires_result_of_span": "查订单"}],
                    "dependency_graph_match": False,
                    "inventory_authority_reused": True,
                    "blind_self_audit_attempted": True,
                },
            },
            "independent_verifier_feedback": {
                "authority": "candidate_blind_goal_inventory",
                "dependency_edges": [{"dependent_span": "查物流", "requires_result_of_span": "查订单"}],
            },
        },
    }
    diagnostic = smoke._sanitized_goal_rejection_diagnostic(result)
    assert diagnostic["granularity"]["dependency_edges"] == [{
        "dependent_span": "查物流",
        "requires_result_of_span": "查订单",
    }]
    assert diagnostic["granularity"]["inventory_authority_reused"] is True
    assert diagnostic["independent_verifier_feedback"]["dependency_edges"]


def test_final_attempt_safety_envelopes_are_not_weakened() -> None:
    smoke = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
    config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
    assert 'model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")' in smoke
    assert "{ timeout: 120_000 }" in browser
    assert '_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0' in config
    assert '_bounded_int_env("MODEL_MAX_RETRIES", 1' in config
''', encoding="utf-8")
print(TEST)
