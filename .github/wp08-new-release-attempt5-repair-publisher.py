#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: {count} for {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


list_orders = ROOT / "services/agent-service/src/agent_modules/ecommerce/capabilities/list_orders.py"
replace_once(
    list_orders,
    "    planner_rule='查询当前用户订单列表或由可见结果引用限定的订单集合。',\n",
    "    planner_rule='发现、筛选或列出当前用户的订单集合；当用户用商品名、状态或其他属性寻找订单本身时，即使运行时可能只命中一笔，也属于 order.list:order。也可由可见结果引用限定订单集合。',\n",
)
replace_once(
    list_orders,
    "        '除了', '第一个', '第二个', '待发货', '已签收', '签收',\n",
    "        '除了', '第一个', '第二个', '待发货', '已签收', '签收',\n"
    "        '按商品查订单', '某商品的订单', '查一下某商品订单', '鼠标订单', '键盘订单',\n",
)

order_details = ROOT / "services/agent-service/src/agent_modules/ecommerce/capabilities/get_order_details.py"
replace_once(
    order_details,
    "    planner_rule='查询一个已验证订单的详情；可作为对该精确订单执行后续动作的前置取证，本身不创建业务动作。',\n",
    "    planner_rule='查询一个在调用前已经通过订单号、唯一历史结果或唯一 ResultRef 明确绑定的订单详情；仅当目标已唯一解析时使用 order.query_details:order。用商品名、状态或其他属性寻找订单本身属于 order.list:order，不是详情查询。可作为对该精确订单执行后续动作的前置取证，本身不创建业务动作。',\n",
)
replace_once(
    order_details,
    "    discovery_examples=('订单详情', '订单状态', '是什么商品', '哪一个订单', '订单信息', '现在是什么状态'),\n",
    "    discovery_examples=('订单详情', '订单状态', '是什么商品', '哪一个订单', '订单信息', '现在是什么状态', '订单10002详情', '刚才那个订单的详情'),\n",
)
replace_once(
    order_details,
    "    exclusion_examples=('物流', '在路上', '退款进度', '售后进度', '发票进度'),\n",
    "    exclusion_examples=('物流', '在路上', '退款进度', '售后进度', '发票进度', '按商品查订单', '某商品的订单', '查一下某商品订单', '鼠标订单', '键盘订单'),\n",
)


test_file = ROOT / "skill-system/tests/test_wp08_new_release_attempt5_repair.py"
test_file.write_text(r'''from __future__ import annotations

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

    def test_certification_oracle_exact_match_is_not_weakened(self) -> None:
        smoke = (ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn('_effect_identity(row.get("requested_effect")) == expected_effect', smoke)
        self.assertIn('match_mode == "unregistered_open"', smoke)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(list_orders.relative_to(ROOT)),
        str(order_details.relative_to(ROOT)),
        str(test_file.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
