from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services" / "agent-service" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.lifecycle.goal_dependency_proof import (  # noqa: E402
    DependencyGraphObservation,
    DependencyObservationRole,
    dependency_premise_digest,
    dependency_proof_metadata,
    reduce_dependency_graph_proof,
)
from agent_core.lifecycle.goal_planning import (  # noqa: E402
    GoalAlignmentVerdict,
    _alignment_authoritative_dependency_repair_contract,
    _alignment_repair_feedback,
    _goal_declaration_alignment_repair_context,
    _model_alignment_dependency_proof,
    validate_goal_declaration,
)
from agent_core.lifecycle.tool_execution_runtime import _tool_result_message  # noqa: E402


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


def _authoritative_details(
    *,
    user_text: str,
    goals: list[dict],
    details: dict,
    evidence_prefix: str,
) -> dict:
    premise = dependency_premise_digest(user_text=user_text, goals=goals)
    pairs = tuple(
        sorted(
            (
                str(row.get("dependent_goal_id") or ""),
                str(row.get("requires_result_of_goal_id") or ""),
            )
            for row in list(details.get("dependency_edges") or [])
            if isinstance(row, dict)
            and str(row.get("dependent_goal_id") or "")
            and str(row.get("requires_result_of_goal_id") or "")
        )
    )
    provisional = DependencyGraphObservation(
        premise_digest=premise,
        edges=pairs,
        complete=True,
        graph_matches_declaration=False,
        expected_pair_count=1,
        observed_pair_count=1,
        source="candidate_blind_dependency_reaudit",
        role=DependencyObservationRole.PROVISIONAL,
        evidence_digest=f"{evidence_prefix}-provisional",
    )
    proof = reduce_dependency_graph_proof(None, provisional)
    closure = DependencyGraphObservation(
        premise_digest=premise,
        edges=pairs,
        complete=True,
        graph_matches_declaration=False,
        expected_pair_count=1,
        observed_pair_count=1,
        source="candidate_blind_dependency_authority_closure",
        role=DependencyObservationRole.ADVERSARIAL_CLOSURE,
        evidence_digest=f"{evidence_prefix}-closure",
    )
    proof = reduce_dependency_graph_proof(proof, closure)
    return dependency_proof_metadata(details, proof)


