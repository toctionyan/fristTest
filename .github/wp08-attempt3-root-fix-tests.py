from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "skill-system/tests/test_wp08_new_release_attempt3_root_fixes.py"

TEST.write_text(r'''from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent-service"
SRC = AGENT / "src"
for path in (AGENT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _goal(goal_id: str, span: str, depends_on: list[str], **extra):
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "depends_on": depends_on,
        **extra,
    }


def test_same_turn_literal_scope_bounds_false_blind_dependency_without_language_keywords() -> None:
    from agent_core.lifecycle.goal_granularity import _evaluate_blind_inventory

    user_text = "查一下鼠标订单，然后帮我申请退款"
    spans = ("查一下鼠标订单", "帮我申请退款")
    authority = {
        "reason_code": "blind_model_still_confused_support_lookup_with_dependency",
        "blind_self_audit_attempted": True,
    }
    goals = [
        _goal("g1", spans[0], []),
        _goal(
            "g2",
            spans[1],
            [],
            target_binding={
                "source": "same_turn_literal_scope",
                "source_goal_id": "g1",
                "evidence_span": "鼠标订单",
            },
            dependency_bindings=[],
        ),
    ]
    verdict = _evaluate_blind_inventory(
        user_text=user_text,
        goals=goals,
        outcome_spans=spans,
        dependency_edges=((1, 0),),
        authority=authority,
        authority_reused=True,
    )
    assert verdict.exact
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["raw_dependency_edges"] == [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]
    assert verdict.details["dependency_edges_suppressed_by_structured_scope_binding"] == [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]


def test_true_current_turn_goal_output_dependency_is_not_suppressed() -> None:
    from agent_core.lifecycle.goal_granularity import _evaluate_blind_inventory

    user_text = "查一下键盘订单，再看看它能不能退款"
    spans = ("查一下键盘订单", "它能不能退款")
    goals = [
        _goal("g1", spans[0], []),
        _goal(
            "g2",
            spans[1],
            ["g1"],
            target_binding={
                "source": "current_turn_goal_output",
                "source_goal_id": "g1",
                "evidence_span": "它",
            },
            dependency_bindings=[{
                "source_goal_id": "g1",
                "relation": "target",
                "evidence_span": "它",
            }],
        ),
    ]
    verdict = _evaluate_blind_inventory(
        user_text=user_text,
        goals=goals,
        outcome_spans=spans,
        dependency_edges=((1, 0),),
        authority={"reason_code": "true_dependency", "blind_self_audit_attempted": True},
        authority_reused=False,
    )
    assert verdict.exact
    assert verdict.details["dependency_edges"] == [{
        "dependent_span": spans[1],
        "requires_result_of_span": spans[0],
    }]
    assert verdict.details["dependency_edges_suppressed_by_structured_scope_binding"] == []


def test_dependency_grounding_rejects_result_dependency_for_reused_literal_scope() -> None:
    from agent_core.lifecycle.goal_planning import _normalize_current_turn_dependency_grounding

    goals = [
        _goal("g1", "查一下鼠标订单", []),
        _goal(
            "g2",
            "帮我申请退款",
            ["g1"],
            _raw_target_binding={
                "source": "same_turn_literal_scope",
                "source_goal_id": "g1",
                "evidence_span": "鼠标订单",
            },
            _raw_dependency_bindings=[],
        ),
    ]
    errors = _normalize_current_turn_dependency_grounding(
        goals,
        user_text="查一下鼠标订单，然后帮我申请退款",
    )
    assert "goal_dependency_basis_required:g2:g1" in errors
    assert "same_turn_literal_scope_cannot_be_result_dependency:g2:g1" in errors


def test_dependency_grounding_accepts_independent_reused_literal_scope() -> None:
    from agent_core.lifecycle.goal_planning import _normalize_current_turn_dependency_grounding

    goals = [
        _goal("g1", "查一下鼠标订单", []),
        _goal(
            "g2",
            "帮我申请退款",
            [],
            _raw_target_binding={
                "source": "same_turn_literal_scope",
                "source_goal_id": "g1",
                "evidence_span": "鼠标订单",
            },
            _raw_dependency_bindings=[],
        ),
    ]
    errors = _normalize_current_turn_dependency_grounding(
        goals,
        user_text="查一下鼠标订单，然后帮我申请退款",
    )
    assert errors == []
    assert goals[1]["target_binding"]["source"] == "same_turn_literal_scope"
    assert goals[1]["dependency_bindings"] == []


def test_dependency_grounding_accepts_true_goal_output_target_dependency() -> None:
    from agent_core.lifecycle.goal_planning import _normalize_current_turn_dependency_grounding

    goals = [
        _goal("g1", "查一下键盘订单", []),
        _goal(
            "g2",
            "它能不能退款",
            ["g1"],
            _raw_target_binding={
                "source": "current_turn_goal_output",
                "source_goal_id": "g1",
                "evidence_span": "它",
            },
            _raw_dependency_bindings=[{
                "source_goal_id": "g1",
                "relation": "target",
                "evidence_span": "它",
            }],
        ),
    ]
    errors = _normalize_current_turn_dependency_grounding(
        goals,
        user_text="查一下键盘订单，再看看它能不能退款",
    )
    assert errors == []
    assert goals[1]["target_binding"]["source"] == "current_turn_goal_output"


def test_runtime_target_authority_redacts_opaque_handle_and_overrides_target_only_rejudgment() -> None:
    from agent_core.runtime.semantic_capability_verifier import (
        SemanticVerdict,
        _apply_deterministic_target_authority,
        _project_candidate_arguments,
    )

    proof = {
        "historical_reference_binding_authoritative": True,
        "authority": "capability_gate_target_binding_only",
    }
    projected = _project_candidate_arguments(
        {
            "target": {"mode": "artifact", "left_handle": "h_order:opaque"},
            "reference_span": "它",
            "question_span": "它可以退货退款吗",
        },
        proof,
    )
    assert projected["target"] == {
        "mode": "artifact",
        "left_handle": "<runtime-proven-opaque-handle>",
    }
    verdict = _apply_deterministic_target_authority(
        SemanticVerdict(
            "unsupported",
            "",
            "target_mismatch",
            "model",
            True,
            {"mismatch_dimensions": ["target"]},
        ),
        user_text="它可以退货退款吗？先不要提交。",
        deterministic_target_proof=proof,
        step_context={"declared_goals": [{"evidence_span": "它可以退货退款吗"}]},
    )
    assert verdict.exact
    assert verdict.evidence_span == "它可以退货退款吗"
    assert verdict.details["runtime_target_authority_applied"] is True


def test_runtime_target_authority_does_not_hide_condition_or_effect_mismatch() -> None:
    from agent_core.runtime.semantic_capability_verifier import SemanticVerdict, _apply_deterministic_target_authority

    original = SemanticVerdict(
        "unsupported",
        "它可以退货退款吗",
        "condition_mismatch",
        "model",
        True,
        {"mismatch_dimensions": ["target", "condition"]},
    )
    result = _apply_deterministic_target_authority(
        original,
        user_text="它可以退货退款吗？先不要提交。",
        deterministic_target_proof={"historical_reference_binding_authoritative": True},
        step_context={"declared_goals": [{"evidence_span": "它可以退货退款吗"}]},
    )
    assert result is original
    assert result.verdict == "unsupported"


def test_capability_gate_marks_historical_binding_authoritative_only_after_all_deterministic_proofs() -> None:
    from agent_core.runtime.capability_gate import _deterministic_semantic_target_proof

    complete = _deterministic_semantic_target_proof(
        normalized_args={"target": {"mode": "artifact", "left_handle": "h_order:opaque"}},
        visible_reference={"complete": True},
        semantic_reference_binding={
            "complete": True,
            "checks": [{
                "goal_id": "g1",
                "required": True,
                "matched": True,
                "reason_code": "resolved_single_reference_member_bound",
                "expected_cardinality": "single",
            }],
        },
        member_scope={"complete": True},
        derived_scope={"complete": True},
    )
    assert complete["historical_reference_binding_authoritative"] is True
    assert complete["opaque_handle_identity_exposed_to_semantic_model"] is False

    incomplete = _deterministic_semantic_target_proof(
        normalized_args={"target": {"mode": "artifact", "left_handle": "h_order:opaque"}},
        visible_reference={"complete": True},
        semantic_reference_binding={"complete": False, "checks": [{"required": True}]},
        member_scope={"complete": True},
        derived_scope={"complete": True},
    )
    assert incomplete["historical_reference_binding_authoritative"] is False


def test_frozen_semantics_retains_structured_dependency_evidence() -> None:
    source = (SRC / "agent_core/lifecycle/semantic_contract.py").read_text(encoding="utf-8")
    assert '"target_binding"' in source
    assert '"dependency_bindings"' in source


def test_attempt3_repair_does_not_weaken_unsupported_or_release_envelopes() -> None:
    effects = (SRC / "agent_core/runtime/capability_effects.py").read_text(encoding="utf-8")
    dialogue = (SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    smoke = (AGENT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
    config = (SRC / "agent_core/config.py").read_text(encoding="utf-8")
    browser = (AGENT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
    assert 'status = "exact_supported"' in effects
    assert 'status = "absent_proven"' in effects
    assert 'execution_kind == "unsupported"' in effects
    assert "same_turn_literal_scope" in dialogue
    assert "current_turn_goal_output" in dialogue
    assert 'model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")' in smoke
    assert '_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0' in config
    assert '_bounded_int_env("MODEL_MAX_RETRIES", 1' in config
    assert '{ timeout: 120_000 }' in browser
''', encoding="utf-8")
print(f"published {TEST}")
