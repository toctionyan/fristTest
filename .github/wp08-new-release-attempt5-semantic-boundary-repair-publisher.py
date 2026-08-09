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


# ---------------------------------------------------------------------------
# Goal alignment: a verifier may challenge outcome identity/completeness, but
# must not turn downstream target/filter/status interpretation into a semantic
# clarification. One candidate-preserving self-audit is allowed before clarify.
# ---------------------------------------------------------------------------
alignment = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    alignment,
    '''            "clarify only when the user text itself is genuinely ambiguous and cannot be safely decomposed",\n''',
    '''            "clarify only when ambiguity changes which user-observable business outcome(s) are requested, their count, or their semantic dependency; target-member selection, filter/status vocabulary, result membership, slot/form values, current business facts, and execution-time cardinality are downstream Runtime concerns and must not trigger pre-freeze semantic clarification when the declaration preserves the user's literal predicate",\n''',
)

old_alignment_loop = '''        format_repair: str | None = None
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
new_alignment_loop = '''        verifier_repair: str | None = None
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
                        format_repair=verifier_repair,
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
                    {"verifier_repair_attempted": attempt > 0},
                )
            else:
                verdict = _as_alignment_verdict(
                    parsed,
                    user_text=user_text,
                    source="model",
                    independent=True,
                )
            if verdict.verdict in {"exact", "incomplete"}:
                return verdict
            if verdict.verdict == "clarify":
                if attempt == 0:
                    verifier_repair = (
                        "Re-audit only the semantic outcome boundary. Clarification is admissible only if USER_TEXT "
                        "cannot determine which independently acceptable business outcome(s) are requested, their count, "
                        "or their semantic dependency. Do not ask to clarify a target member, prior-result membership, "
                        "filter/status vocabulary, threshold, slot/form value, execution cardinality, or current business fact; "
                        "those are downstream Runtime concerns. If the declared goal preserves the literal predicate and the "
                        "business outcome identity is clear, return exact. Return the same strict JSON fields only."
                    )
                    continue
                return GoalAlignmentVerdict(
                    verdict.verdict,
                    verdict.evidence_spans,
                    verdict.missing_spans,
                    verdict.reason_code,
                    verdict.source,
                    verdict.independent,
                    {**verdict.details, "verifier_repair_attempted": True},
                )
            last_indeterminate = GoalAlignmentVerdict(
                verdict.verdict,
                verdict.evidence_spans,
                verdict.missing_spans,
                verdict.reason_code,
                verdict.source,
                verdict.independent,
                {**verdict.details, "verifier_repair_attempted": attempt > 0},
            )
            if attempt == 0:
                verifier_repair = (
                    "The previous verifier response did not satisfy the machine-readable JSON contract. "
                    "Return exactly one JSON object using only verdict, evidence_spans, missing_spans and reason_code; "
                    "all spans must be literal substrings of USER_TEXT. Do not change or expand the semantic task."
                )
        return last_indeterminate
'''
replace_once(alignment, old_alignment_loop, new_alignment_loop)

# Add concise, independent-verifier-owned redeclaration feedback for a real
# under-split. This is derived from the candidate-blind proof already returned
# by Runtime; it is not an oracle, tool, capability or expected effect identity.
insert_marker = "\ndef validate_goal_declaration(\n"
feedback_helper = '''\ndef _granularity_repair_feedback(granularity: Any) -> dict[str, Any]:
    if str(getattr(granularity, "verdict", "") or "") != "under_split":
        return {}
    spans: list[str] = []
    for finding in tuple(getattr(granularity, "findings", ()) or ()):
        if not isinstance(finding, dict):
            continue
        if str(finding.get("reason") or "") != "blind_inventory_outcome_not_covered":
            continue
        span = _clean_text(finding.get("evidence_span"), limit=240)
        if span and span not in spans:
            spans.append(span)
    if not spans:
        return {}
    return {
        "independent_verifier_feedback": {
            "authority": "candidate_blind_goal_inventory",
            "required_action": "redeclaration_preserving_existing_goals_and_adding_uncovered_outcomes",
            "uncovered_outcome_spans": spans,
            "constraints": [
                "literal_user_text_spans_only",
                "do_not_infer_or_copy_tool_or_capability_identity",
                "preserve_unsupported_or_open_business_effects",
                "do_not_delete_already_preserved_independent_outcomes",
            ],
        }
    }


