from __future__ import annotations

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


class Attempt4RepairTests(unittest.TestCase):
    def test_clarification_schema_goal_binding_is_optional(self) -> None:
        from agent_core.lifecycle.protocol import ASK_USER_CLARIFICATION_SCHEMA
        params = ASK_USER_CLARIFICATION_SCHEMA["function"]["parameters"]
        self.assertIn("goal_ids", params["properties"])
        self.assertNotIn("goal_ids", params["required"])
        self.assertTrue(params["properties"]["goal_ids"]["uniqueItems"])

    def test_clarification_binding_singleton_is_deterministic_and_multi_goal_fails_closed(self) -> None:
        from agent_core.lifecycle.dialogue_runtime import _clarification_terminal_goal_ids
        one = {"goals": [{"goal_id": "g1", "required": True, "coverage_status": "PENDING"}]}
        self.assertEqual(_clarification_terminal_goal_ids(one, {"args": {}}), ["g1"])
        many = {"goals": [
            {"goal_id": "g1", "required": True, "coverage_status": "PENDING"},
            {"goal_id": "g2", "required": True, "coverage_status": "PENDING"},
        ]}
        self.assertEqual(_clarification_terminal_goal_ids(many, {"args": {}}), [])
        self.assertEqual(_clarification_terminal_goal_ids(many, {"args": {"goal_ids": ["g2"]}}), ["g2"])
        self.assertEqual(_clarification_terminal_goal_ids(many, {"args": {"goal_ids": ["missing"]}}), [])
        self.assertEqual(_clarification_terminal_goal_ids(many, {"args": {"goal_ids": ["g1", "g1"]}}), [])

    def test_goal_blocker_binding_does_not_expand_to_sibling_pending_goal(self) -> None:
        from agent_core.lifecycle import clarification_runtime
        goals = [
            {"goal_id": "g1", "required": True, "requested_effect": {"domain": "x", "operation": "a", "object_type": "x"}},
            {"goal_id": "g2", "required": True, "requested_effect": {"domain": "x", "operation": "b", "object_type": "x"}},
        ]
        state = {
            "turn_index": 2,
            "current_user_input": "请选择一个",
            "frozen_semantic_contract": {"user_text": "请选择一个"},
            "grounded_execution_plan": {"goals": [
                {"goal_id": "g1", "required": True, "coverage_status": "PENDING"},
                {"goal_id": "g2", "required": True, "coverage_status": "PENDING"},
            ]},
        }
        with patch.object(clarification_runtime, "semantic_goals", return_value=goals), patch.object(
            clarification_runtime, "read_plan_projection", return_value=state["grounded_execution_plan"]
        ):
            blockers = clarification_runtime.goal_blockers_for_clarification(
                state=state,
                call={"args": {"goal_ids": ["g2"], "missing_kind": "target", "question": "哪一个？", "reason": "需要选择", "evidence_handles": []}},
            )
        self.assertEqual([row["goal_id"] for row in blockers], ["g2"])

    def test_alignment_verifier_repairs_machine_format_once(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier
        responses = [
            (SimpleNamespace(content="not-json"), {}),
            (SimpleNamespace(content=json.dumps({
                "verdict": "exact", "evidence_spans": ["查订单"], "missing_spans": [], "reason_code": "exact"
            }, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=responses
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text="查订单",
                goals=[{"goal_id": "g1", "evidence_span": "查订单"}],
                known_tools=set(),
            )
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        second_payload = invoke.call_args_list[1].kwargs["payload"][-1].content
        self.assertIn("FORMAT_REPAIR", second_payload)

    def test_granularity_verifier_repairs_machine_format_once(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
        responses = [
            (SimpleNamespace(content="{}"), {}),
            (SimpleNamespace(content=json.dumps({
                "verdict": "exact", "outcome_spans": ["查订单"], "reason_code": "exact"
            }, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=responses
        ) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(
                user_text="查订单",
                goals=[{"goal_id": "g1", "evidence_span": "查订单"}],
            )
        self.assertEqual(verdict.verdict, "exact")
        self.assertEqual(invoke.call_count, 2)
        self.assertIn("FORMAT_REPAIR", invoke.call_args_list[1].kwargs["payload"][-1].content)

    def test_independent_verifier_environment_failure_is_not_masked(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=RuntimeError("provider down")
        ), patch(
            "agent_core.model_calls.classify_model_failure", return_value="timeout"
        ), patch(
            "agent_core.model_calls.is_environmental_model_failure_category", return_value=True
        ):
            with self.assertRaisesRegex(RuntimeError, "provider down"):
                ModelGoalAlignmentVerifier().verify(
                    user_text="查订单",
                    goals=[{"goal_id": "g1", "evidence_span": "查订单"}],
                    known_tools=set(),
                )

    def test_protected_outer_slas_are_unchanged(self) -> None:
        semantic = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")', semantic)
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)
        self.assertIn('{ timeout: 120_000 }', browser)


if __name__ == "__main__":
    unittest.main()
