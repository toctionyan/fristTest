from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services" / "agent-service" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.lifecycle.goal_planning import (  # noqa: E402
    GoalAlignmentVerdict,
    _alignment_repair_feedback,
    _model_alignment_dependency_proof,
)


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "evidence_span": span,
        "depends_on": list(depends_on),
    }


class SemanticDependencyRepairFeedbackTests(unittest.TestCase):
    def test_missing_true_result_dependency_returns_grounded_edge(self) -> None:
        text = "查一下键盘订单，再看看它能不能退款"
        goals = [
            _goal("g1", "查一下键盘订单", []),
            _goal("g2", "再看看它能不能退款", []),
        ]
        edge = {
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }
        details, error = _model_alignment_dependency_proof(
            user_text=text,
            goals=goals,
            values=[edge],
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")

        feedback = _alignment_repair_feedback(
            GoalAlignmentVerdict(
                "incomplete",
                ("查一下键盘订单", "再看看它能不能退款"),
                (),
                "goal_alignment_dependency_graph_mismatch",
                "model",
                True,
                details,
            )
        )["independent_verifier_feedback"]

        self.assertEqual(feedback["authority"], "independent_goal_alignment")
        self.assertEqual(
            feedback["required_action"],
            "redeclaration_preserving_grounded_dependency_graph",
        )
        self.assertEqual(feedback["dependency_edges"], [edge])
        self.assertEqual(feedback["candidate_declared_dependency_edges"], [])
        self.assertIn(
            "runtime_does_not_auto_rewrite_the_candidate",
            feedback["constraints"],
        )

    def test_false_candidate_dependency_returns_verified_empty_graph(self) -> None:
        text = "查一下鼠标订单，然后帮我申请退款"
        goals = [
            _goal("g1", "查一下鼠标订单", []),
            _goal("g2", "帮我申请退款", ["g1"]),
        ]
        details, error = _model_alignment_dependency_proof(
            user_text=text,
            goals=goals,
            values=[],
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")

        feedback = _alignment_repair_feedback(
            GoalAlignmentVerdict(
                "incomplete",
                ("查一下鼠标订单", "帮我申请退款"),
                (),
                "goal_alignment_dependency_graph_mismatch",
                "model",
                True,
                details,
            )
        )["independent_verifier_feedback"]

        self.assertEqual(feedback["dependency_edges"], [])
        self.assertEqual(
            feedback["candidate_declared_dependency_edges"],
            [{"dependent_goal_id": "g2", "requires_result_of_goal_id": "g1"}],
        )
        self.assertIn(
            "an_empty_verified_dependency_graph_requires_removing_unproved_candidate_edges",
            feedback["constraints"],
        )

    def test_incomplete_or_nonindependent_proof_never_becomes_repair_authority(self) -> None:
        incomplete = GoalAlignmentVerdict(
            "incomplete",
            ("查一下键盘订单", "再看看它能不能退款"),
            (),
            "goal_alignment_dependency_graph_mismatch",
            "model",
            True,
            {
                "dependency_authority": "independent_goal_alignment",
                "dependency_proof_complete": False,
                "dependency_graph_match": False,
                "dependency_edges": [],
                "declared_dependency_edges": [],
            },
        )
        nonindependent = GoalAlignmentVerdict(
            "incomplete",
            ("查一下键盘订单", "再看看它能不能退款"),
            (),
            "goal_alignment_dependency_graph_mismatch",
            "candidate_only",
            False,
            {
                "dependency_authority": "independent_goal_alignment",
                "dependency_proof_complete": True,
                "dependency_graph_match": False,
                "dependency_edges": [],
                "declared_dependency_edges": [],
            },
        )

        self.assertEqual(_alignment_repair_feedback(incomplete), {})
        self.assertEqual(_alignment_repair_feedback(nonindependent), {})

    def test_alignment_rejection_path_includes_grounded_feedback_hook(self) -> None:
        source = (
            ROOT
            / "services"
            / "agent-service"
            / "src"
            / "agent_core"
            / "lifecycle"
            / "goal_planning.py"
        ).read_text(encoding="utf-8")
        self.assertIn("**_alignment_repair_feedback(alignment)", source)
        self.assertIn("**_goal_declaration_repair_context(user_text)", source)


if __name__ == "__main__":
    unittest.main()
