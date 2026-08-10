from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent-service"
SRC = AGENT / "src"
for path in (AGENT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _inventory(spans: list[str], edges: list[dict[str, str]], reason: str) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps({
        "verdict": "exact",
        "outcome_spans": spans,
        "dependency_edges": edges,
        "reason_code": reason,
    }, ensure_ascii=False)), {}


def _dependency(edges: list[dict[str, str]], reason: str) -> tuple[SimpleNamespace, dict]:
    return SimpleNamespace(content=json.dumps({
        "verdict": "exact",
        "dependency_edges": edges,
        "reason_code": reason,
    }, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {"goal_id": goal_id, "evidence_span": span, "depends_on": depends_on}


class Attempt3DependencyAuthorityTests(unittest.TestCase):
    def test_shared_literal_scope_dependency_is_removed_by_dedicated_blind_audit(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

        user_text = "查一下鼠标订单，然后帮我申请退款"
        spans = ["查一下鼠标订单", "帮我申请退款"]
        first_edge = [{
            "dependent_span": spans[1],
            "requires_result_of_span": spans[0],
        }]
        calls = [
            _inventory(spans, first_edge, "first_pass_execution_support_confusion"),
            _dependency([], "same_turn_literal_scope_not_result_dependency"),
        ]
        goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=calls
        ) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(verdict.verdict, "mixed")
        self.assertEqual(verdict.reason_code, "blind_inventory_dependency_graph_mismatch")
        self.assertEqual(verdict.details["dependency_edges"], [])
        self.assertTrue(verdict.details["dependency_basis_audited"])
        authority = verdict.details["inventory_authority"]
        self.assertEqual(authority["dependency_edge_basis"], [])

        repaired_goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], [])]
        with patch("agent_core.config.get_model", side_effect=AssertionError("frozen authority must avoid a new model call")):
            from agent_core.lifecycle.goal_granularity import verify_goal_granularity
            repaired = verify_goal_granularity(
                state={
                    "current_user_input": user_text,
                    "current_turn_plan": {"goal_granularity_inventory_authority": authority},
                },
                goals=repaired_goals,
            )
        self.assertTrue(repaired.exact)
        self.assertTrue(repaired.details["inventory_authority_reused"])

    def test_true_result_reference_dependency_requires_literal_basis_inside_dependent_outcome(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

        user_text = "查一下键盘订单，再看看它能不能退款"
        spans = ["查一下键盘订单", "它能不能退款"]
        edge = {
            "dependent_span": spans[1],
            "requires_result_of_span": spans[0],
        }
        calls = [
            _inventory(spans, [edge], "first_pass_result_dependency"),
            _dependency([{**edge, "basis_kind": "result_reference", "basis_span": "它"}], "result_reference_reaudited"),
        ]
        goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=calls
        ) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        self.assertEqual(invoke.call_count, 2)
        self.assertTrue(verdict.exact)
        self.assertEqual(verdict.details["dependency_edge_basis"][0]["basis_span"], "它")
        self.assertEqual(verdict.details["dependency_edge_basis"][0]["basis_kind"], "result_reference")

    def test_dependency_basis_outside_dependent_outcome_fails_closed(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

        user_text = "查一下键盘订单，再看看它能不能退款"
        spans = ["查一下键盘订单", "它能不能退款"]
        edge = {
            "dependent_span": spans[1],
            "requires_result_of_span": spans[0],
        }
        calls = [
            _inventory(spans, [edge], "first_pass"),
            _dependency([{**edge, "basis_kind": "result_reference", "basis_span": "键盘订单"}], "bad_basis"),
        ]
        goals = [_goal("g1", spans[0], []), _goal("g2", spans[1], ["g1"])]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=calls
        ):
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        self.assertEqual(verdict.verdict, "indeterminate")
        self.assertTrue(verdict.reason_code.startswith("blind_dependency_basis_span_not_in_dependent_outcome"))


