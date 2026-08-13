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


class NewReleaseAttempt2RepairTests(unittest.TestCase):
    def _catalog(self) -> dict:
        path = AGENT_ROOT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _smoke(self):
        path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
        spec = importlib.util.spec_from_file_location("wp08_attempt2_semantic_smoke", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_courier_contact_is_registered_semantic_meaning_without_capability_coverage(self) -> None:
        source = (
            AGENT_SRC / "agent_modules/ecommerce/semantic_vocabulary.py"
        ).read_text(encoding="utf-8")
        self.assertIn('_output("courier.contact.phone"', source)
        self.assertIn("zero installed capability coverage", source)
        self.assertNotIn('"courier.contact.phone", "courier", ("read",), "配送人员的联系电话。", "', source)

        courier = []
        for case in self._catalog()["cases"]:
            execution = case.get("execution_contract") or {}
            for turn in execution.get("turn_contracts") or []:
                courier.extend(
                    goal for goal in turn.get("goal_oracle") or []
                    if "快递员" in str(goal.get("evidence_span") or "")
                    and ("电话" in str(goal.get("evidence_span") or "") or "手机号" in str(goal.get("evidence_span") or ""))
                )
        self.assertEqual(len(courier), 2)
        for goal in courier:
            # The historical execution fixture still records the old effect and
            # unsupported tool expectation. Certification no longer uses either
            # as semantic authority.
            self.assertEqual(goal.get("required_tools"), ["report_unsupported_request"])

    def test_canonical_match_accepts_unavailable_registered_meaning_and_rejects_nearby_output(self) -> None:
        smoke = self._smoke()
        oracle = [{
            "oracle_id": "g1",
            "evidence_span": "快递员手机号",
            "required": True,
            "depends_on": [],
            "accepted_output_sets": [["courier.contact.phone"]],
        }]
        smoke._match_oracle(
            case_id="canonical-unavailable-effect",
            oracle=oracle,
            goals=[{
                "goal_id": "m1",
                "evidence_span": "快递员手机号",
                "required": True,
                "depends_on": [],
                "requested_effect": {
                    "domain": "delivery",
                    "operation": "query_courier_contact",
                    "object_type": "courier",
                    "requested_outputs": [{
                        "output_id": "courier.contact.phone",
                        "evidence_span": "快递员手机号",
                    }],
                },
            }],
        )
        with self.assertRaises(RuntimeError):
            smoke._match_oracle(
                case_id="canonical-nearby-effect",
                oracle=oracle,
                goals=[{
                    "goal_id": "m1",
                    "evidence_span": "快递员手机号",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {
                        "domain": "order",
                        "operation": "query_logistics",
                        "object_type": "order",
                        "requested_outputs": [{
                            "output_id": "shipment.current_status",
                            "evidence_span": "快递员手机号",
                        }],
                    },
                }],
            )

    def test_unavailable_semantic_branch_cannot_be_dropped(self) -> None:
        smoke = self._smoke()
        with self.assertRaisesRegex(RuntimeError, "goal count mismatch"):
            smoke._match_oracle(
                case_id="drop-unavailable-branch",
                oracle=[
                    {
                        "oracle_id": "g1",
                        "evidence_span": "查物流",
                        "required": True,
                        "depends_on": [],
                        "accepted_output_sets": [["shipment.tracking"]],
                    },
                    {
                        "oracle_id": "g2",
                        "evidence_span": "快递员手机号",
                        "required": True,
                        "depends_on": [],
                        "accepted_output_sets": [["courier.contact.phone"]],
                    },
                ],
                goals=[{
                    "goal_id": "m1",
                    "evidence_span": "查物流",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {
                        "domain": "order",
                        "operation": "query_logistics",
                        "object_type": "order",
                        "requested_outputs": [{
                            "output_id": "shipment.tracking",
                            "evidence_span": "查物流",
                        }],
                    },
                }],
            )

    def test_declaration_clarification_detector_is_same_turn_only(self) -> None:
        from agent_core.lifecycle.dialogue_runtime import _declaration_clarification_required
        state = {
            "turn_index": 2,
            "current_turn_plan": {"turn": 2, "tool_calls": [{"name": "declare_turn_goals"}]},
            "tool_trace": [{"name": "declare_turn_goals", "result": {"ok": False, "code": "GOAL_DECLARATION_REQUIRES_CLARIFICATION"}}],
        }
        self.assertTrue(_declaration_clarification_required(state))
        self.assertFalse(_declaration_clarification_required({**state, "turn_index": 3}))
        self.assertFalse(_declaration_clarification_required({
            **state,
            "tool_trace": [{"name": "declare_turn_goals", "result": {"ok": True, "code": "TURN_SEMANTICS_FROZEN"}}],
        }))

    def test_declaration_clarification_surface_is_ask_only_and_terminal_before_workflow_build(self) -> None:
        source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        self.assertIn("[deepcopy(ASK_USER_CLARIFICATION_SCHEMA)]", source)
        self.assertIn('"ask_user_clarification"\n                if declaration_clarification_mode', source)
        handler = source.index("if declaration_clarification_mode:", source.index("raw_calls = _tool_calls(response)"))
        accepted = source.index('decision="declaration_clarification_accepted"', handler)
        workflow = source.index("candidate_workflow_plan = build_workflow_plan", accepted)
        self.assertLess(accepted, workflow)
        self.assertIn("不得发现、调用或暗示任何业务能力", source)

    def test_semantic_prompt_preserves_vocab_meanings_even_without_capability(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn("能力词汇中没有精确身份的分支也必须保留成独立 Goal", source)
        self.assertIn("capability-independent semantic vocabulary", source)
        self.assertIn("不包含能力可用性和工具身份", source)
        self.assertIn("get_module_registry().semantic_vocabulary_snapshot()", source)
        self.assertNotIn("capability_effect_index(get_runtime_registry().capabilities)", source)

    def test_browser_response_sla_remains_120_seconds(self) -> None:
        source = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('{ timeout: 120_000 }', source)


if __name__ == "__main__":
    unittest.main()
