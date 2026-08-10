from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.lifecycle.goal_planning import _model_alignment_pairwise_dependency_proof
from agent_core.lifecycle.dialogue_runtime import (
    GOAL_DECLARATION_MAX_RETRIES,
    _goal_declaration_protocol_repair_rule,
)


def _goals(*, with_dependency: bool) -> list[dict]:
    return [
        {
            "goal_id": "g1",
            "evidence_span": "find the order",
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "evidence_span": "assess that result",
            "depends_on": ["g1"] if with_dependency else [],
        },
    ]


class PairwiseDependencyProofTests(unittest.TestCase):
    def test_pairwise_audit_cannot_prove_multi_goal_graph_with_empty_decisions(self) -> None:
        details, error = _model_alignment_pairwise_dependency_proof(
            user_text="find the order, then assess that result",
            goals=_goals(with_dependency=False),
            values=[],
        )
        self.assertEqual(error, "goal_alignment_dependency_pair_coverage_incomplete")
        self.assertFalse(details["dependency_proof_complete"])
        self.assertEqual(details["missing_dependency_pairs"], [["g1", "g2"]])

    def test_pairwise_audit_exposes_grounded_edge_when_candidate_omits_it(self) -> None:
        details, error = _model_alignment_pairwise_dependency_proof(
            user_text="find the order, then assess that result",
            goals=_goals(with_dependency=False),
            values=[{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "that result",
            }],
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")
        self.assertTrue(details["dependency_proof_complete"])
        self.assertFalse(details["dependency_graph_match"])
        self.assertEqual(details["dependency_edges"], [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "that result",
        }])

    def test_pairwise_audit_accepts_same_grounded_edge_when_candidate_declares_it(self) -> None:
        details, error = _model_alignment_pairwise_dependency_proof(
            user_text="find the order, then assess that result",
            goals=_goals(with_dependency=True),
            values=[{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "that result",
            }],
        )
        self.assertIsNone(error)
        self.assertTrue(details["dependency_proof_complete"])
        self.assertTrue(details["dependency_graph_match"])


class GoalDeclarationProtocolRepairTests(unittest.TestCase):
    def test_no_tool_planning_retry_has_explicit_tool_only_feedback(self) -> None:
        rule = _goal_declaration_protocol_repair_rule({"status": "GoalDeclarationProtocolRetry"})
        self.assertIsNotNone(rule)
        self.assertIn("declare_turn_goals", rule or "")
        self.assertIn("纯文本", rule or "")
        self.assertIn("必须只调用一次", rule or "")
        self.assertEqual(GOAL_DECLARATION_MAX_RETRIES, 2)

    def test_unrelated_status_does_not_invent_repair_semantics(self) -> None:
        self.assertIsNone(_goal_declaration_protocol_repair_rule({"status": "GroundedFinalAnswer"}))

    def test_repair_is_protocol_generic_not_invoice_or_recall_keyword_logic(self) -> None:
        source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        helper = source.split("def _goal_declaration_protocol_repair_rule", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("开票", helper)
        self.assertNotIn("刚才", helper)
        self.assertNotIn("10004", helper)


if __name__ == "__main__":
    unittest.main()