class Attempt3SemanticTargetAuthorityTests(unittest.TestCase):
    @staticmethod
    def _semantic_goals() -> list[dict]:
        return [{
            "goal_id": "g1",
            "description": "核验它能否退款",
            "evidence_span": "它可以退货退款吗",
            "requested_effect": {
                "domain": "refund",
                "operation": "assess_eligibility",
                "object_type": "order",
            },
            "expected_result_cardinality": "single",
            "required": True,
            "depends_on": [],
            "reference_expression": {
                "reference_type": "temporal_visible_result",
                "temporal_relation": "latest",
                "expected_cardinality": "single",
                "evidence_span": "它",
            },
            "resolved_reference": {
                "result_ref": "h_result:latest-order",
                "member_handles": ["artifact:order:10002"],
                "proof_digest": "proof",
            },
        }]

    @staticmethod
    def _state() -> dict:
        return {
            "current_user_input": "它可以退货退款吗？先不要提交。",
            "current_turn_plan": {
                "effects": [{"effect_id": "effect:1", "goal_ids": ["g1"]}],
            },
        }

    def test_exact_frozen_member_target_becomes_deterministic_authority(self) -> None:
        from agent_core.runtime import semantic_capability_verifier as module

        with patch.object(module, "semantic_goals", return_value=self._semantic_goals()):
            proof = module._deterministic_historical_target_authority(
                self._state(),
                effect_id="effect:1",
                args={"target": {"mode": "artifact", "left_handle": "artifact:order:10002"}},
            )
        self.assertTrue(proof["historical_reference_binding_authoritative"])
        projected = module._project_candidate_arguments(
            {"target": {"mode": "artifact", "left_handle": "artifact:order:10002"}, "question_span": "退货退款吗"},
            proof,
        )
        self.assertEqual(projected["target"]["left_handle"], "<runtime-proven-opaque-reference>")
        self.assertEqual(projected["question_span"], "退货退款吗")

    def test_wider_or_wrong_target_does_not_receive_authority(self) -> None:
        from agent_core.runtime import semantic_capability_verifier as module

        with patch.object(module, "semantic_goals", return_value=self._semantic_goals()):
            all_orders = module._deterministic_historical_target_authority(
                self._state(), effect_id="effect:1", args={"target": {"mode": "all_orders"}}
            )
            wrong_member = module._deterministic_historical_target_authority(
                self._state(),
                effect_id="effect:1",
                args={"target": {"mode": "artifact", "left_handle": "artifact:order:99999"}},
            )
        self.assertFalse(all_orders["historical_reference_binding_authoritative"])
        self.assertFalse(wrong_member["historical_reference_binding_authoritative"])

    def test_target_only_second_model_rejudgment_is_ignored_but_effect_mismatch_stays_fail_closed(self) -> None:
        from agent_core.runtime.semantic_capability_verifier import (
            SemanticVerdict,
            _apply_deterministic_target_authority,
        )

        authority = {
            "historical_reference_binding_authoritative": True,
            "authority": "frozen_semantic_reference_plus_runtime_candidate_binding",
        }
        step_context = {"declared_goals": [{"evidence_span": "它可以退货退款吗"}]}
        target_only = SemanticVerdict(
            "unsupported",
            "它可以退货退款吗",
            "target_mismatch",
            "model",
            True,
            {"mismatch_dimensions": ["target"]},
        )
        corrected = _apply_deterministic_target_authority(
            target_only,
            user_text="它可以退货退款吗？先不要提交。",
            step_context=step_context,
            deterministic_target_authority=authority,
        )
        self.assertTrue(corrected.exact)
        self.assertTrue(corrected.details["runtime_target_authority_applied"])

        effect_mismatch = SemanticVerdict(
            "unsupported",
            "它可以退货退款吗",
            "different_business_effect",
            "model",
            True,
            {"mismatch_dimensions": ["effect"]},
        )
        still_blocked = _apply_deterministic_target_authority(
            effect_mismatch,
            user_text="它可以退货退款吗？先不要提交。",
            step_context=step_context,
            deterministic_target_authority=authority,
        )
        self.assertFalse(still_blocked.exact)
        self.assertEqual(still_blocked.verdict, "unsupported")

    def test_legacy_target_mismatch_reason_is_scoped_to_target_dimension_only(self) -> None:
        from agent_core.runtime.semantic_capability_verifier import _mismatch_dimensions

        self.assertEqual(
            _mismatch_dimensions({"verdict": "unsupported", "reason_code": "target_mismatch"}),
            ["target"],
        )
        self.assertEqual(
            _mismatch_dimensions({"verdict": "unsupported", "reason_code": "different_effect"}),
            ["other"],
        )


if __name__ == "__main__":
    unittest.main()
