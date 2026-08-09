from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

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
        self.assertEqual(
            result["data"]["repair_contract"]["evidence_span_rule"],
            "literal_contiguous_substring",
        )

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


if __name__ == "__main__":
    unittest.main()
