from __future__ import annotations

import json
from pathlib import Path
import string
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services" / "agent-service"
AGENT_SRC = AGENT_ROOT / "src"
SCRIPTS = ROOT / "scripts"
for value in (AGENT_ROOT, AGENT_SRC, SCRIPTS):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, *, depends_on: list[str], effect: dict) -> dict:
    return {
        "goal_id": goal_id,
        "description": span,
        "evidence_span": span,
        "requested_effect": {**effect, "raw_description": span},
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": list(depends_on),
    }


class WP08FinalProductClosureRepairTests(unittest.TestCase):
    def test_attempt8_scope_false_positive_gets_candidate_blind_reaudit(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

        text = "查一下键盘订单，再看看它能不能退款"
        goals = [
            _goal(
                "g1",
                "查一下键盘订单",
                depends_on=[],
                effect={"domain": "order", "operation": "list", "object_type": "order"},
            ),
            _goal(
                "g2",
                "再看看它能不能退款",
                depends_on=["g1"],
                effect={"domain": "refund", "operation": "assess_eligibility", "object_type": "order"},
            ),
        ]
        edge = {
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }
        decision = {
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }
        first = _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_edges": [edge],
            "reason_code": "all_outcomes_preserved",
        })
        false_scope_claim = _response({
            "verdict": "incomplete",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": ["键盘订单", "它"],
            "dependency_decisions": [decision],
            "reason_code": "target-scope-constraint coverage",
        })
        corrected = _response({
            "verdict": "exact",
            "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
            "missing_spans": [],
            "dependency_decisions": [decision],
            "reason_code": "target_identity_and_result_reference_are_not_scope_constraints",
        })
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=[first, false_scope_claim, corrected]
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text=text,
                goals=goals,
                known_tools=set(),
            )

        self.assertEqual(invoke.call_count, 3)
        self.assertTrue(verdict.exact)
        self.assertTrue(verdict.details["dependency_graph_match"])
        self.assertEqual(verdict.details["dependency_edges"], [edge])
        self.assertEqual(
            verdict.details["verifier_repair_kind"],
            "candidate_blind_dependency_scope_constraint_reaudit",
        )
        repair_message = invoke.call_args_list[2].kwargs["payload"][-1].content
        self.assertIn("Object identity, member naming, ordinary target selection", repair_message)
        self.assertIn("current-turn Goal result", repair_message)
        self.assertIn("remain incomplete", repair_message)

    def test_real_population_narrowing_stays_fail_closed_after_reaudit(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

        text = "查一下还在路上的订单"
        goals = [
            _goal(
                "g1",
                text,
                depends_on=[],
                effect={"domain": "order", "operation": "list", "object_type": "order"},
            )
        ]
        first = _response({
            "verdict": "exact",
            "evidence_spans": [text],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "outcome_preserved",
        })
        omitted_filter = _response({
            "verdict": "incomplete",
            "evidence_spans": [text],
            "missing_spans": ["还在路上"],
            "dependency_decisions": [],
            "reason_code": "target-scope-constraint coverage",
        })
        confirmed_filter = _response({
            "verdict": "incomplete",
            "evidence_spans": [text],
            "missing_spans": ["还在路上"],
            "dependency_decisions": [],
            "reason_code": "target-scope-constraint coverage",
        })
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=[first, omitted_filter, confirmed_filter]
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text=text,
                goals=goals,
                known_tools=set(),
            )

        self.assertEqual(invoke.call_count, 3)
        self.assertEqual(verdict.verdict, "incomplete")
        self.assertEqual(verdict.missing_spans, ("还在路上",))
        self.assertEqual(
            verdict.details["verifier_repair_kind"],
            "candidate_blind_dependency_scope_constraint_reaudit",
        )

    def test_non_scope_semantic_mismatch_does_not_gain_extra_reaudit(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

        text = "查一下键盘订单"
        goals = [
            _goal(
                "g1",
                text,
                depends_on=[],
                effect={"domain": "order", "operation": "list", "object_type": "order"},
            )
        ]
        first = _response({
            "verdict": "exact",
            "evidence_spans": [text],
            "missing_spans": [],
            "dependency_edges": [],
            "reason_code": "outcome_preserved",
        })
        effect_mismatch = _response({
            "verdict": "incomplete",
            "evidence_spans": [text],
            "missing_spans": [text],
            "dependency_decisions": [],
            "reason_code": "requested-effect fidelity",
        })
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=[first, effect_mismatch]
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text=text,
                goals=goals,
                known_tools=set(),
            )

        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(verdict.verdict, "incomplete")
        self.assertNotEqual(
            verdict.details.get("verifier_repair_kind"),
            "candidate_blind_dependency_scope_constraint_reaudit",
        )

    def test_protected_ephemeral_secrets_cannot_match_production_banned_words(self) -> None:
        from verify_full_lifecycle_canary import ProductRuntimeHarness

        harness = ProductRuntimeHarness(
            deterministic_model=True,
            persistence_url="postgresql://test:test@127.0.0.1:5432/test",
            protected_preprod=True,
            allowed_origins="http://127.0.0.1:3000",
        )
        try:
            for secret in (harness.jwt_secret, harness.actor_signing_secret):
                self.assertEqual(len(secret), 64)
                self.assertTrue(set(secret) <= set(string.hexdigits.lower()))
                self.assertNotIn("dev", secret.lower())
                self.assertNotIn("change", secret.lower())
            self.assertEqual(harness.env["AGENT_JWT_SECRET"], harness.jwt_secret)
            self.assertEqual(harness.env["BUSINESS_ACTOR_SIGNING_SECRET"], harness.actor_signing_secret)
        finally:
            harness.stop()

    def test_production_secret_validation_remains_fail_closed(self) -> None:
        source = (
            AGENT_SRC / "agent_core" / "config.py"
        ).read_text(encoding="utf-8")
        self.assertIn("AGENT_JWT_SECRET must be a strong non-default secret", source)
        self.assertIn('or "dev" in secret.lower()', source)
        self.assertIn('or "change" in secret.lower()', source)
        self.assertIn("BUSINESS_ACTOR_SIGNING_SECRET must be a strong non-default secret", source)


if __name__ == "__main__":
    unittest.main()
