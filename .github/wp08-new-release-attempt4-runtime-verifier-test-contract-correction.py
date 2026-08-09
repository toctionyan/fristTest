#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()
path = ROOT / "skill-system/tests/test_wp08_new_release_attempt4_repair.py"
path.write_text(r'''from __future__ import annotations

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
    spec = importlib.util.spec_from_file_location("wp08_attempt4_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Attempt4RepairTests(unittest.TestCase):
    # Existing attempt-4 semantic repair contracts are intentionally preserved.
    def test_production_rejection_payload_preserves_exact_evidence_rule_and_current_input(self) -> None:
        smoke = _load_smoke()
        user_text = "查一下我的订单，再查下物流到哪了"
        result, declared = smoke._production_goal_declaration_evaluation(
            user_text=user_text,
            goals=[
                {
                    "goal_id": "g1",
                    "description": "查订单",
                    "evidence_span": "查一下我的订单",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
                },
                {
                    "goal_id": "g2",
                    "description": "查物流",
                    "evidence_span": "查询订单物流位置",
                    "required": True,
                    "depends_on": ["g1"],
                    "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
                },
            ],
        )
        self.assertIsNone(declared)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "GOAL_DECLARATION_INVALID")
        self.assertIn("evidence_not_in_current_turn:g2", result["data"]["errors"])
        self.assertEqual(result["data"]["current_user_input"], user_text)
        self.assertEqual(result["data"]["repair_contract"]["evidence_span_rule"], "literal_contiguous_substring")

    def test_bounded_repair_forwards_exact_runtime_result_without_oracle_material(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        start = source.index("def _declare_with_bounded_production_repair")
        end = source.index("def _identity_failure_reason", start)
        helper = source[start:end]
        self.assertIn("_validate_with_production_goal_contract", helper)
        self.assertIn("except RuntimeError as exc", helper)
        self.assertIn("isinstance(exc, _ProductionGoalDeclarationRejected)", helper)
        self.assertIn("content=json.dumps(result, ensure_ascii=False, default=str)", helper)
        self.assertIn('name="declare_turn_goals"', helper)
        self.assertIn("for attempt in range(1, 3)", helper)
        self.assertNotIn("goal_oracle", helper)
        self.assertNotIn("_match_oracle", helper)
        self.assertNotIn("expected_effect", helper)

    def test_oracle_still_runs_only_after_production_declaration_is_accepted(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        loop = source.index("for case in cases:")
        declaration = source.index("_declare_with_bounded_production_repair(", loop)
        oracle = source.index("_match_oracle(", declaration)
        self.assertLess(declaration, oracle)

    def test_runtime_itself_returns_full_repair_context_as_tool_result(self) -> None:
        tool_runtime = (AGENT_SRC / "agent_core/lifecycle/tool_execution_runtime.py").read_text(encoding="utf-8")
        goal_planning = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
        self.assertIn("result, declared = validate_goal_declaration", tool_runtime)
        self.assertIn("tool_message = _tool_result_message(call, result)", tool_runtime)
        self.assertIn('"current_user_input": user_text', goal_planning)
        self.assertIn('"evidence_span_rule": "literal_contiguous_substring"', goal_planning)

    def test_provider_budget_and_browser_response_gate_remain_bounded(self) -> None:
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)
        self.assertIn('{ timeout: 120_000 }', browser)

    # Attempt-4 certification exposed a separate post-freeze clarification binding defect.
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
                user_text="查订单", goals=[{"goal_id": "g1", "evidence_span": "查订单"}], known_tools=set()
            )
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        self.assertIn("FORMAT_REPAIR", invoke.call_args_list[1].kwargs["payload"][-1].content)

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
                user_text="查订单", goals=[{"goal_id": "g1", "evidence_span": "查订单"}]
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
                    user_text="查订单", goals=[{"goal_id": "g1", "evidence_span": "查订单"}], known_tools=set()
                )

    def test_semantic_worst_case_budget_matches_format_repair_envelope(self) -> None:
        semantic = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn('model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")', semantic)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
print("restored existing attempt-4 regressions and appended runtime/verifier counterexamples")
