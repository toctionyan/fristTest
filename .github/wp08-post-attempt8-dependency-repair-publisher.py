#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: {count} for {old[:140]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


protocol = ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py"
replace_once(
    protocol,
    '                            "depends_on": {"type": "array", "items": {"type": "string"}},\n',
    '                            "depends_on": {\n'
    '                                "type": "array",\n'
    '                                "items": {"type": "string"},\n'
    '                                "description": (\n'
    '                                    "只表达当前轮 Goal 的真实结果依赖：只有后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才填写。"\n'
    '                                    "并列、再/然后/另外等话语顺序、共享同一业务对象或同一主题本身都不是依赖；这些情况必须保持独立。"\n'
    '                                    "若后一个 Goal 用它/这个/其中某项等指向本轮前一个 Goal 尚未产生的结果，或其条件显式依赖前一个结果，则应填写依赖。"\n'
    '                                    "系统不支持某个效果也不能因此制造依赖：能力缺失由后续 MatchProof 独立证明。"\n'
    '                                ),\n'
    '                            },\n',
)
replace_once(
    protocol,
    '            "同一当前轮内 Goal 之间的先后或结果依赖只能填写 depends_on；不得为尚未执行的当前轮 Goal 的未来结果创建 reference_expression。"\n',
    '            "同一当前轮内只有真实结果依赖才填写 depends_on：后一个 Goal 的目标、输入、条件或可完成含义必须使用前一个 Goal 的结果时才依赖；并列、再/然后/另外、共享对象或共享主题本身不构成依赖。不得为尚未执行的当前轮 Goal 的未来结果创建 reference_expression。"\n'
    '            "能力缺失不能改变依赖图；unsupported/open Goal 若语义上可独立判断是否得到满足，就必须保持独立，后续由 Capability MatchProof 证明缺失。"\n',
)


dialogue = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
old_planning = (
    '            "当前处于统一语义声明阶段：只能调用 declare_turn_goals。先完整理解当前原话与公开上下文，再按用户可独立判断完成与否的业务效果拆 Goal；不要按接口、Tool 或现有能力数量拆，也不要把筛选、输入、前置校验、政策读取、Draft 或展示步骤提升为 Goal。每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。显式引用历史结果、历史轮次或展示顺序成员时必须给出 reference_expression，由 Runtime 解析并只接受 UNIQUE 证明。系统没有对应能力时仍保留原 Goal，后续由 Capability MatchProof 证明缺失，禁止改写成相近能力。goal_type 只在旧能力合同确实需要时作为兼容提示，不是正式语义。"\n'
)
new_planning = (
    '            "当前处于统一语义声明阶段：只能调用 declare_turn_goals。先完整理解当前原话与公开上下文，再按用户可独立判断完成与否的业务效果拆 Goal；不要按接口、Tool 或现有能力数量拆，也不要把筛选、输入、前置校验、政策读取、Draft 或展示步骤提升为 Goal。每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。depends_on 只表示真实结果依赖：只有后一个 Goal 的目标、输入、条件或完成含义必须使用前一个 Goal 的结果才依赖；并列、再/然后/另外、共享业务对象或共享主题只是话语顺序/共同范围，不得据此制造依赖。若后一个 Goal 用它/这个/其中某项等指向本轮前一个 Goal 尚未产生的结果，或条件显式依赖前一个结果，则应声明 depends_on。显式引用历史结果、历史轮次或展示顺序成员时必须给出 reference_expression，由 Runtime 解析并只接受 UNIQUE 证明。系统没有对应能力时仍保留原 Goal且保持原本的独立/依赖关系，后续由 Capability MatchProof 证明缺失，禁止改写成相近能力或因 unsupported 状态附加依赖。goal_type 只在旧能力合同确实需要时作为兼容提示，不是正式语义。"\n'
)
replace_once(dialogue, old_planning, new_planning)


goal_planning = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    goal_planning,
    '                "business effect, condition, ordering, unsupported request, or clarification need. Return JSON only with verdict "\n',
    '                "business effect, condition, ordering, unsupported request, clarification need, or the user-visible dependency/independence relation between goals. Return JSON only with verdict "\n',
)
replace_once(
    goal_planning,
    '            "a later outcome that relies on an earlier selection or query must declare depends_on that earlier goal",\n'
    '            "depends_on links only goals declared in this same current turn; never require a dependency on a goal from an earlier turn",\n',
    '            "depends_on is semantic result dependency, not sentence order: require it only when the later goal\'s target, input, condition, or independently acceptable completion must use the earlier current-turn goal\'s result",\n'
    '            "a later goal that refers to the not-yet-produced earlier result with it/this/that/其中/这个/该结果, or is explicitly conditional on that result, must declare depends_on that earlier goal",\n'
    '            "and/then/next/also/再/然后/另外 or merely sharing the same business object/topic does not by itself create depends_on; independently acceptable sibling outcomes must keep depends_on empty",\n'
    '            "unsupported or open effects obey the same semantic dependency rule: capability absence never creates a dependency and must not make an otherwise independent unsupported request depend on a supported sibling",\n'
    '            "a declaration is not exact when it adds a dependency that the user did not express, because that would incorrectly block an independently reportable goal behind another goal",\n'
    '            "depends_on links only goals declared in this same current turn; never require a dependency on a goal from an earlier turn",\n',
)


test = ROOT / "skill-system/tests/test_wp08_post_attempt8_dependency_repair.py"
test.write_text(r'''from __future__ import annotations

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
        description = goal["properties"]["depends_on"]["description"]
        self.assertIn("真实结果依赖", description)
        self.assertIn("再/然后/另外", description)
        self.assertIn("共享同一业务对象", description)
        self.assertIn("能力缺失", description)
        self.assertIn("它/这个/其中某项", description)

    def test_planning_prompt_carries_the_same_dependency_boundary(self) -> None:
        source = (ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        self.assertIn("depends_on 只表示真实结果依赖", source)
        self.assertIn("共享业务对象或共享主题只是话语顺序/共同范围", source)
        self.assertIn("因 unsupported 状态附加依赖", source)

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
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(protocol.relative_to(ROOT)),
        str(dialogue.relative_to(ROOT)),
        str(goal_planning.relative_to(ROOT)),
        str(test.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