'''
text = alignment.read_text(encoding="utf-8")
if "def _granularity_repair_feedback(" in text:
    raise SystemExit("granularity repair feedback helper already exists")
if insert_marker not in text:
    raise SystemExit("goal planning insertion marker missing")
alignment.write_text(text.replace(insert_marker, "\n" + feedback_helper + "def validate_goal_declaration(\n", 1), encoding="utf-8")

replace_once(
    alignment,
    '''            "data": {
                "alignment_proof": alignment.as_dict(),
                "granularity_proof": granularity.as_dict(),
                **_goal_declaration_repair_context(user_text),
            },
''',
    '''            "data": {
                "alignment_proof": alignment.as_dict(),
                "granularity_proof": granularity.as_dict(),
                **_granularity_repair_feedback(granularity),
                **_goal_declaration_repair_context(user_text),
            },
''',
)

# ---------------------------------------------------------------------------
# Candidate-blind granularity: disagreement itself receives one blind self-audit
# with no candidate disclosure. Clarify is also re-audited as a decomposition-
# scope question. Only the second independent inventory can reject granularity.
# ---------------------------------------------------------------------------
granularity = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
class_start = "class ModelGoalGranularityVerifier:\n"
class_end = "\ndef _goal_granularity_mode() -> str:\n"
new_class = '''class ModelGoalGranularityVerifier:
    """Candidate-blind inventory plus deterministic evidence-span comparison.

    The model sees only the current user text. It cannot anchor on, repair or
    imitate DECLARED_GOALS. Runtime compares the returned literal outcome spans
    to already validated literal Goal evidence spans. If the first blind
    inventory disagrees structurally, one second blind self-audit is allowed;
    the audit is never told the candidate, its Goal count or which span failed.
    The independent goal-alignment verifier remains responsible for effect
    identity and semantic dependency correctness.
    """

    def verify(
        self,
        *,
        user_text: str,
        goals: list[dict[str, Any]],
    ) -> GoalGranularityVerdict:
        from agent_core.config import get_model
        from agent_core.model_calls import (
            classify_model_failure,
            invoke_model,
            is_environmental_model_failure_category,
            structured_verifier_messages,
        )

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
            "Filters, target selectors, ordering, cardinality, exclusions, reasons, dates, addresses, status predicates and other form values stay inside the outcome they constrain; do not inventory them as separate outcomes and do not ask to clarify their execution-time interpretation.",
            "Implementation/support steps, policy loading, permission checks, database work, Draft creation, authorization and rendering are never outcomes unless the customer explicitly requests them as a business result.",
            "Eligibility is a separate outcome only when the customer explicitly asks to receive that conclusion independently; otherwise it can be a condition/support step for an action.",
            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",
            "Return each independently acceptable requested result exactly once. Outcome spans for sibling results must be non-overlapping local spans; never emit both a phrase and a nested/expanded version of that same outcome as two outcomes.",
            "clarify only when ambiguity changes the number or identity of independently requested business outcomes; target membership, filters, status vocabulary, thresholds, current facts and slot values are not granularity ambiguity.",
            "Never omit an outcome merely because it appears unsupported, unusual, unavailable or outside the current deployment.",
        ]

        verifier_repair: str | None = None
        last_indeterminate = GoalGranularityVerdict(
            "indeterminate",
            "goal_granularity_inventory_unverified",
            (),
            "model_blind_inventory",
            True,
            {"candidate_blind": True},
        )
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
                        format_repair=verifier_repair,
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
            if parsed is None:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate",
                    "goal_granularity_inventory_non_json",
                    (),
                    "model_blind_inventory",
                    True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "The previous candidate-blind inventory response did not satisfy the machine-readable JSON contract. "
                        "Return exactly one JSON object using only verdict, outcome_spans and reason_code; verdict must be exact or clarify, "
                        "and every outcome_span must be a local literal substring of USER_TEXT. Do not inspect or infer capabilities."
                    )
                    continue
                return last_indeterminate

            raw_verdict = _text(parsed.get("verdict"), limit=40).lower()
            if raw_verdict == "clarify":
                if attempt == 0:
                    verifier_repair = (
                        "Re-audit only candidate-blind outcome decomposition. Clarify is admissible only if ambiguity changes "
                        "the number or identity of independently requested business outcomes. Do not clarify target membership, "
                        "filter/status vocabulary, thresholds, current facts, cardinality or slot/form values. If outcome boundaries "
                        "are identifiable, return exact with each independently acceptable result exactly once as non-overlapping "
                        "literal spans. You still must not see or infer any candidate Goal plan or capability."
                    )
                    continue
                return GoalGranularityVerdict(
                    "clarify",
                    _text(parsed.get("reason_code"), limit=120) or "blind_inventory_requires_clarification",
                    (),
                    "model_blind_inventory",
                    True,
                    {"candidate_blind": True, "verifier_repair_attempted": True},
                )

            if raw_verdict != "exact":
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate",
                    "goal_granularity_inventory_invalid_verdict",
                    (),
                    "model_blind_inventory",
                    True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0, "raw_verdict": raw_verdict or None},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return the candidate-blind business-outcome inventory in the strict JSON contract: verdict exact|clarify, "
                        "literal outcome_spans and reason_code. Do not inspect candidate Goals or capabilities."
                    )
                    continue
                return last_indeterminate

            outcome_spans = _literal_outcome_spans(user_text, parsed.get("outcome_spans"))
            if not outcome_spans:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate",
                    "goal_granularity_inventory_missing_literal_spans",
                    (),
                    "model_blind_inventory",
                    True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return exact only with at least one local literal outcome_span from USER_TEXT. Inventory each independently "
                        "acceptable business result exactly once and do not inspect candidate Goals or capabilities."
                    )
                    continue
                return last_indeterminate

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
                "blind_self_audit_attempted": attempt > 0,
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

            # One blind self-audit is allowed on structural disagreement, but the
            # prompt deliberately reveals no candidate Goal, count, matching result
            # or missing span. The verifier can only re-audit its own USER_TEXT inventory.
            if attempt == 0:
                verifier_repair = (
                    "Re-audit the candidate-blind inventory from USER_TEXT only. Return each independently acceptable "
                    "business result exactly once; do not duplicate a result as nested/expanded overlapping spans. Filters, "
                    "status predicates, target selectors, ordering, exclusions, cardinality and form values stay inside the "
                    "business outcome they constrain. Unsupported/open requested business results must still remain separate. "
                    "Do not inspect, infer or ask about any candidate Goal plan, candidate count, tool or capability."
                )
                continue

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

        return last_indeterminate


