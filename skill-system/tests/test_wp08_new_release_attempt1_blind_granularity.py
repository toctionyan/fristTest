from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / "services/agent-service"
AGENT_SRC = AGENT_ROOT / "src"
for path in (AGENT_ROOT, AGENT_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier  # noqa: E402


class _Response:
    def __init__(self, content: str):
        self.content = content


class BlindGranularityRepairTests(unittest.TestCase):
    def _verify(self, *, user_text: str, goals: list[dict], outcome_spans: list[str]):
        captured: dict = {}

        def fake_invoke_model(*, purpose, model, payload):
            captured["purpose"] = purpose
            captured["payload"] = payload
            return _Response(json.dumps({
                "verdict": "exact",
                "outcome_spans": outcome_spans,
                "reason_code": "test_inventory",
            }, ensure_ascii=False)), {"status": "ok"}

        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=fake_invoke_model
        ):
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        return verdict, captured

    def test_blind_inventory_catches_dropped_unsupported_branch(self) -> None:
        user_text = "查一下鼠标物流，再告诉我快递员手机号"
        goals = [{
            "goal_id": "g1",
            "evidence_span": "查一下鼠标物流",
            "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
        }]
        verdict, captured = self._verify(
            user_text=user_text,
            goals=goals,
            outcome_spans=["查一下鼠标物流", "快递员手机号"],
        )
        self.assertEqual(verdict.verdict, "under_split")
        self.assertEqual(verdict.reason_code, "blind_inventory_has_more_outcomes_than_declared_goals")
        self.assertEqual(verdict.details["inventory_outcome_count"], 2)
        self.assertEqual(verdict.details["declared_goal_count"], 1)
        self.assertTrue(verdict.independent)
        self.assertTrue(verdict.details["candidate_blind"])
        prompt = "\n".join(str(getattr(row, "content", row)) for row in captured["payload"])
        self.assertNotIn("DECLARED_GOALS", prompt)
        self.assertNotIn("requested_effect", prompt)
        self.assertNotIn("list_orders", prompt)
        self.assertNotIn("get_order_logistics", prompt)

    def test_blind_inventory_accepts_one_to_one_supported_plus_open_outcome(self) -> None:
        user_text = "查一下鼠标物流，再告诉我快递员手机号"
        goals = [
            {"goal_id": "g1", "evidence_span": "查一下鼠标物流"},
            {"goal_id": "g2", "evidence_span": "快递员手机号"},
        ]
        verdict, _ = self._verify(
            user_text=user_text,
            goals=goals,
            outcome_spans=["查一下鼠标物流", "快递员手机号"],
        )
        self.assertEqual(verdict.verdict, "exact")
        self.assertEqual(verdict.details["matched_outcome_count"], 2)

    def test_blind_inventory_detects_over_split_without_keywords(self) -> None:
        user_text = "把待发货的订单取消"
        goals = [
            {"goal_id": "g1", "evidence_span": "待发货的订单"},
            {"goal_id": "g2", "evidence_span": "把待发货的订单取消"},
        ]
        verdict, _ = self._verify(
            user_text=user_text,
            goals=goals,
            outcome_spans=["把待发货的订单取消"],
        )
        self.assertEqual(verdict.verdict, "over_split")
        self.assertEqual(verdict.details["inventory_outcome_count"], 1)
        self.assertEqual(verdict.details["declared_goal_count"], 2)

    def test_inventory_non_literal_span_fails_closed(self) -> None:
        user_text = "查一下鼠标物流，再告诉我快递员手机号"
        goals = [{"goal_id": "g1", "evidence_span": "查一下鼠标物流"}]
        verdict, _ = self._verify(
            user_text=user_text,
            goals=goals,
            outcome_spans=["查询快递员电话"],
        )
        self.assertEqual(verdict.verdict, "indeterminate")
        self.assertEqual(verdict.reason_code, "goal_granularity_inventory_missing_literal_spans")

    def test_failed_attempt_oracle_remains_two_distinct_goals(self) -> None:
        catalog = json.loads((
            AGENT_ROOT / "tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
        ).read_text(encoding="utf-8"))
        case = next(row for row in catalog["cases"] if row["id"] == "semantic_supported_plus_unsupported")
        turn = case["execution_contract"]["turn_contracts"][0]
        oracle = turn["goal_oracle"]
        self.assertEqual(len(oracle), 2)
        self.assertEqual(oracle[0]["evidence_span"], "查一下鼠标物流")
        self.assertEqual(oracle[1]["evidence_span"], "快递员手机号")
        self.assertEqual(oracle[0]["requested_effect"], {
            "domain": "order", "operation": "query_logistics", "object_type": "order"
        })
        self.assertEqual(oracle[1]["requested_effect"], {
            "domain": "delivery", "operation": "query_courier_contact", "object_type": "courier"
        })
        self.assertEqual(oracle[1].get("requested_effect_match"), "unregistered_open")

    def test_certification_exact_effect_oracle_is_unchanged(self) -> None:
        source = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn('_effect_identity(row.get("requested_effect")) == expected_effect', source)
        self.assertIn('match_mode == "unregistered_open"', source)

    def test_browser_response_sla_and_provider_budget_are_unchanged(self) -> None:
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        self.assertIn('{ timeout: 120_000 }', browser)
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)


if __name__ == "__main__":
    unittest.main()
