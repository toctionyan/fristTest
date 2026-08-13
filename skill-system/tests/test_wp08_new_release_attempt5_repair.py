from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


class Attempt5EffectGuidanceRepairTests(unittest.TestCase):
    def _registry(self):
        from agent_core.kernel.capability_registry import CapabilityBinding, CapabilityRegistry
        from agent_modules.ecommerce.capabilities.list_orders import DEFINITION as list_orders
        from agent_modules.ecommerce.capabilities.get_order_details import DEFINITION as order_details

        def noop(*args, **kwargs):
            return {"ok": True}

        return CapabilityRegistry([
            CapabilityBinding("ecommerce", list_orders.contract, list_orders.schema, noop),
            CapabilityBinding("ecommerce", order_details.contract, order_details.schema, noop),
        ])

    def test_module_guidance_distinguishes_attribute_discovery_from_unique_detail_read(self) -> None:
        from agent_modules.ecommerce.capabilities.list_orders import DEFINITION as listed
        from agent_modules.ecommerce.capabilities.get_order_details import DEFINITION as details

        self.assertEqual(listed.contract.completion_effects, ("order.list:order",))
        self.assertEqual(listed.contract.planning_contract.target.cardinality, "collection")
        self.assertIn("商品名", listed.contract.planner_rule)
        self.assertIn("即使运行时可能只命中一笔", listed.contract.planner_rule)
        self.assertIn("按商品查订单", listed.contract.discovery_examples)
        self.assertIn("鼠标订单", listed.contract.discovery_examples)

        self.assertEqual(details.contract.completion_effects, ("order.query_details:order",))
        self.assertEqual(details.contract.planning_contract.target.cardinality, "exactly_one")
        self.assertIn("调用前", details.contract.planner_rule)
        self.assertIn("已唯一解析", details.contract.planner_rule)
        self.assertIn("order.list:order", details.contract.planner_rule)
        self.assertIn("按商品查订单", details.contract.exclusion_examples)
        self.assertIn("鼠标订单", details.contract.exclusion_examples)

    def test_effect_index_projects_the_stronger_boundary_without_tool_names(self) -> None:
        from agent_core.runtime.capability_effects import capability_effect_index

        index = capability_effect_index(self._registry())
        effects = {row["requested_effect_identity"]: row for row in index["effects"]}
        listed = effects["order.list:order"]["semantic_guidance"][0]
        details = effects["order.query_details:order"]["semantic_guidance"][0]
        self.assertEqual(listed["target_cardinality"], "collection")
        self.assertIn("商品名", listed["planner_rule"])
        self.assertIn("鼠标订单", listed["discovery_examples"])
        self.assertEqual(details["target_cardinality"], "exactly_one")
        self.assertIn("已唯一解析", details["planner_rule"])
        self.assertIn("鼠标订单", details["exclusion_examples"])
        serialized = json.dumps(index, ensure_ascii=False)
        self.assertNotIn("list_orders", serialized)
        self.assertNotIn("get_order_details", serialized)

    def test_exact_runtime_effect_matching_is_unchanged(self) -> None:
        from agent_core.runtime.capability_effects import discover_exact_effect_surface

        registry = self._registry()
        listed = discover_exact_effect_surface(registry, [{
            "goal_id": "g1",
            "requested_effect": {"domain": "order", "operation": "list", "object_type": "order"},
        }])
        details = discover_exact_effect_surface(registry, [{
            "goal_id": "g1",
            "requested_effect": {"domain": "order", "operation": "query_details", "object_type": "order"},
        }])
        self.assertEqual(listed["goals"][0]["completion_tools"], ["list_orders"])
        self.assertEqual(details["goals"][0]["completion_tools"], ["get_order_details"])
        self.assertFalse(listed["similarity_used"])
        self.assertFalse(details["similarity_used"])

    def test_failed_attempt5_oracle_remains_exact_collection_discovery(self) -> None:
        path = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        case = next(row for row in payload["cases"] if row["id"] == "semantic_query_then_refund_draft")
        turn = case["execution_contract"]["turn_contracts"][0]
        first = turn["goal_oracle"][0]
        self.assertEqual(first["evidence_span"], "查一下鼠标订单")
        self.assertEqual(first["required_tools"], ["list_orders"])
        self.assertEqual(first["requested_effect"], {"domain": "order", "operation": "list", "object_type": "order"})
        scripted = turn["model_steps"][0]["tool_calls"][0]["args"]["goals"][0]
        self.assertEqual(scripted["requested_effect"]["operation"], "list")
        list_call = turn["model_steps"][1]["tool_calls"][0]
        self.assertEqual(list_call["name"], "list_orders")
        self.assertEqual(list_call["args"]["target"], {"mode": "entity_match", "attribute_span": "鼠标"})
        self.assertEqual(list_call["args"]["expected_shape"], "collection")

    def test_certification_oracle_exact_canonical_match_is_not_weakened(self) -> None:
        smoke = (ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn("_requested_output_identity(row) in accepted_outputs", smoke)
        self.assertIn("require_canonical_output_identity=True", smoke)
        self.assertIn("planning_schemas(semantic_output_ids=semantic_output_ids)", smoke)
        self.assertNotIn('_effect_identity(row.get("requested_effect")) == expected_effect', smoke)
        self.assertNotIn('match_mode == "unregistered_open"', smoke)


if __name__ == "__main__":
    unittest.main()
