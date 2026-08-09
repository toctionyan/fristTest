#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_region(path: Path, start_marker: str, end_marker: str, replacement: str, *, after: str | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    search_from = text.index(after) if after else 0
    start = text.index(start_marker, search_from)
    end = text.index(end_marker, start)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


# 1) Clarification can optionally bind the precise frozen Goal(s) it pauses.
protocol = ROOT / "services/agent-service/src/agent_core/lifecycle/protocol.py"
replace_once(
    protocol,
    '''            "question": {"type": "string"}, "reason": {"type": "string"},
            "missing_kind": {"type": "string", "enum": ["target", "scope", "condition", "intent"]},
            "evidence_handles": {"type": "array", "items": {"type": "string"}},
        }, "required": ["question", "reason", "missing_kind", "evidence_handles"], "additionalProperties": False},
''',
    '''            "question": {"type": "string"}, "reason": {"type": "string"},
            "missing_kind": {"type": "string", "enum": ["target", "scope", "condition", "intent"]},
            "evidence_handles": {"type": "array", "items": {"type": "string"}},
            "goal_ids": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
                "description": (
                    "仅在本轮正式语义已冻结时使用：列出真正因本次缺失输入而暂停的 Goal。"
                    "语义冻结前可以省略；多个待处理 Goal 时不得用它扩大或改写用户目标。"
                ),
            },
        }, "required": ["question", "reason", "missing_kind", "evidence_handles"], "additionalProperties": False},
''',
)

# 2) Runtime deterministically binds a clarification to the singleton pending Goal,
# or validates the model's explicit subset when several Goals are pending.
dialogue = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
insert_marker = "\ndef _bind_loop_tools(\n"
helper = '''\ndef _clarification_terminal_goal_ids(
    workflow_plan: dict[str, Any] | None,
    call: dict[str, Any],
) -> list[str]:
    """Bind a clarification pause without inventing or widening semantics.

    A singleton pending Goal is a deterministic orchestration fact and may be
    bound without asking the model to repeat its ID.  When several required
    Goals remain pending, only an explicit unique subset from the currently
    pending set is accepted.  Invalid/unknown bindings fail closed as empty.
    """
    plan = workflow_plan if isinstance(workflow_plan, dict) else {}
    pending = [
        str(goal.get("goal_id") or "")
        for goal in list(plan.get("goals") or [])
        if isinstance(goal, dict)
        and bool(goal.get("required", True))
        and str(goal.get("goal_id") or "")
        and str(goal.get("coverage_status") or "") in {"PENDING", "BLOCKED"}
    ]
    pending = list(dict.fromkeys(pending))
    pending_set = set(pending)
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    explicit = [str(value) for value in list(args.get("goal_ids") or []) if str(value)]
    if explicit:
        if len(explicit) != len(set(explicit)) or any(value not in pending_set for value in explicit):
            return []
        return explicit
    return pending if len(pending) == 1 else []


'''
text = dialogue.read_text(encoding="utf-8")
if "def _clarification_terminal_goal_ids(" in text:
    raise SystemExit("clarification helper already exists")
if insert_marker not in text:
    raise SystemExit("dialogue insertion marker missing")
dialogue.write_text(text.replace(insert_marker, "\n" + helper + "def _bind_loop_tools(\n", 1), encoding="utf-8")

replace_once(
    dialogue,
    '''                terminal_args = terminal[0].get("args") if isinstance(terminal[0].get("args"), dict) else {}
                terminal_goal_ids = [
                    str(value)
                    for value in list(terminal_args.get("goal_ids") or [])
                    if str(value)
                ]
                try:
                    plan_run = record_terminal_goal_outcome(
                        definition=frozen_plan_definition,
                        plan_run=plan_run,
                        goal_ids=terminal_goal_ids,
                        terminal_tool=str(terminal[0].get("name") or ""),
                    )
''',
    '''                terminal_args = terminal[0].get("args") if isinstance(terminal[0].get("args"), dict) else {}
                terminal_name = str(terminal[0].get("name") or "")
                terminal_goal_ids = (
                    _clarification_terminal_goal_ids(workflow_plan, terminal[0])
                    if terminal_name == "ask_user_clarification"
                    else [
                        str(value)
                        for value in list(terminal_args.get("goal_ids") or [])
                        if str(value)
                    ]
                )
                normalized_terminal_call = terminal[0]
                if terminal_name == "ask_user_clarification" and terminal_goal_ids:
                    normalized_terminal_call = {
                        **terminal[0],
                        "args": {**terminal_args, "goal_ids": terminal_goal_ids},
                    }
                try:
                    plan_run = record_terminal_goal_outcome(
                        definition=frozen_plan_definition,
                        plan_run=plan_run,
                        goal_ids=terminal_goal_ids,
                        terminal_tool=terminal_name,
                    )
''',
)
replace_once(
    dialogue,
    '''                    clarification_patch["goal_blockers"] = goal_blockers_for_clarification(
                        state=clarification_state,
                        call=terminal[0],
                        capability_surface=capability_surface,
                    )
''',
    '''                    clarification_patch["goal_blockers"] = goal_blockers_for_clarification(
                        state=clarification_state,
                        call=normalized_terminal_call,
                        capability_surface=capability_surface,
                    )
''',
)

