from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))


class NewReleaseAttempt1RepairTests(unittest.TestCase):
    def _case(self) -> dict:
        path = ROOT / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return next(row for row in payload["cases"] if row["id"] == "semantic_cancel_and_refund_branch")

    def test_filter_is_not_promoted_to_standalone_goal(self) -> None:
        turn = self._case()["execution_contract"]["turn_contracts"][0]
        oracle = turn["goal_oracle"]
        self.assertEqual(len(oracle), 2)
        self.assertEqual(oracle[0]["goal_type"], "action")
        self.assertEqual(oracle[0]["evidence_span"], "把待发货的订单取消")
        self.assertEqual(
            oracle[0]["requested_effect"],
            {"domain": "order", "operation": "cancel", "object_type": "order"},
        )
        self.assertEqual(oracle[0]["required_tools"], ["prepare_cancel_order"])
        self.assertEqual(oracle[1]["goal_type"], "consult")
        self.assertEqual(oracle[1]["oracle_id"], "g2")
        self.assertEqual(turn["expected"]["goal_count"], 2)
        self.assertEqual(turn["expected"]["workflow_levels"], ["L1_LIGHTWEIGHT_PLAN"])
        self.assertFalse(any(
            row.get("requested_effect", {}).get("operation") == "list"
            for row in oracle
        ))

    def test_scripted_execution_binds_literal_status_directly_to_business_tools(self) -> None:
        turn = self._case()["execution_contract"]["turn_contracts"][0]
        calls = [call for step in turn["model_steps"][1:] for call in step.get("tool_calls", [])]
        by_name = {call["name"]: call for call in calls}
        self.assertNotIn("list_orders", by_name)
        cancel = by_name["prepare_cancel_order"]["args"]
        refund = by_name["evaluate_refund_eligibility"]["args"]
        self.assertEqual(cancel["goal_ids"], ["g1"])
        self.assertEqual(cancel["target"], {"mode": "all_orders", "status": "待发货", "status_span": "待发货"})
        self.assertEqual(refund["goal_ids"], ["g2"])
        self.assertEqual(refund["target"], {"mode": "all_orders", "status": "已签收", "status_span": "已签收"})
        self.assertNotIn("list_orders", turn["required_tools"])
        self.assertNotIn("list_orders", turn["expected"]["trace"]["must_include"])
        self.assertEqual(turn["expected"]["port_calls"]["query_resources"], {"min": 2})

    def test_independent_semantic_prompt_uses_production_granularity_rule(self) -> None:
        production = (ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        smoke = (ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn("不要把筛选、输入、前置校验、政策读取、Draft 或展示步骤提升为 Goal", production)
        self.assertIn("筛选、选目标、输入、前置校验、政策读取、Draft 和展示都只是实现步骤，不能单独提升为 Goal", smoke)

    def test_inflight_model_log_is_allowlisted_and_secret_free(self) -> None:
        from agent_core.model_calls import gateway
        record = {
            "purpose": "agent_loop", "model": "deepseek-v4-flash", "sequence": 2,
            "scope": "request", "lane": "planner", "payload": "must-not-leak",
            "api_key": "must-not-leak", "request_headers": {"Authorization": "must-not-leak"},
        }
        with patch.object(gateway.LOGGER, "info") as info:
            gateway._emit_model_call_log("started", record)
        self.assertEqual(info.call_count, 1)
        serialized = " ".join(str(value) for value in info.call_args.args)
        self.assertIn("agent_loop", serialized)
        self.assertIn("planner", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("payload", serialized)

    def test_browser_response_sla_remains_120_seconds(self) -> None:
        source = (ROOT / "services/agent-service/frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('{ timeout: 120_000 }', source)


if __name__ == "__main__":
    unittest.main()