class SemanticDependencyRepairFeedbackTests(unittest.TestCase):
    """Repair is derived only from mature, literal-evidence dependency authority."""

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

        # Parser-complete/matching output is observation, not writer authority.
        raw_verdict = GoalAlignmentVerdict(
            "incomplete",
            ("查一下键盘订单", "再看看它能不能退款"),
            (),
            "goal_alignment_dependency_graph_mismatch",
            "model",
            True,
            details,
        )
        self.assertEqual(_alignment_repair_feedback(raw_verdict), {})

        mature = _authoritative_details(
            user_text=text,
            goals=goals,
            details=details,
            evidence_prefix="positive",
        )
        feedback = _alignment_repair_feedback(
            GoalAlignmentVerdict(
                "incomplete",
                ("查一下键盘订单", "再看看它能不能退款"),
                (),
                "goal_alignment_dependency_graph_mismatch",
                "model",
                True,
                mature,
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
        self.assertEqual(
            _alignment_repair_feedback(
                GoalAlignmentVerdict(
                    "incomplete",
                    ("查一下鼠标订单", "帮我申请退款"),
                    (),
                    "goal_alignment_dependency_graph_mismatch",
                    "model",
                    True,
                    details,
                )
            ),
            {},
        )

        mature = _authoritative_details(
            user_text=text,
            goals=goals,
            details=details,
            evidence_prefix="negative",
        )
        feedback = _alignment_repair_feedback(
            GoalAlignmentVerdict(
                "incomplete",
                ("查一下鼠标订单", "帮我申请退款"),
                (),
                "goal_alignment_dependency_graph_mismatch",
                "model",
                True,
                mature,
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

    def test_authoritative_add_delta_roundtrips_without_reinferring_dependency(self) -> None:
        text = "查一下键盘订单，再看看它能不能退款"
        edge = {
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }
        original = [
            _goal("g1", "查一下键盘订单", []),
            _goal("g2", "再看看它能不能退款", []),
        ]
        details, error = _model_alignment_dependency_proof(
            user_text=text, goals=original, values=[edge]
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")
        mature = _authoritative_details(
            user_text=text,
            goals=original,
            details=details,
            evidence_prefix="roundtrip-add",
        )
        verdict = GoalAlignmentVerdict(
            "incomplete",
            tuple(row["evidence_span"] for row in original),
            (),
            "goal_alignment_dependency_graph_mismatch",
            "model",
            True,
            mature,
        )
        contract = _alignment_authoritative_dependency_repair_contract(verdict)[
            "authoritative_dependency_delta"
        ]
        self.assertEqual(contract["operations"], [{"operation": "ADD_DEPENDENCY", **edge}])

        repaired = [dict(row, depends_on=list(row["depends_on"])) for row in original]
        for operation in contract["operations"]:
            self.assertEqual(operation["operation"], "ADD_DEPENDENCY")
            target = next(row for row in repaired if row["goal_id"] == operation["dependent_goal_id"])
            target["depends_on"] = list(dict.fromkeys([
                *target["depends_on"], operation["requires_result_of_goal_id"]
            ]))

        self.assertEqual(
            dependency_premise_digest(user_text=text, goals=original),
            dependency_premise_digest(user_text=text, goals=repaired),
            "Planner depends_on must not change frozen semantic premise authority",
        )
        repaired_details, repaired_error = _model_alignment_dependency_proof(
            user_text=text, goals=repaired, values=[edge]
        )
        self.assertIsNone(repaired_error)
        self.assertTrue(repaired_details["dependency_graph_match"])

    def test_authoritative_remove_delta_roundtrips_to_verified_empty_graph(self) -> None:
        text = "查一下鼠标订单，然后帮我申请退款"
        original = [
            _goal("g1", "查一下鼠标订单", []),
            _goal("g2", "帮我申请退款", ["g1"]),
        ]
        details, error = _model_alignment_dependency_proof(
            user_text=text, goals=original, values=[]
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")
        mature = _authoritative_details(
            user_text=text,
            goals=original,
            details=details,
            evidence_prefix="roundtrip-remove",
        )
        verdict = GoalAlignmentVerdict(
            "incomplete",
            tuple(row["evidence_span"] for row in original),
            (),
            "goal_alignment_dependency_graph_mismatch",
            "model",
            True,
            mature,
        )
        context = _goal_declaration_alignment_repair_context(text, verdict)
        contract = context["repair_contract"]["authoritative_dependency_delta"]
        self.assertEqual(contract["operations"], [{
            "operation": "REMOVE_DEPENDENCY",
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
        }])

        repaired = [dict(row, depends_on=list(row["depends_on"])) for row in original]
        for operation in contract["operations"]:
            target = next(row for row in repaired if row["goal_id"] == operation["dependent_goal_id"])
            target["depends_on"] = [
                dep for dep in target["depends_on"]
                if dep != operation["requires_result_of_goal_id"]
            ]
        repaired_details, repaired_error = _model_alignment_dependency_proof(
            user_text=text, goals=repaired, values=[]
        )
        self.assertIsNone(repaired_error)
        self.assertTrue(repaired_details["dependency_graph_match"])

    def test_non_authoritative_mismatch_cannot_seal_dependency_repair_delta(self) -> None:
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
                "dependency_authority_state": "verified",
                "dependency_edges": [{
                    "dependent_goal_id": "g2",
                    "requires_result_of_goal_id": "g1",
                    "basis_kind": "result_reference",
                    "basis_span": "它",
                }],
                "declared_dependency_edges": [],
                "dependency_authority_premise_digest": "premise",
                "dependency_authority_evidence_digest": "evidence",
            },
        )
        self.assertEqual(_alignment_authoritative_dependency_repair_contract(verdict), {})

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
                "dependency_authority_state": "authoritative",
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
                "dependency_authority_state": "authoritative",
            },
        )
        merely_verified = GoalAlignmentVerdict(
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
                "dependency_edges": [],
                "declared_dependency_edges": [],
                "dependency_authority_state": "verified",
            },
        )

        self.assertEqual(_alignment_repair_feedback(incomplete), {})
        self.assertEqual(_alignment_repair_feedback(nonindependent), {})
        self.assertEqual(_alignment_repair_feedback(merely_verified), {})

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
        proof_goals = [
            _goal("g1", "查一下键盘订单", []),
            _goal("g2", "再看看它能不能退款", []),
        ]
        details, error = _model_alignment_dependency_proof(
            user_text=text,
            goals=proof_goals,
            values=[edge],
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")
        details = _authoritative_details(
            user_text=text,
            goals=proof_goals,
            details=details,
            evidence_prefix="transport",
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