clarification = ROOT / "services/agent-service/src/agent_core/lifecycle/clarification_runtime.py"
replace_once(
    clarification,
    '''    selected = pending_ids or set(bound_goal_ids)
''',
    '''    bound_set = set(bound_goal_ids)
    if bound_set:
        selected = (pending_ids & bound_set) if pending_ids else bound_set
    elif len(pending_ids) == 1:
        selected = set(pending_ids)
    else:
        selected = set()
''',
)

# 3) Independent model verifier provider failures must remain observable as
# environment failures; machine-format indeterminacy gets exactly one bounded repair.
alignment = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    alignment,
    '''        from agent_core.model_calls import invoke_model, structured_verifier_messages
''',
    '''        from agent_core.model_calls import (
            classify_model_failure,
            invoke_model,
            is_environmental_model_failure_category,
            structured_verifier_messages,
        )
''',
)
alignment_replacement = '''        format_repair: str | None = None
        last_indeterminate = GoalAlignmentVerdict(
            "indeterminate", (), (), "goal_alignment_unverified", "model", True, {}
        )
        for attempt in range(2):
            try:
                response, _trace = invoke_model(
                    purpose="turn_goal_alignment_verifier",
                    model=get_model(),
                    payload=structured_verifier_messages(
                        role="turn_goal_alignment_verifier",
                        instruction=instruction,
                        decision_rules=decision_rules,
                        payload=prompt,
                        format_repair=format_repair,
                    ),
                )
            except Exception as exc:
                category = classify_model_failure(exc)
                if is_environmental_model_failure_category(category):
                    raise
                return GoalAlignmentVerdict(
                    "indeterminate",
                    (),
                    (),
                    "goal_alignment_verifier_unavailable",
                    "model",
                    True,
                    {"exception": exc.__class__.__name__, "error_category": category},
                )
            parsed = _extract_json(str(getattr(response, "content", response) or ""))
            if parsed is None:
                verdict = GoalAlignmentVerdict(
                    "indeterminate",
                    (),
                    (),
                    "goal_alignment_non_json",
                    "model",
                    True,
                    {"format_repair_attempted": attempt > 0},
                )
            else:
                verdict = _as_alignment_verdict(
                    parsed,
                    user_text=user_text,
                    source="model",
                    independent=True,
                )
            if verdict.verdict != "indeterminate":
                return verdict
            last_indeterminate = GoalAlignmentVerdict(
                verdict.verdict,
                verdict.evidence_spans,
                verdict.missing_spans,
                verdict.reason_code,
                verdict.source,
                verdict.independent,
                {**verdict.details, "format_repair_attempted": attempt > 0},
            )
            if attempt == 0:
                format_repair = (
                    "The previous verifier response did not satisfy the machine-readable JSON contract. "
                    "Return exactly one JSON object using only verdict, evidence_spans, missing_spans and reason_code; "
                    "all spans must be literal substrings of USER_TEXT. Do not change or expand the semantic task."
                )
        return last_indeterminate
'''
replace_region(
    alignment,
    "        try:\n            response, _trace = invoke_model(\n",
    "\n\n\nclass CandidateOnlyGoalAlignmentVerifier",
    alignment_replacement,
    after="class ModelGoalAlignmentVerifier:",
)

