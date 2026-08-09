#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import json

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_region(path: Path, start_marker: str, end_marker: str, body: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"start marker missing in {path}: {start_marker!r}")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        raise SystemExit(f"end marker missing in {path}: {end_marker!r}")
    path.write_text(text[:start] + body + text[end:], encoding="utf-8")


# Production had two candidate-aware semantic reviewers. A dropped unsupported
# branch could anchor both reviewers and still be frozen, while the independent
# WP-08 oracle later caught the missing Goal. Keep the existing alignment
# reviewer, but make the existing granularity model call candidate-blind: it
# inventories independently acceptable user outcomes from USER_TEXT alone and
# Runtime then compares that inventory with literal Goal evidence spans. This
# adds no model call and uses no keyword/tool/capability routing.
granularity = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
replace_once(
    granularity,
    "import json\nimport re\nfrom typing import Any, Protocol\n",
    "import json\nimport re\nimport unicodedata\nfrom typing import Any, Protocol\n",
)
new_model_verifier = r'''def _blind_span_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value, limit=1_000)).casefold()
    return re.sub(r"[\s,，。.!！?？;；:：、]+", "", normalized)


def _spans_correspond(left: Any, right: Any) -> bool:
    left_key = _blind_span_key(left)
    right_key = _blind_span_key(right)
    return bool(
        left_key
        and right_key
        and (
            left_key == right_key
            or left_key in right_key
            or right_key in left_key
        )
    )


def _literal_outcome_spans(user_text: str, values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    rows: list[str] = []
    for value in values:
        span = _text(value, limit=240)
        if span and span in user_text and span not in rows:
            rows.append(span)
    return tuple(rows)


def _maximum_outcome_goal_matching(
    outcome_spans: tuple[str, ...],
    goals: list[dict[str, Any]],
) -> tuple[int, dict[int, int]]:
    """Return maximum one-to-one literal-containment matching.

    The matching is structural only. It does not classify language, infer
    intents, inspect tools or rewrite requested_effect identities.
    """
    edges: dict[int, list[int]] = {
        outcome_index: [
            goal_index
            for goal_index, goal in enumerate(goals)
            if _spans_correspond(outcome_span, goal.get("evidence_span"))
        ]
        for outcome_index, outcome_span in enumerate(outcome_spans)
    }
    goal_to_outcome: dict[int, int] = {}

    def augment(outcome_index: int, seen_goals: set[int]) -> bool:
        for goal_index in edges.get(outcome_index, []):
            if goal_index in seen_goals:
                continue
            seen_goals.add(goal_index)
            prior = goal_to_outcome.get(goal_index)
            if prior is None or augment(prior, seen_goals):
                goal_to_outcome[goal_index] = outcome_index
                return True
        return False

    matched = 0
    for outcome_index in sorted(edges, key=lambda index: len(edges[index])):
        if augment(outcome_index, set()):
            matched += 1
    return matched, goal_to_outcome


class ModelGoalGranularityVerifier:
    """Candidate-blind inventory plus deterministic evidence-span comparison.

    The model sees only the current user text. It cannot anchor on, repair or
    imitate DECLARED_GOALS. Runtime compares the returned literal outcome spans
    to already validated literal Goal evidence spans. The existing independent
    goal-alignment verifier remains responsible for effect identity and semantic
    dependency correctness.
    """

    def verify(
        self,
        *,
        user_text: str,
        goals: list[dict[str, Any]],
    ) -> GoalGranularityVerdict:
        from agent_core.config import get_model
        from agent_core.model_calls import invoke_model, structured_verifier_messages

        instruction = (
            "Read USER_TEXT independently, before seeing any candidate Goal plan. Inventory every user-observable "
            "business outcome that the customer could independently judge complete or incomplete. Do not infer or "
            "inspect available tools/capabilities and do not decide whether the system supports an outcome. Return "
            "JSON only with verdict (exact|clarify), outcome_spans, reason_code. Every outcome_span must be a local "
            "literal contiguous substring of USER_TEXT."
        )
        rules = [
            "A separately requested unsupported/open business effect is still an outcome and must remain in the inventory.",
            "A supported outcome and an unsupported outcome in the same turn remain two outcomes when the customer can judge them separately.",
            "Filters, target selectors, ordering, cardinality, exclusions, reasons, dates, addresses and other form values stay inside the outcome they constrain; do not inventory them as separate outcomes.",
            "Implementation/support steps, policy loading, permission checks, database work, Draft creation, authorization and rendering are never outcomes unless the customer explicitly requests them as a business result.",
            "Eligibility is a separate outcome only when the customer explicitly asks to receive that conclusion independently; otherwise it can be a condition/support step for an action.",
            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",
            "When two independently acceptable requested results are present, return two non-overlapping local spans rather than one whole-sentence span.",
            "clarify only when USER_TEXT itself cannot be safely decomposed without additional customer information.",
            "Never omit an outcome merely because it appears unsupported, unusual, unavailable or outside the current deployment.",
        ]
        try:
            response, _trace = invoke_model(
                purpose="turn_goal_granularity_inventory_verifier",
                model=get_model(),
                payload=structured_verifier_messages(
                    role="turn_goal_granularity_inventory_verifier",
                    instruction=instruction,
                    decision_rules=rules,
                    payload={"USER_TEXT_UNTRUSTED": user_text},
                ),
            )
        except Exception as exc:
            return GoalGranularityVerdict(
                "indeterminate",
                "goal_granularity_inventory_unavailable",
                (),
                "model_blind_inventory",
                True,
                {"exception": exc.__class__.__name__, "candidate_blind": True},
            )

        parsed = _extract_json(str(getattr(response, "content", response) or ""))
        if parsed is None:
            return GoalGranularityVerdict(
                "indeterminate",
                "goal_granularity_inventory_non_json",
                (),
                "model_blind_inventory",
                True,
                {"candidate_blind": True},
            )
        raw_verdict = _text(parsed.get("verdict"), limit=40).lower()
        if raw_verdict == "clarify":
            return GoalGranularityVerdict(
                "clarify",
                _text(parsed.get("reason_code"), limit=120) or "blind_inventory_requires_clarification",
                (),
                "model_blind_inventory",
                True,
                {"candidate_blind": True},
            )
        if raw_verdict != "exact":
            return GoalGranularityVerdict(
                "indeterminate",
                "goal_granularity_inventory_invalid_verdict",
                (),
                "model_blind_inventory",
                True,
                {"candidate_blind": True, "raw_verdict": raw_verdict},
            )

        outcome_spans = _literal_outcome_spans(user_text, parsed.get("outcome_spans"))
        if not outcome_spans:
            return GoalGranularityVerdict(
                "indeterminate",
                "goal_granularity_inventory_missing_literal_spans",
                (),
                "model_blind_inventory",
                True,
                {"candidate_blind": True},
            )

        matched, goal_to_outcome = _maximum_outcome_goal_matching(outcome_spans, goals)
        goal_count = len(goals)
        outcome_count = len(outcome_spans)
        exact = matched == outcome_count == goal_count
        details = {
            "candidate_blind": True,
            "inventory_outcome_count": outcome_count,
            "declared_goal_count": goal_count,
            "matched_outcome_count": matched,
            "outcome_spans": list(outcome_spans),
        }
        if exact:
            return GoalGranularityVerdict(
                "exact",
                _text(parsed.get("reason_code"), limit=120) or "blind_inventory_exact",
                (),
                "model_blind_inventory",
                True,
                details,
            )

        matched_outcomes = set(goal_to_outcome.values())
        matched_goals = set(goal_to_outcome)
        findings: list[dict[str, Any]] = []
        for outcome_index, span in enumerate(outcome_spans):
            if outcome_index not in matched_outcomes:
                findings.append({
                    "goal_id": None,
                    "reason": "blind_inventory_outcome_not_covered",
                    "recommended_role": "goal",
                    "evidence_span": span,
                })
        for goal_index, goal in enumerate(goals):
            if goal_index not in matched_goals:
                findings.append({
                    "goal_id": str(goal.get("goal_id") or "") or None,
                    "reason": "declared_goal_not_uniquely_mapped_to_blind_outcome",
                    "recommended_role": "support_step",
                    "evidence_span": (
                        str(goal.get("evidence_span") or "")
                        if str(goal.get("evidence_span") or "") in user_text
                        else None
                    ),
                })

        if goal_count < outcome_count:
            verdict = "under_split"
            reason_code = "blind_inventory_has_more_outcomes_than_declared_goals"
        elif goal_count > outcome_count:
            verdict = "over_split"
            reason_code = "declared_goals_exceed_blind_inventory"
        else:
            verdict = "mixed"
            reason_code = "blind_inventory_not_one_to_one_with_declared_goals"
        return GoalGranularityVerdict(
            verdict,
            reason_code,
            tuple(findings),
            "model_blind_inventory",
            True,
            details,
        )


'''
replace_region(
    granularity,
    "class ModelGoalGranularityVerifier:\n",
    "def _goal_granularity_mode() -> str:\n",
    new_model_verifier,
)


test_file = ROOT / "skill-system/tests/test_wp08_new_release_attempt1_blind_granularity.py"
test_file.write_text(r'''from __future__ import annotations

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
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        str(granularity.relative_to(ROOT)),
        str(test_file.relative_to(ROOT)),
    ],
}, ensure_ascii=False, indent=2))
