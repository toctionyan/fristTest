from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


class PostAttempt8DependencyRepairTests(unittest.TestCase):
    def test_declare_goal_schema_defines_semantic_dependency_not_discourse_order(self) -> None:
        from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA

        goal = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]
        properties = goal["properties"]
        required = set(goal["required"])
        self.assertNotIn("depends_on", properties)
        self.assertIn("input_bindings", properties)
        self.assertIn("input_bindings", required)
        binding_schema = properties["input_bindings"]
        binding_text = json.dumps(binding_schema, ensure_ascii=False)
        self.assertIn("current_goal_output", binding_text)
        self.assertIn("visible_result_ref", binding_text)
        self.assertIn("current_text", binding_text)

    def test_planning_prompt_carries_the_same_dependency_boundary(self) -> None:
        source = (ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        self.assertIn("禁止输出 depends_on", source)
        self.assertIn("Runtime 只从已验证 input_bindings 和 condition 确定性编译图边", source)
        self.assertIn("共享业务对象或共享主题只是话语顺序/共同范围", source)
        self.assertIn("系统没有对应能力时仍保留原 Goal", source)

    def test_independent_alignment_verifier_rejects_spurious_dependency_semantics(self) -> None:
        source = (ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
        self.assertIn("depends_on is semantic result dependency, not sentence order", source)
        self.assertIn("sharing the same business object/topic does not by itself create depends_on", source)
        self.assertIn("capability absence never creates a dependency", source)
        self.assertIn("adds a dependency that the user did not express", source)

    def _semantic_case(self, case_id: str) -> dict:
        path = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return next(row for row in payload["cases"] if row["id"] == case_id)

    def test_failed_attempt8_unsupported_sibling_oracle_is_independent(self) -> None:
        case = self._semantic_case("semantic_supported_plus_unsupported")
        turn = case["execution_contract"]["turn_contracts"][0]
        self.assertEqual(turn["user_text"], "查一下鼠标物流，再告诉我快递员手机号")
        first, second = turn["goal_oracle"]
        self.assertEqual(first["depends_on"], [])
        self.assertEqual(second["goal_type"], "unsupported")
        self.assertEqual(second["evidence_span"], "快递员手机号")
        self.assertEqual(second["depends_on"], [])
        self.assertEqual(second["required_tools"], ["report_unsupported_request"])
        scripted = turn["model_steps"][0]["tool_calls"][0]["args"]["goals"]
        scripted_g2 = next(row for row in scripted if row["goal_id"] == "g2")
        self.assertEqual(scripted_g2["depends_on"], [])
        self.assertEqual(scripted_g2["requested_effect"]["operation"], "query_courier_contact")

    def test_true_same_turn_result_reference_dependency_remains_required(self) -> None:
        case = self._semantic_case("semantic_query_then_refund_consult")
        turn = case["execution_contract"]["turn_contracts"][0]
        self.assertEqual(turn["user_text"], "查一下键盘订单，再看看它能不能退款")
        first, second = turn["goal_oracle"]
        self.assertEqual(first["depends_on"], [])
        self.assertEqual(second["evidence_span"], "它能不能退款")
        self.assertEqual(second["depends_on"], ["g1"])
        scripted = turn["model_steps"][0]["tool_calls"][0]["args"]["goals"]
        scripted_g2 = next(row for row in scripted if row["goal_id"] == "g2")
        self.assertEqual(scripted_g2["depends_on"], ["g1"])

    def test_runtime_preserves_declared_dependency_graph_without_keyword_rewrite(self) -> None:
        from agent_core.lifecycle.semantic_contract import freeze_semantic_contract

        independent = freeze_semantic_contract(
            turn=1,
            user_text="查一下鼠标物流，再告诉我快递员手机号",
            summary="two independent outcomes",
            goals=[
                {
                    "goal_id": "g1",
                    "description": "查物流",
                    "evidence_span": "查一下鼠标物流",
                    "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
                    "expected_result_cardinality": "single",
                    "required": True,
                    "depends_on": [],
                },
                {
                    "goal_id": "g2",
                    "description": "快递员手机号",
                    "evidence_span": "快递员手机号",
                    "requested_effect": {"domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"},
                    "expected_result_cardinality": "single",
                    "required": True,
                    "depends_on": [],
                },
            ],
            alignment_proof={"verdict": "exact", "source": "test"},
            granularity_proof={"verdict": "exact", "source": "test"},
        )
        self.assertEqual([row["depends_on"] for row in independent["goals"]], [[], []])

        dependent = freeze_semantic_contract(
            turn=1,
            user_text="查一下键盘订单，再看看它能不能退款",
            summary="result-dependent consult",
            goals=[
                {
                    "goal_id": "g1",
                    "description": "查订单",
                    "evidence_span": "查一下键盘订单",
                    "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
                    "expected_result_cardinality": "collection",
                    "required": True,
                    "depends_on": [],
                },
                {
                    "goal_id": "g2",
                    "description": "它能不能退款",
                    "evidence_span": "它能不能退款",
                    "requested_effect": {"domain": "refund", "operation": "assess_eligibility", "object_type": "order"},
                    "expected_result_cardinality": "single",
                    "required": True,
                    "depends_on": ["g1"],
                },
            ],
            alignment_proof={"verdict": "exact", "source": "test"},
            granularity_proof={"verdict": "exact", "source": "test"},
        )
        self.assertEqual(dependent["goals"][1]["depends_on"], ["g1"])

    def test_attempt8_browser_sla_and_fail_closed_probe_are_not_weakened(self) -> None:
        browser_js = (ROOT / "services/agent-service/frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        browser_verify = (ROOT / "scripts/verify_product_browser_journey.py").read_text(encoding="utf-8")
        self.assertIn("{ timeout: 120_000 }", browser_js)
        self.assertIn("browser_journey_post_response_timeout_probe", browser_verify)
        self.assertIn("except ConfiguredModelEnvironmentBlocked as exc", browser_verify)


if __name__ == "__main__":
    unittest.main()
