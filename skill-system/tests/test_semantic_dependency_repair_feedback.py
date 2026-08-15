from __future__ import annotations

import json
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
    validate_goal_declaration,
)
from agent_core.lifecycle.tool_execution_runtime import _tool_result_message  # noqa: E402
from agent_core.goal_graph.dependency_alignment import (  # noqa: E402
    alignment_dependency_authority_details,
    apply_alignment_dependency_proof,
)


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {
        "goal_id": goal_id,
        "evidence_span": span,
        "depends_on": list(depends_on),
    }


def _declared_goal(
    goal_id: str,
    *,
    description: str,
    span: str,
    domain: str,
    operation: str,
    depends_on: list[str],
) -> dict:
    return {
        "goal_id": goal_id,
        "description": description,
        "evidence_span": span,
        "requested_effect": {
            "domain": domain,
            "operation": operation,
            "object_type": "order",
            "raw_description": description,
        },
        "expected_result_cardinality": "unknown",
        "required": True,
        "depends_on": list(depends_on),
    }


class _InjectedVerifier:
    def __init__(self, verdict: GoalAlignmentVerdict) -> None:
        self.verdict = verdict

    def verify(self, **_kwargs):
        return self.verdict


def _close_dependency_authority(
    *,
    user_text: str,
    goals: list[dict],
    legacy_details: dict,
    decisions: list[dict],
) -> dict:
    ledger, _ = apply_alignment_dependency_proof(
        None,
        user_text=user_text,
        goals=goals,
        details={
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": legacy_details.get("dependency_graph_match"),
            "dependency_pair_decisions": decisions,
        },
        phase="candidate_blind_dependency_authority_closure",
    )
    return {
        **legacy_details,
        **alignment_dependency_authority_details(ledger, goals=goals),
    }


class SemanticDependencyRepairFeedbackTests(unittest.TestCase):
    """Lock repair feedback to independent, literal-evidence dependency proof only."""

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
        details = _close_dependency_authority(
            user_text=text,
            goals=goals,
            legacy_details=details,
            decisions=[{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "它",
            }],
        )

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
        details = _close_dependency_authority(
            user_text=text,
            goals=goals,
            legacy_details=details,
            decisions=[{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "independent",
            }],
        )

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

    def test_raw_complete_matching_dependency_proof_is_not_repair_authority(self) -> None:
        verdict = GoalAlignmentVerdict(
            "incomplete",
            ("查一下键盘订单", "再看看它能不能退款"),
            (),
            "goal_alignment_dependency_graph_mismatch",
            "model",
            True,
            {
                "dependency_authority": "independent_goal_alignment",
                "dependency_proof_complete": True,
                "dependency_graph_match": False,
                "dependency_edges": [{
                    "dependent_goal_id": "g2",
                    "requires_result_of_goal_id": "g1",
                    "basis_kind": "result_reference",
                    "basis_span": "它",
                }],
                "declared_dependency_edges": [],
            },
        )
        self.assertEqual(_alignment_repair_feedback(verdict), {})

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

    def test_validated_dependency_rejection_reaches_next_model_as_tool_message(self) -> None:
        text = "查一下键盘订单，再看看它能不能退款"
        edge = {
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }
        details, error = _model_alignment_dependency_proof(
            user_text=text,
            goals=[
                _goal("g1", "查一下键盘订单", []),
                _goal("g2", "再看看它能不能退款", []),
            ],
            values=[edge],
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")
        proof_goals = [
            _goal("g1", "查一下键盘订单", []),
            _goal("g2", "再看看它能不能退款", []),
        ]
        details = _close_dependency_authority(
            user_text=text,
            goals=proof_goals,
            legacy_details=details,
            decisions=[{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "它",
            }],
        )
        state = {
            "current_user_input": text,
            "goal_alignment_verifier": _InjectedVerifier(
                GoalAlignmentVerdict(
                    "incomplete",
                    ("查一下键盘订单", "再看看它能不能退款"),
                    (),
                    "goal_alignment_dependency_graph_mismatch",
                    "injected",
                    True,
                    details,
                )
            ),
        }
        args = {
            "goals": [
                _declared_goal(
                    "g1",
                    description="查询键盘订单",
                    span="查一下键盘订单",
                    domain="order",
                    operation="list",
                    depends_on=[],
                ),
                _declared_goal(
                    "g2",
                    description="判断该查询结果的退款资格",
                    span="再看看它能不能退款",
                    domain="refund",
                    operation="assess_eligibility",
                    depends_on=[],
                ),
            ]
        }

        result, declared = validate_goal_declaration(
            state=state,
            args=args,
            capability_registry=None,  # type: ignore[arg-type]
        )
        self.assertIsNone(declared)
        self.assertEqual(result["code"], "GOAL_DECLARATION_INCOMPLETE")
        feedback = result["data"]["independent_verifier_feedback"]
        self.assertEqual(feedback["dependency_edges"], [edge])
        self.assertEqual(feedback["candidate_declared_dependency_edges"], [])
        self.assertEqual(result["data"]["current_user_input"], text)

        message = _tool_result_message(
            {"id": "call-declare", "name": "declare_turn_goals"},
            result,
        )
        self.assertIsNotNone(message)
        payload = json.loads(message.content)
        self.assertEqual(payload["code"], "GOAL_DECLARATION_INCOMPLETE")
        self.assertEqual(
            payload["data"]["independent_verifier_feedback"]["dependency_edges"],
            [edge],
        )
        self.assertIn(
            "runtime_does_not_auto_rewrite_the_candidate",
            payload["data"]["independent_verifier_feedback"]["constraints"],
        )


if __name__ == "__main__":
    unittest.main()
