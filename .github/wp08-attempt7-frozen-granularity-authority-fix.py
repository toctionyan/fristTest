#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_region(path: Path, start_marker: str, end_marker: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


granularity = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
replace_once(
    granularity,
    "from dataclasses import dataclass\nimport json\n",
    "from dataclasses import dataclass\nfrom hashlib import sha256\nimport json\n",
)

new_model_region = r'''GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION = "goal-granularity-inventory-authority@1"


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _build_inventory_authority(
    *,
    user_text: str,
    outcome_spans: tuple[str, ...],
    dependency_edges: tuple[tuple[int, int], ...],
    reason_code: str,
    blind_self_audit_attempted: bool,
) -> dict[str, Any]:
    """Freeze only candidate-blind evidence, never candidate Goal structure.

    A declaration repair may change candidate Goals, but it must not cause the
    independent semantic authority already returned to that model to move on
    the next validation attempt.  This object therefore contains only the blind
    USER_TEXT inventory and its true-result dependency graph.
    """
    payload: dict[str, Any] = {
        "version": GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION,
        "user_text_sha256": sha256(str(user_text or "").encode("utf-8")).hexdigest(),
        "outcome_spans": list(outcome_spans),
        "dependency_edges": [
            {
                "dependent_span": outcome_spans[dependent],
                "requires_result_of_span": outcome_spans[prerequisite],
            }
            for dependent, prerequisite in dependency_edges
        ],
        "reason_code": _text(reason_code, limit=120) or "blind_inventory_exact",
        "source": "model_blind_inventory",
        "independent": True,
        "candidate_blind": True,
        "blind_self_audit_attempted": bool(blind_self_audit_attempted),
    }
    payload["integrity_digest"] = _canonical_digest(payload)
    return payload


def _validate_inventory_authority(
    *,
    user_text: str,
    authority: Any,
) -> tuple[dict[str, Any] | None, tuple[str, ...], tuple[tuple[int, int], ...], str | None]:
    if not isinstance(authority, dict):
        return None, (), (), "goal_granularity_inventory_authority_required"
    if str(authority.get("version") or "") != GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION:
        return None, (), (), "goal_granularity_inventory_authority_version_invalid"
    stored_digest = str(authority.get("integrity_digest") or "").strip()
    if not stored_digest:
        return None, (), (), "goal_granularity_inventory_authority_digest_required"
    digest_payload = dict(authority)
    digest_payload.pop("integrity_digest", None)
    if _canonical_digest(digest_payload) != stored_digest:
        return None, (), (), "goal_granularity_inventory_authority_digest_invalid"
    expected_user_digest = sha256(str(user_text or "").encode("utf-8")).hexdigest()
    if str(authority.get("user_text_sha256") or "") != expected_user_digest:
        return None, (), (), "goal_granularity_inventory_authority_user_text_mismatch"
    if authority.get("candidate_blind") is not True or authority.get("independent") is not True:
        return None, (), (), "goal_granularity_inventory_authority_not_independent"
    if str(authority.get("source") or "") != "model_blind_inventory":
        return None, (), (), "goal_granularity_inventory_authority_source_invalid"
    raw_spans = authority.get("outcome_spans")
    if not isinstance(raw_spans, list):
        return None, (), (), "goal_granularity_inventory_authority_outcome_spans_invalid"
    exact_raw_spans = tuple(str(value) for value in raw_spans if str(value))
    outcome_spans = _literal_outcome_spans(user_text, raw_spans)
    if not outcome_spans or exact_raw_spans != outcome_spans:
        return None, (), (), "goal_granularity_inventory_authority_outcome_spans_not_literal"
    dependency_edges, dependency_error = _literal_dependency_edges(
        user_text,
        outcome_spans,
        authority.get("dependency_edges"),
    )
    if dependency_error:
        return None, (), (), f"goal_granularity_inventory_authority_{dependency_error}"
    return dict(authority), outcome_spans, dependency_edges, None


def _inventory_authority_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    direct = state.get("goal_granularity_inventory_authority")
    if isinstance(direct, dict):
        return dict(direct)
    plan = state.get("current_turn_plan") if isinstance(state.get("current_turn_plan"), dict) else {}
    value = plan.get("goal_granularity_inventory_authority")
    return dict(value) if isinstance(value, dict) else None


def _evaluate_blind_inventory(
    *,
    user_text: str,
    goals: list[dict[str, Any]],
    outcome_spans: tuple[str, ...],
    dependency_edges: tuple[tuple[int, int], ...],
    authority: dict[str, Any],
    authority_reused: bool,
) -> GoalGranularityVerdict:
    matched, goal_to_outcome = _maximum_outcome_goal_matching(outcome_spans, goals)
    goal_count = len(goals)
    outcome_count = len(outcome_spans)
    dependency_graph_match = _blind_dependency_graph_matches(
        outcome_count=outcome_count,
        dependency_edges=dependency_edges,
        goals=goals,
        goal_to_outcome=goal_to_outcome,
    )
    dependency_edge_details = [
        {
            "dependent_span": outcome_spans[dependent],
            "requires_result_of_span": outcome_spans[prerequisite],
        }
        for dependent, prerequisite in dependency_edges
    ]
    details = {
        "candidate_blind": True,
        "inventory_outcome_count": outcome_count,
        "declared_goal_count": goal_count,
        "matched_outcome_count": matched,
        "outcome_spans": list(outcome_spans),
        "dependency_edges": dependency_edge_details,
        "dependency_graph_match": dependency_graph_match,
        "blind_self_audit_attempted": bool(authority.get("blind_self_audit_attempted")),
        "inventory_authority_reused": bool(authority_reused),
        "inventory_authority": dict(authority),
    }
    if matched == outcome_count == goal_count and dependency_graph_match:
        return GoalGranularityVerdict(
            "exact",
            str(authority.get("reason_code") or "blind_inventory_exact"),
            (),
            "model_blind_inventory",
            True,
            details,
        )
    if matched == outcome_count == goal_count and not dependency_graph_match:
        findings = [
            {
                "goal_id": str(goal.get("goal_id") or "") or None,
                "reason": "declared_goal_dependency_graph_mismatch",
                "recommended_role": "goal",
                "evidence_span": (
                    str(goal.get("evidence_span") or "")
                    if str(goal.get("evidence_span") or "") in user_text
                    else None
                ),
            }
            for goal in goals
        ]
        return GoalGranularityVerdict(
            "mixed",
            "blind_inventory_dependency_graph_mismatch",
            tuple(findings),
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


class ModelGoalGranularityVerifier:
    """Candidate-blind inventory plus deterministic candidate comparison.

    The model sees only current USER_TEXT. A first structural disagreement gets
    one second candidate-blind self-audit; the audit is never told candidate
    Goals, candidate count, matching result, tools or capabilities. The final
    blind inventory authority can then be frozen across declaration repair.
    """

    def verify(self, *, user_text: str, goals: list[dict[str, Any]]) -> GoalGranularityVerdict:
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
            "JSON only with verdict (exact|clarify), outcome_spans, dependency_edges, reason_code. Every outcome_span must be a local "
            "literal contiguous substring of USER_TEXT. dependency_edges must be an array of objects with dependent_span and "
            "requires_result_of_span, both copied from outcome_spans; return [] when the outcomes are independent."
        )
        rules = [
            "A separately requested unsupported/open business effect is still an outcome and must remain in the inventory.",
            "A supported outcome and an unsupported outcome in the same turn remain two outcomes when the customer can judge them separately.",
            "Filters, target selectors, ordering, cardinality, exclusions, reasons, dates, addresses, status predicates and other form values stay inside the outcome they constrain; do not inventory them as separate outcomes and do not ask to clarify their execution-time interpretation.",
            "Implementation/support steps, policy loading, permission checks, database work, Draft creation, authorization and rendering are never outcomes unless the customer explicitly requests them as a business result.",
            "Eligibility is a separate outcome only when the customer explicitly asks to receive that conclusion independently; otherwise it can be a condition/support step for an action.",
            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",
            "dependency_edges express true current-turn result dependency only: add an edge only when one outcome cannot determine its target, input, condition, or independently acceptable completion without the result of another current-turn outcome.",
            "When a later outcome omits its target but an earlier phrase in the same USER_TEXT already names the reusable business object or scope, inherit that stated scope as ellipsis; that is not a dependency on the earlier Goal result by itself.",
            "Sentence order, shared topic/object/scope, conjunctions, and unsupported/open capability status never create a dependency edge by themselves.",
            "A later outcome that refers to the not-yet-produced earlier result (for example it/this/that/其中/这个/该结果) or is explicitly conditional on that result requires an edge.",
            "Return each independently acceptable requested result exactly once. Sibling outcome spans must be non-overlapping local spans; never emit both a target phrase and the business action over that same target as separate outcomes.",
            "clarify only when ambiguity changes the number or identity of independently requested business outcomes; target membership, filters, status vocabulary, thresholds, current facts and slot values are not granularity ambiguity.",
            "Never omit an outcome merely because it appears unsupported, unusual, unavailable or outside the current deployment.",
        ]
        verifier_repair: str | None = None
        last_indeterminate = GoalGranularityVerdict(
            "indeterminate", "goal_granularity_inventory_unverified", (),
            "model_blind_inventory", True, {"candidate_blind": True},
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
                    "indeterminate", "goal_granularity_inventory_unavailable", (),
                    "model_blind_inventory", True,
                    {"exception": exc.__class__.__name__, "error_category": category, "candidate_blind": True},
                )
            parsed = _extract_json(str(getattr(response, "content", response) or ""))
            if parsed is None:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", "goal_granularity_inventory_non_json", (),
                    "model_blind_inventory", True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "The previous candidate-blind inventory response did not satisfy the machine-readable JSON contract. "
                        "Return exactly one JSON object using only verdict, outcome_spans, dependency_edges and reason_code; verdict must be exact or clarify. "
                        "Every outcome_span must be a local literal substring of USER_TEXT; dependency_edges must use only those spans and must be [] when independent. "
                        "Do not inspect or infer capabilities or candidate Goals."
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
                        "literal spans and the true dependency_edges. You still must not see or infer any candidate Goal plan or capability."
                    )
                    continue
                return GoalGranularityVerdict(
                    "clarify", _text(parsed.get("reason_code"), limit=120) or "blind_inventory_requires_clarification",
                    (), "model_blind_inventory", True,
                    {"candidate_blind": True, "verifier_repair_attempted": True},
                )
            if raw_verdict != "exact":
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", "goal_granularity_inventory_invalid_verdict", (),
                    "model_blind_inventory", True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0, "raw_verdict": raw_verdict or None},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return the candidate-blind business-outcome inventory in the strict JSON contract: verdict exact|clarify, "
                        "literal outcome_spans, dependency_edges and reason_code. dependency_edges must encode only true result dependencies and use [] for independent outcomes. "
                        "Do not inspect candidate Goals or capabilities."
                    )
                    continue
                return last_indeterminate
            outcome_spans = _literal_outcome_spans(user_text, parsed.get("outcome_spans"))
            if not outcome_spans:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", "goal_granularity_inventory_missing_literal_spans", (),
                    "model_blind_inventory", True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return exact only with at least one local literal outcome_span from USER_TEXT. Inventory each independently "
                        "acceptable business result exactly once, return dependency_edges using only those spans (or [] when independent), "
                        "and do not inspect candidate Goals or capabilities."
                    )
                    continue
                return last_indeterminate
            dependency_edges, dependency_error = _literal_dependency_edges(
                user_text, outcome_spans, parsed.get("dependency_edges")
            )
            if dependency_error:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", dependency_error, (), "model_blind_inventory", True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return dependency_edges as an array of {dependent_span, requires_result_of_span}; both fields must refer "
                        "to exactly one literal outcome_span. Use [] when no outcome truly requires another current-turn result. "
                        "Do not inspect candidate Goals, candidate count, tools or capabilities."
                    )
                    continue
                return last_indeterminate
            authority = _build_inventory_authority(
                user_text=user_text,
                outcome_spans=outcome_spans,
                dependency_edges=dependency_edges,
                reason_code=_text(parsed.get("reason_code"), limit=120) or "blind_inventory_exact",
                blind_self_audit_attempted=attempt > 0,
            )
            verdict = _evaluate_blind_inventory(
                user_text=user_text,
                goals=goals,
                outcome_spans=outcome_spans,
                dependency_edges=dependency_edges,
                authority=authority,
                authority_reused=False,
            )
            if verdict.exact or attempt > 0:
                return verdict
            verifier_repair = (
                "Run a candidate-blind self-audit of USER_TEXT only. Return each independently acceptable business result exactly once and re-audit "
                "the true result-dependency graph among those outcomes. Do not duplicate a target phrase and its enclosing business action as two outcomes. "
                "Filters, status predicates, target selectors, ordering, exclusions, cardinality and form values stay inside the outcome they constrain. "
                "A later omitted target may inherit an explicitly stated same-turn business object/scope without depending on an earlier Goal result. "
                "Sentence order, shared topic/object/scope and unsupported/open status do not create dependency; an edge exists only when one outcome needs "
                "another current-turn result for its target, input, condition or independently acceptable completion. Do not inspect, infer or ask about any "
                "candidate Goal plan, candidate count, tool or capability."
            )
        return last_indeterminate


'''
replace_region(
    granularity,
    "class ModelGoalGranularityVerifier:\n",
    "def _goal_granularity_mode() -> str:\n",
    new_model_region,
)

