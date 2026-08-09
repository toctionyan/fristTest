from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for path in (AGENT_ROOT, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_smoke():
    path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_attempt5_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Attempt5RepairTests(unittest.TestCase):
    def test_blind_inventory_self_audits_false_extra_outcome_without_candidate_disclosure(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
        user_text = "查一下鼠标物流，再告诉我快递员手机号"
        goals = [{"goal_id": "g1", "evidence_span": "查一下鼠标物流"}, {"goal_id": "g2", "evidence_span": "快递员手机号"}]
        responses = [
            (SimpleNamespace(content=json.dumps({"verdict": "exact", "outcome_spans": ["查一下鼠标物流", "告诉我快递员手机号", "快递员手机号"], "dependency_edges": [], "reason_code": "first_inventory"}, ensure_ascii=False)), {}),
            (SimpleNamespace(content=json.dumps({"verdict": "exact", "outcome_spans": ["查一下鼠标物流", "快递员手机号"], "dependency_edges": [], "reason_code": "self_audited_inventory"}, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=responses) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        second_prompt = invoke.call_args_list[1].kwargs["payload"][-1].content
        self.assertNotIn("DECLARED_GOALS", second_prompt)
        self.assertNotIn("g1", second_prompt)
        self.assertNotIn("g2", second_prompt)
        self.assertIn("candidate-blind", second_prompt)

    def test_real_under_split_remains_fail_closed_after_blind_self_audit(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
        user_text = "查一下鼠标物流，再告诉我快递员手机号"
        goals = [{"goal_id": "g1", "evidence_span": "查一下鼠标物流"}]
        response = SimpleNamespace(content=json.dumps({"verdict": "exact", "outcome_spans": ["查一下鼠标物流", "快递员手机号"], "dependency_edges": [], "reason_code": "two_independent_outcomes"}, ensure_ascii=False))
        with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=[(response, {}), (response, {})]) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        self.assertEqual(verdict.verdict, "under_split")
        self.assertEqual(invoke.call_count, 2)
        uncovered = [row.get("evidence_span") for row in verdict.findings if row.get("reason") == "blind_inventory_outcome_not_covered"]
        self.assertEqual(uncovered, ["快递员手机号"])
        self.assertTrue(verdict.details["blind_self_audit_attempted"])

    def test_granularity_clarify_gets_one_decomposition_scope_self_audit(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
        responses = [
            (SimpleNamespace(content=json.dumps({"verdict": "clarify", "outcome_spans": [], "dependency_edges": [], "reason_code": "status_filter_scope"}, ensure_ascii=False)), {}),
            (SimpleNamespace(content=json.dumps({"verdict": "exact", "outcome_spans": ["哪些还在路上"], "dependency_edges": [], "reason_code": "one_query_outcome"}, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=responses) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(user_text="哪些还在路上？", goals=[{"goal_id": "g1", "evidence_span": "哪些还在路上"}])
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        self.assertIn("target membership", invoke.call_args_list[1].kwargs["payload"][-1].content)

    def test_alignment_clarify_gets_one_semantic_scope_self_audit(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier
        responses = [
            (SimpleNamespace(content=json.dumps({"verdict": "clarify", "evidence_spans": ["哪些还在路上"], "missing_spans": [], "reason_code": "status_filter_scope"}, ensure_ascii=False)), {}),
            (SimpleNamespace(content=json.dumps({"verdict": "exact", "evidence_spans": ["哪些还在路上"], "missing_spans": [], "reason_code": "query_outcome_preserved"}, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=responses) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text="哪些还在路上？",
                goals=[{"goal_id": "g1", "evidence_span": "哪些还在路上", "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"}}],
                known_tools=set(),
            )
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        self.assertIn("filter/status vocabulary", invoke.call_args_list[1].kwargs["payload"][-1].content)

    def test_persistent_alignment_clarify_still_fails_closed(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier
        response = SimpleNamespace(content=json.dumps({"verdict": "clarify", "evidence_spans": ["处理一下"], "missing_spans": [], "reason_code": "outcome_identity_ambiguous"}, ensure_ascii=False))
        with patch("agent_core.config.get_model", return_value=object()), patch("agent_core.model_calls.invoke_model", side_effect=[(response, {}), (response, {})]) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(user_text="处理一下", goals=[{"goal_id": "g1", "evidence_span": "处理一下"}], known_tools=set())
        self.assertEqual(verdict.verdict, "clarify")
        self.assertEqual(invoke.call_count, 2)
        self.assertTrue(verdict.details["verifier_repair_attempted"])

    def test_under_split_runtime_feedback_is_independent_literal_only(self) -> None:
        from agent_core.lifecycle.goal_granularity import GoalGranularityVerdict
        from agent_core.lifecycle.goal_planning import GoalAlignmentVerdict, validate_goal_declaration
        alignment = GoalAlignmentVerdict("exact", ("查一下鼠标物流",), (), "exact", "test", True, {})
        granularity = GoalGranularityVerdict(
            "under_split", "blind_inventory_has_more_outcomes_than_declared_goals",
            ({"goal_id": None, "reason": "blind_inventory_outcome_not_covered", "recommended_role": "goal", "evidence_span": "快递员手机号"},),
            "model_blind_inventory", True,
            {"inventory_outcome_count": 2, "declared_goal_count": 1, "matched_outcome_count": 1, "outcome_spans": ["查一下鼠标物流", "快递员手机号"]},
        )
        with patch("agent_core.lifecycle.goal_planning.verify_goal_alignment", return_value=alignment), patch("agent_core.lifecycle.goal_planning.verify_goal_granularity", return_value=granularity):
            result, declared = validate_goal_declaration(
                state={"current_user_input": "查一下鼠标物流，再告诉我快递员手机号"},
                args={"goals": [{"goal_id": "g1", "description": "查鼠标物流", "evidence_span": "查一下鼠标物流", "required": True, "depends_on": [], "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"}}]},
                capability_registry=object(),
            )
        self.assertIsNone(declared)
        self.assertEqual(result["code"], "GOAL_DECLARATION_UNDER_SPLIT")
        feedback = result["data"]["independent_verifier_feedback"]
        self.assertEqual(feedback["authority"], "candidate_blind_goal_inventory")
        self.assertEqual(feedback["uncovered_outcome_spans"], ["快递员手机号"])
        serialized = json.dumps(feedback, ensure_ascii=False)
        self.assertNotIn("query_courier_contact", serialized)
        self.assertNotIn("report_unsupported_request", serialized)

    def test_certification_failure_keeps_sanitized_verifier_diagnostic(self) -> None:
        smoke = _load_smoke()
        diagnostic = smoke._sanitized_goal_rejection_diagnostic({
            "code": "GOAL_DECLARATION_UNDER_SPLIT",
            "data": {
                "alignment_proof": {"verdict": "exact", "reason_code": "exact", "source": "model", "independent": True},
                "granularity_proof": {"verdict": "under_split", "reason_code": "blind_inventory_has_more_outcomes_than_declared_goals", "details": {"inventory_outcome_count": 2, "declared_goal_count": 1, "matched_outcome_count": 1, "outcome_spans": ["查一下鼠标物流", "快递员手机号"], "blind_self_audit_attempted": True}},
                "independent_verifier_feedback": {"authority": "candidate_blind_goal_inventory", "uncovered_outcome_spans": ["快递员手机号"]},
            },
        })
        self.assertEqual(diagnostic["code"], "GOAL_DECLARATION_UNDER_SPLIT")
        self.assertEqual(diagnostic["granularity"]["inventory_outcome_count"], 2)
        self.assertEqual(diagnostic["independent_verifier_feedback"]["uncovered_outcome_spans"], ["快递员手机号"])

    def test_provider_and_browser_outer_slas_are_unchanged(self) -> None:
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        semantic = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)
        self.assertIn('{ timeout: 120_000 }', browser)
        self.assertIn('model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")', semantic)


if __name__ == "__main__":
    unittest.main()