granularity = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
replace_once(
    granularity,
    '''        from agent_core.model_calls import invoke_model, structured_verifier_messages
''',
    '''        from agent_core.model_calls import (
            classify_model_failure,
            invoke_model,
            is_environmental_model_failure_category,
            structured_verifier_messages,
        )
''',
)
granularity_replacement = '''        format_repair: str | None = None
        parsed: dict[str, Any] | None = None
        raw_verdict = ""
        outcome_spans: tuple[str, ...] = ()
        for attempt in range(2):
            try:
                response, _trace = invoke_model(
                    purpose="turn_goal_granularity_inventory_verifier",
                    model=get_model(),
                    payload=structured_verifier_messages(
                        role="turn_goal_granularity_inventory_verifier",
                        instruction=instruction,
                        decision_rules=rules,
                        payload={"USER_TEXT_UNTRUSTED": user_text},
                        format_repair=format_repair,
                    ),
                )
            except Exception as exc:
                category = classify_model_failure(exc)
                if is_environmental_model_failure_category(category):
                    raise
                return GoalGranularityVerdict(
                    "indeterminate",
                    "goal_granularity_inventory_unavailable",
                    (),
                    "model_blind_inventory",
                    True,
                    {"exception": exc.__class__.__name__, "error_category": category, "candidate_blind": True},
                )

            parsed = _extract_json(str(getattr(response, "content", response) or ""))
            repair_reason = "goal_granularity_inventory_non_json"
            if parsed is not None:
                raw_verdict = _text(parsed.get("verdict"), limit=40).lower()
                if raw_verdict == "clarify":
                    return GoalGranularityVerdict(
                        "clarify",
                        _text(parsed.get("reason_code"), limit=120) or "blind_inventory_requires_clarification",
                        (),
                        "model_blind_inventory",
                        True,
                        {"candidate_blind": True, "format_repair_attempted": attempt > 0},
                    )
                if raw_verdict == "exact":
                    outcome_spans = _literal_outcome_spans(user_text, parsed.get("outcome_spans"))
                    if outcome_spans:
                        break
                    repair_reason = "goal_granularity_inventory_missing_literal_spans"
                else:
                    repair_reason = "goal_granularity_inventory_invalid_verdict"
            if attempt == 0:
                format_repair = (
                    "The previous candidate-blind inventory response did not satisfy the machine-readable JSON contract. "
                    "Return exactly one JSON object using only verdict, outcome_spans and reason_code; verdict must be exact or clarify, "
                    "and every outcome_span must be a local literal substring of USER_TEXT. Do not inspect or infer capabilities."
                )
                continue
            return GoalGranularityVerdict(
                "indeterminate",
                repair_reason,
                (),
                "model_blind_inventory",
                True,
                {"candidate_blind": True, "format_repair_attempted": True, "raw_verdict": raw_verdict or None},
            )

'''
replace_region(
    granularity,
    "        try:\n            response, _trace = invoke_model(\n",
    "        matched, goal_to_outcome = _maximum_outcome_goal_matching(outcome_spans, goals)\n",
    granularity_replacement,
    after="class ModelGoalGranularityVerifier:",
)
# replace_region removes the end marker; restore it explicitly.
text = granularity.read_text(encoding="utf-8")
needle = granularity_replacement
if needle not in text:
    raise SystemExit("granularity replacement missing after write")
text = text.replace(needle, needle + "        matched, goal_to_outcome = _maximum_outcome_goal_matching(outcome_spans, goals)\n", 1)
granularity.write_text(text, encoding="utf-8")

# 4) Certification call budget reflects the true protected worst case with one
# format-only repair per independent verifier.
semantic_smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    semantic_smoke,
    '''        # once through the exact same protected path, so the worst-case bounded envelope is
        # 12 prototypes * 2 declaration attempts * 3 model calls = 72.
        with model_call_scope(max_calls=72, scope="preprod_semantic_goal_prototypes") as calls:
''',
    '''        # once through the exact same protected path. Each independent verifier may use one
        # format-only repair, so the worst-case envelope is 12 * 2 * (1 declaration + 2 alignment + 2 granularity) = 120.
        with model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes") as calls:
''',
)

for test_path in (
    ROOT / "skill-system/tests/test_wp08_new_release_attempt3_protected_verifier_authority.py",
    ROOT / "skill-system/tests/test_wp08_new_release_attempt3_repair.py",
):
    text = test_path.read_text(encoding="utf-8")
    if "max_calls=72" not in text:
        raise SystemExit(f"expected 72-call assertion in {test_path}")
    test_path.write_text(text.replace("max_calls=72", "max_calls=120"), encoding="utf-8")

attempt4_test = ROOT / "skill-system/tests/test_wp08_new_release_attempt4_repair.py"
attempt4_test.write_text(r'''from __future__ import annotations

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


class Attempt4RepairTests(unittest.TestCase):
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
                user_text="查订单",
                goals=[{"goal_id": "g1", "evidence_span": "查订单"}],
                known_tools=set(),
            )
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        second_payload = invoke.call_args_list[1].kwargs["payload"][-1].content
        self.assertIn("FORMAT_REPAIR", second_payload)

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
                user_text="查订单",
                goals=[{"goal_id": "g1", "evidence_span": "查订单"}],
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
                    user_text="查订单",
                    goals=[{"goal_id": "g1", "evidence_span": "查订单"}],
                    known_tools=set(),
                )

    def test_protected_outer_slas_are_unchanged(self) -> None:
        semantic = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        self.assertIn('model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")', semantic)
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)
        self.assertIn('{ timeout: 120_000 }', browser)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

changed = [
    "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
    "services/agent-service/src/agent_core/lifecycle/clarification_runtime.py",
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/src/agent_core/lifecycle/protocol.py",
    "skill-system/tests/test_wp08_new_release_attempt3_protected_verifier_authority.py",
    "skill-system/tests/test_wp08_new_release_attempt3_repair.py",
    "skill-system/tests/test_wp08_new_release_attempt4_repair.py",
]
print(json.dumps({
    "status": "APPLIED",
    "changed": changed,
    "root_causes": [
        "post-freeze clarification terminal had no Goal binding, so intended clarification pause could not satisfy workflow verification",
        "independent verifier provider failures were collapsed into generic indeterminate semantic failures",
        "independent verifier machine-format indeterminacy had no bounded format-only repair",
    ],
}, ensure_ascii=False, indent=2))