'''
replace_region(granularity, class_start, class_end, new_class)
# restore the end marker removed by replace_region
text = granularity.read_text(encoding="utf-8")
if "def _goal_granularity_mode() -> str:" not in text:
    text = text.replace(new_class, new_class + "def _goal_granularity_mode() -> str:\n", 1)
else:
    raise SystemExit("unexpected goal granularity end marker survived replacement")
granularity.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Certification evidence: preserve a compact verifier-owned diagnostic when a
# bounded declaration repair exhausts, instead of reducing it to only the code.
# ---------------------------------------------------------------------------
smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
insert_before = "\n\nclass _ProductionGoalDeclarationRejected(RuntimeError):\n"
diagnostic_helper = '''\n\ndef _sanitized_goal_rejection_diagnostic(result: dict[str, Any] | None) -> dict[str, Any]:
    payload = result if isinstance(result, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    diagnostic: dict[str, Any] = {"code": str(payload.get("code") or "")}
    alignment = data.get("alignment_proof") if isinstance(data.get("alignment_proof"), dict) else None
    if alignment is not None:
        diagnostic["alignment"] = {
            "verdict": str(alignment.get("verdict") or ""),
            "reason_code": str(alignment.get("reason_code") or ""),
            "source": str(alignment.get("source") or ""),
            "independent": bool(alignment.get("independent")),
        }
    granularity = data.get("granularity_proof") if isinstance(data.get("granularity_proof"), dict) else None
    if granularity is not None:
        details = granularity.get("details") if isinstance(granularity.get("details"), dict) else {}
        diagnostic["granularity"] = {
            "verdict": str(granularity.get("verdict") or ""),
            "reason_code": str(granularity.get("reason_code") or ""),
            "inventory_outcome_count": details.get("inventory_outcome_count"),
            "declared_goal_count": details.get("declared_goal_count"),
            "matched_outcome_count": details.get("matched_outcome_count"),
            "outcome_spans": [str(value) for value in list(details.get("outcome_spans") or []) if str(value)][:8],
            "blind_self_audit_attempted": bool(details.get("blind_self_audit_attempted")),
        }
    feedback = data.get("independent_verifier_feedback") if isinstance(data.get("independent_verifier_feedback"), dict) else None
    if feedback is not None:
        diagnostic["independent_verifier_feedback"] = {
            "authority": str(feedback.get("authority") or ""),
            "uncovered_outcome_spans": [
                str(value) for value in list(feedback.get("uncovered_outcome_spans") or []) if str(value)
            ][:8],
        }
    return diagnostic
'''
text = smoke.read_text(encoding="utf-8")
if "def _sanitized_goal_rejection_diagnostic(" in text:
    raise SystemExit("semantic diagnostic helper already exists")
if insert_before not in text:
    raise SystemExit("semantic diagnostic insertion marker missing")
smoke.write_text(text.replace(insert_before, diagnostic_helper + insert_before, 1), encoding="utf-8")

replace_once(
    smoke,
    '''    The repair message contains no oracle count, expected effect identity or
    expected span. It mirrors the production rule: a rejected declaration is
    not frozen and the model must re-read the same user turn, preserving every
    independently completable effect, including open effects with no exact
    registered capability.
''',
    '''    The repair message contains no oracle-derived count, expected effect identity,
    span or dependency. It mirrors the production rule: a rejected declaration is
    not frozen and the model must re-read the same user turn, preserving every
    independently completable effect. Candidate-blind verifier feedback may expose
    its own grounded uncovered literal spans, but never an oracle/tool/capability answer.
''',
)
replace_once(
    smoke,
    '''        # result as the ToolMessage. Keep certification behavior identical:
        # the model may see the deterministic rejection code, validation
        # errors, current_user_input and repair_contract, but no oracle count,
        # expected effect identity, expected span or expected dependency.
''',
    '''        # result as the ToolMessage. Keep certification behavior identical:
        # the model may see the deterministic rejection code, validation errors,
        # current_user_input, repair_contract and candidate-blind verifier feedback,
        # but no oracle-derived count/effect/span/dependency or capability answer.
''',
)
replace_once(
    smoke,
    '''    errors = ((last_result or {}).get("data") or {}).get("errors") or [(last_result or {}).get("code")]
    raise RuntimeError(
        f"{case_id}: bounded production declaration repair exhausted: "
        f"{case_id}: production goal declaration rejected model output: {errors}"
    )
''',
    '''    errors = ((last_result or {}).get("data") or {}).get("errors") or [(last_result or {}).get("code")]
    diagnostic = _sanitized_goal_rejection_diagnostic(last_result)
    raise RuntimeError(
        f"{case_id}: bounded production declaration repair exhausted: "
        f"{case_id}: production goal declaration rejected model output: {errors}; "
        f"verifier_diagnostic={json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)}"
    )
''',
)

# ---------------------------------------------------------------------------
# Focused counterexamples. New file only: do not replace any prior attempt tests.
# ---------------------------------------------------------------------------
test_path = ROOT / "skill-system/tests/test_wp08_new_release_attempt5_repair.py"
test_path.write_text(r'''from __future__ import annotations

import importlib.util
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


def _load_smoke():
    path = AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("wp08_attempt5_semantic_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Attempt5RepairTests(unittest.TestCase):
    def test_blind_inventory_self_audits_false_extra_outcome_without_candidate_disclosure(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
        user_text = "查一下鼠标物流，再告诉我快递员手机号"
        goals = [
            {"goal_id": "g1", "evidence_span": "查一下鼠标物流"},
            {"goal_id": "g2", "evidence_span": "快递员手机号"},
        ]
        responses = [
            (SimpleNamespace(content=json.dumps({
                "verdict": "exact",
                "outcome_spans": ["查一下鼠标物流", "告诉我快递员手机号", "快递员手机号"],
                "reason_code": "first_inventory",
            }, ensure_ascii=False)), {}),
            (SimpleNamespace(content=json.dumps({
                "verdict": "exact",
                "outcome_spans": ["查一下鼠标物流", "快递员手机号"],
                "reason_code": "self_audited_inventory",
            }, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=responses
        ) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        second_prompt = invoke.call_args_list[1].kwargs["payload"][-1].content
        self.assertNotIn("DECLARED_GOALS", second_prompt)
        self.assertNotIn("g1", second_prompt)
        self.assertNotIn("g2", second_prompt)
        self.assertIn("candidate-blind", second_prompt)

    def test_real_under_split_remains_fail_closed_after_blind_self_audit(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
        user_text = "查一下鼠标物流，再告诉我快递员手机号"
        goals = [{"goal_id": "g1", "evidence_span": "查一下鼠标物流"}]
        response = SimpleNamespace(content=json.dumps({
            "verdict": "exact",
            "outcome_spans": ["查一下鼠标物流", "快递员手机号"],
            "reason_code": "two_independent_outcomes",
        }, ensure_ascii=False))
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=[(response, {}), (response, {})]
        ) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(user_text=user_text, goals=goals)
        self.assertEqual(verdict.verdict, "under_split")
        self.assertEqual(invoke.call_count, 2)
        uncovered = [
            row.get("evidence_span")
            for row in verdict.findings
            if row.get("reason") == "blind_inventory_outcome_not_covered"
        ]
        self.assertEqual(uncovered, ["快递员手机号"])
        self.assertTrue(verdict.details["blind_self_audit_attempted"])

    def test_granularity_clarify_gets_one_decomposition_scope_self_audit(self) -> None:
        from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier
        responses = [
            (SimpleNamespace(content=json.dumps({
                "verdict": "clarify", "outcome_spans": [], "reason_code": "status_filter_scope"
            }, ensure_ascii=False)), {}),
            (SimpleNamespace(content=json.dumps({
                "verdict": "exact", "outcome_spans": ["哪些还在路上"], "reason_code": "one_query_outcome"
            }, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=responses
        ) as invoke:
            verdict = ModelGoalGranularityVerifier().verify(
                user_text="哪些还在路上？",
                goals=[{"goal_id": "g1", "evidence_span": "哪些还在路上"}],
            )
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        self.assertIn("target membership", invoke.call_args_list[1].kwargs["payload"][-1].content)

    def test_alignment_clarify_gets_one_semantic_scope_self_audit(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier
        responses = [
            (SimpleNamespace(content=json.dumps({
                "verdict": "clarify",
                "evidence_spans": ["哪些还在路上"],
                "missing_spans": [],
                "reason_code": "status_filter_scope",
            }, ensure_ascii=False)), {}),
            (SimpleNamespace(content=json.dumps({
                "verdict": "exact",
                "evidence_spans": ["哪些还在路上"],
                "missing_spans": [],
                "reason_code": "query_outcome_preserved",
            }, ensure_ascii=False)), {}),
        ]
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=responses
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text="哪些还在路上？",
                goals=[{
                    "goal_id": "g1",
                    "evidence_span": "哪些还在路上",
                    "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
                }],
                known_tools=set(),
            )
        self.assertTrue(verdict.exact)
        self.assertEqual(invoke.call_count, 2)
        second_prompt = invoke.call_args_list[1].kwargs["payload"][-1].content
        self.assertIn("filter/status vocabulary", second_prompt)

    def test_persistent_alignment_clarify_still_fails_closed(self) -> None:
        from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier
        response = SimpleNamespace(content=json.dumps({
            "verdict": "clarify",
            "evidence_spans": ["处理一下"],
            "missing_spans": [],
            "reason_code": "outcome_identity_ambiguous",
        }, ensure_ascii=False))
        with patch("agent_core.config.get_model", return_value=object()), patch(
            "agent_core.model_calls.invoke_model", side_effect=[(response, {}), (response, {})]
        ) as invoke:
            verdict = ModelGoalAlignmentVerifier().verify(
                user_text="处理一下",
                goals=[{"goal_id": "g1", "evidence_span": "处理一下"}],
                known_tools=set(),
            )
        self.assertEqual(verdict.verdict, "clarify")
        self.assertEqual(invoke.call_count, 2)
        self.assertTrue(verdict.details["verifier_repair_attempted"])

    def test_under_split_runtime_feedback_is_independent_literal_only(self) -> None:
        from agent_core.lifecycle.goal_granularity import GoalGranularityVerdict
        from agent_core.lifecycle.goal_planning import GoalAlignmentVerdict, validate_goal_declaration
        alignment = GoalAlignmentVerdict(
            "exact", ("查一下鼠标物流",), (), "exact", "test", True, {}
        )
        granularity = GoalGranularityVerdict(
            "under_split",
            "blind_inventory_has_more_outcomes_than_declared_goals",
            ({
                "goal_id": None,
                "reason": "blind_inventory_outcome_not_covered",
                "recommended_role": "goal",
                "evidence_span": "快递员手机号",
            },),
            "model_blind_inventory",
            True,
            {
                "inventory_outcome_count": 2,
                "declared_goal_count": 1,
                "matched_outcome_count": 1,
                "outcome_spans": ["查一下鼠标物流", "快递员手机号"],
            },
        )
        with patch("agent_core.lifecycle.goal_planning.verify_goal_alignment", return_value=alignment), patch(
            "agent_core.lifecycle.goal_planning.verify_goal_granularity", return_value=granularity
        ):
            result, declared = validate_goal_declaration(
                state={"current_user_input": "查一下鼠标物流，再告诉我快递员手机号"},
                args={"goals": [{
                    "goal_id": "g1",
                    "description": "查鼠标物流",
                    "evidence_span": "查一下鼠标物流",
                    "required": True,
                    "depends_on": [],
                    "requested_effect": {"domain": "order", "operation": "query_logistics", "object_type": "order"},
                }]},
                capability_registry=object(),
            )
        self.assertIsNone(declared)
        self.assertEqual(result["code"], "GOAL_DECLARATION_UNDER_SPLIT")
        feedback = result["data"]["independent_verifier_feedback"]
        self.assertEqual(feedback["authority"], "candidate_blind_goal_inventory")
        self.assertEqual(feedback["uncovered_outcome_spans"], ["快递员手机号"])
        serialized = json.dumps(feedback, ensure_ascii=False)
        self.assertNotIn("query_courier_contact", serialized)
        self.assertNotIn("report_unsupported_request", serialized)

    def test_certification_failure_keeps_sanitized_verifier_diagnostic(self) -> None:
        smoke = _load_smoke()
        diagnostic = smoke._sanitized_goal_rejection_diagnostic({
            "code": "GOAL_DECLARATION_UNDER_SPLIT",
            "data": {
                "alignment_proof": {"verdict": "exact", "reason_code": "exact", "source": "model", "independent": True},
                "granularity_proof": {
                    "verdict": "under_split",
                    "reason_code": "blind_inventory_has_more_outcomes_than_declared_goals",
                    "details": {
                        "inventory_outcome_count": 2,
                        "declared_goal_count": 1,
                        "matched_outcome_count": 1,
                        "outcome_spans": ["查一下鼠标物流", "快递员手机号"],
                        "blind_self_audit_attempted": True,
                    },
                },
                "independent_verifier_feedback": {
                    "authority": "candidate_blind_goal_inventory",
                    "uncovered_outcome_spans": ["快递员手机号"],
                },
            },
        })
        self.assertEqual(diagnostic["code"], "GOAL_DECLARATION_UNDER_SPLIT")
        self.assertEqual(diagnostic["granularity"]["inventory_outcome_count"], 2)
        self.assertEqual(diagnostic["independent_verifier_feedback"]["uncovered_outcome_spans"], ["快递员手机号"])

    def test_provider_and_browser_outer_slas_are_unchanged(self) -> None:
        config = (AGENT_SRC / "agent_core/config.py").read_text(encoding="utf-8")
        browser = (AGENT_ROOT / "frontend/e2e/strong_context_journey.mjs").read_text(encoding="utf-8")
        semantic = (AGENT_ROOT / "scripts/verify_preprod_conversation_smoke.py").read_text(encoding="utf-8")
        self.assertIn('_bounded_float_env("MODEL_TIMEOUT_SECONDS", 25.0', config)
        self.assertIn('_bounded_int_env("MODEL_MAX_RETRIES", 1', config)
        self.assertIn('{ timeout: 120_000 }', browser)
        self.assertIn('model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes")', semantic)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print(json.dumps({
    "status": "APPLIED",
    "changed": [
        "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
        "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
        "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
        "skill-system/tests/test_wp08_new_release_attempt5_repair.py",
    ],
    "root_causes": [
        "candidate-blind granularity had no self-audit on a structurally inconsistent inventory, allowing false extra spans to become under-split authority",
        "semantic verifier clarify could absorb downstream target/filter/status interpretation and force a pre-freeze customer clarification",
        "under-split redeclaration feedback was nested and not explicit about the independent uncovered literal outcome",
        "semantic certification failure evidence discarded the verifier proof needed to distinguish a real under-split from verifier drift",
    ],
}, ensure_ascii=False, indent=2))