old_verify_fragment = r'''    mode = _goal_granularity_mode()
    if mode == "disabled":
'''
new_verify_fragment = r'''    frozen_authority = _inventory_authority_from_state(state)
    if frozen_authority is not None:
        validated_authority, outcome_spans, dependency_edges, authority_error = _validate_inventory_authority(
            user_text=user_text,
            authority=frozen_authority,
        )
        if authority_error or validated_authority is None:
            return GoalGranularityVerdict(
                "indeterminate",
                authority_error or "goal_granularity_inventory_authority_invalid",
                (),
                "frozen_model_blind_inventory",
                True,
                {"candidate_blind": True, "inventory_authority_reused": True},
            )
        return _evaluate_blind_inventory(
            user_text=user_text,
            goals=goals,
            outcome_spans=outcome_spans,
            dependency_edges=dependency_edges,
            authority=validated_authority,
            authority_reused=True,
        )
    mode = _goal_granularity_mode()
    if mode == "disabled":
'''
replace_once(granularity, old_verify_fragment, new_verify_fragment)
replace_once(
    granularity,
    '    "GoalGranularityVerdict",\n',
    '    "GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION",\n    "GoalGranularityVerdict",\n',
)

runtime = ROOT / "services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py"
old_declare = r'''                result, declared = validate_goal_declaration(
                    state=state,
                    args=args,
                    capability_registry=capability_registry,
                )
                if declared is not None:
'''
new_declare = r'''                result, declared = validate_goal_declaration(
                    state=state,
                    args=args,
                    capability_registry=capability_registry,
                )
                result_data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
                granularity_proof = result_data.get("granularity_proof") if isinstance(result_data.get("granularity_proof"), dict) else {}
                granularity_details = granularity_proof.get("details") if isinstance(granularity_proof.get("details"), dict) else {}
                inventory_authority = granularity_details.get("inventory_authority")
                if isinstance(inventory_authority, dict):
                    # Freeze the final candidate-blind authority that produced
                    # this ToolMessage. _build_loop_plan preserves prior plan
                    # metadata across the bounded declaration-repair loop.
                    plan = {
                        **dict(plan),
                        "goal_granularity_inventory_authority": deepcopy(inventory_authority),
                    }
                if declared is not None:
'''
replace_once(runtime, old_declare, new_declare)

print("attempt7 frozen granularity authority repair applied")
