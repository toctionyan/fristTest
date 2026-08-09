#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:160]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_region(path: Path, start_marker: str, end_marker: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


granularity = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"

helper_marker = "\n\nclass ModelGoalGranularityVerifier:\n"
helper = r'''

def _literal_dependency_edges(
    user_text: str,
    outcome_spans: tuple[str, ...],
    values: Any,
) -> tuple[tuple[tuple[int, int], ...], str | None]:
    """Normalize a candidate-blind result-dependency graph over literal outcomes.

    The verifier never sees candidate Goals.  It can only connect two outcomes
    that it independently emitted as literal spans from the current user turn.
    An empty list is an explicit assertion that the outcomes are independent.
    """
    if not isinstance(values, list):
        return (), "blind_dependency_edges_required"
    edges: list[tuple[int, int]] = []
    for edge_index, raw in enumerate(values):
        if not isinstance(raw, dict):
            return (), f"blind_dependency_edge_invalid:{edge_index}"
        dependent_span = _text(raw.get("dependent_span"), limit=240)
        prerequisite_span = _text(raw.get("requires_result_of_span"), limit=240)
        if not dependent_span or dependent_span not in user_text:
            return (), f"blind_dependency_dependent_span_not_literal:{edge_index}"
        if not prerequisite_span or prerequisite_span not in user_text:
            return (), f"blind_dependency_prerequisite_span_not_literal:{edge_index}"
        dependent_matches = [
            index for index, span in enumerate(outcome_spans)
            if _spans_correspond(dependent_span, span)
        ]
        prerequisite_matches = [
            index for index, span in enumerate(outcome_spans)
            if _spans_correspond(prerequisite_span, span)
        ]
        if len(dependent_matches) != 1 or len(prerequisite_matches) != 1:
            return (), f"blind_dependency_edge_not_uniquely_bound:{edge_index}"
        edge = (dependent_matches[0], prerequisite_matches[0])
        if edge[0] == edge[1]:
            return (), f"blind_dependency_self_edge:{edge_index}"
        if edge not in edges:
            edges.append(edge)
    return tuple(edges), None


def _blind_dependency_graph_matches(
    *,
    outcome_count: int,
    dependency_edges: tuple[tuple[int, int], ...],
    goals: list[dict[str, Any]],
    goal_to_outcome: dict[int, int],
) -> bool:
    """Compare candidate Goals with the independently inventoried dependency graph."""
    if len(goals) != outcome_count or len(goal_to_outcome) != outcome_count:
        return False
    outcome_to_goal = {outcome: goal for goal, outcome in goal_to_outcome.items()}
    if set(outcome_to_goal) != set(range(outcome_count)):
        return False
    expected_by_goal: dict[int, set[str]] = {index: set() for index in range(len(goals))}
    for dependent_outcome, prerequisite_outcome in dependency_edges:
        dependent_goal = outcome_to_goal.get(dependent_outcome)
        prerequisite_goal = outcome_to_goal.get(prerequisite_outcome)
        if dependent_goal is None or prerequisite_goal is None:
            return False
        prerequisite_goal_id = str(goals[prerequisite_goal].get("goal_id") or "")
        if not prerequisite_goal_id:
            return False
        expected_by_goal[dependent_goal].add(prerequisite_goal_id)
    for goal_index, goal in enumerate(goals):
        actual = {
            str(value)
            for value in list(goal.get("depends_on") or [])
            if str(value)
        }
        if actual != expected_by_goal[goal_index]:
            return False
    return True
'''
replace_once(granularity, helper_marker, helper + helper_marker)

replace_once(
    granularity,
    '            "JSON only with verdict (exact|clarify), outcome_spans, reason_code. Every outcome_span must be a local "\n            "literal contiguous substring of USER_TEXT."\n',
    '            "JSON only with verdict (exact|clarify), outcome_spans, dependency_edges, reason_code. Every outcome_span must be a local "\n            "literal contiguous substring of USER_TEXT. dependency_edges must be an array of objects with dependent_span and "\n            "requires_result_of_span, both copied from outcome_spans; return [] when the outcomes are independent."\n',
)
replace_once(
    granularity,
    '            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",\n',
    '            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",\n            "dependency_edges express true current-turn result dependency only: add an edge only when one outcome cannot determine its target, input, condition, or independently acceptable completion without the result of another current-turn outcome.",\n            "Sentence order, shared topic/object, conjunctions, and unsupported/open capability status never create a dependency edge by themselves.",\n            "A later outcome that refers to the not-yet-produced earlier result (for example it/this/that/其中/这个/该结果) or is explicitly conditional on that result requires an edge.",\n',
)
replace_once(
    granularity,
    '                        "Return exactly one JSON object using only verdict, outcome_spans and reason_code; verdict must be exact or clarify, "\n                        "and every outcome_span must be a local literal substring of USER_TEXT. Do not inspect or infer capabilities."\n',
    '                        "Return exactly one JSON object using only verdict, outcome_spans, dependency_edges and reason_code; verdict must be exact or clarify. "\n                        "Every outcome_span must be a local literal substring of USER_TEXT; dependency_edges must use only those spans and must be [] when independent. "\n                        "Do not inspect or infer capabilities or candidate Goals."\n',
)
replace_once(
    granularity,
    '                        "Return the candidate-blind business-outcome inventory in the strict JSON contract: verdict exact|clarify, "\n                        "literal outcome_spans and reason_code. Do not inspect candidate Goals or capabilities."\n',
    '                        "Return the candidate-blind business-outcome inventory in the strict JSON contract: verdict exact|clarify, "\n                        "literal outcome_spans, dependency_edges and reason_code. dependency_edges must encode only true result dependencies and use [] for independent outcomes. "\n                        "Do not inspect candidate Goals or capabilities."\n',
)

old_core = r'''            outcome_spans = _literal_outcome_spans(user_text, parsed.get("outcome_spans"))
            if not outcome_spans:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", "goal_granularity_inventory_missing_literal_spans", (),
                    "model_blind_inventory", True,
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
            details = {
                "candidate_blind": True,
                "inventory_outcome_count": outcome_count,
                "declared_goal_count": goal_count,
                "matched_outcome_count": matched,
                "outcome_spans": list(outcome_spans),
                "blind_self_audit_attempted": attempt > 0,
            }
            if matched == outcome_count == goal_count:
                return GoalGranularityVerdict(
                    "exact", _text(parsed.get("reason_code"), limit=120) or "blind_inventory_exact", (),
                    "model_blind_inventory", True, details,
                )
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
'''
new_core = r'''            outcome_spans = _literal_outcome_spans(user_text, parsed.get("outcome_spans"))
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
                "blind_self_audit_attempted": attempt > 0,
            }
            if matched == outcome_count == goal_count and dependency_graph_match:
                return GoalGranularityVerdict(
                    "exact", _text(parsed.get("reason_code"), limit=120) or "blind_inventory_exact", (),
                    "model_blind_inventory", True, details,
                )
            if attempt == 0:
                verifier_repair = (
                    "Re-audit USER_TEXT only. Return each independently acceptable business result exactly once and also re-audit "
                    "the true result-dependency graph among those outcomes. Do not duplicate nested spans. Filters, status predicates, "
                    "target selectors, ordering, exclusions, cardinality and form values stay inside the outcome they constrain. "
                    "Sentence order, shared topic/object and unsupported/open status do not create dependency; an edge exists only when "
                    "one outcome needs another current-turn result for its target, input, condition or independently acceptable completion. "
                    "Do not inspect, infer or ask about any candidate Goal plan, candidate count, tool or capability."
                )
                continue
            if matched == outcome_count == goal_count and not dependency_graph_match:
                findings = [
                    {
                        "goal_id": str(goal.get("goal_id") or "") or None,
                        "reason": "declared_goal_dependency_graph_mismatch",
                        "recommended_role": "goal",
                        "evidence_span": str(goal.get("evidence_span") or "") if str(goal.get("evidence_span") or "") in user_text else None,
                    }
                    for goal in goals
                ]
                return GoalGranularityVerdict(
                    "mixed", "blind_inventory_dependency_graph_mismatch", tuple(findings),
                    "model_blind_inventory", True, details,
                )
            matched_outcomes = set(goal_to_outcome.values())
'''
replace_once(granularity, old_core, new_core)

planning = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
new_feedback = r'''def _granularity_repair_feedback(granularity: Any) -> dict[str, Any]:
    verdict = str(getattr(granularity, "verdict", "") or "")
    reason_code = str(getattr(granularity, "reason_code", "") or "")
    details = getattr(granularity, "details", {})
    details = details if isinstance(details, dict) else {}
    if verdict == "under_split":
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
    if verdict == "mixed" and reason_code == "blind_inventory_dependency_graph_mismatch":
        edges: list[dict[str, str]] = []
        for raw in list(details.get("dependency_edges") or []):
            if not isinstance(raw, dict):
                continue
            dependent_span = _clean_text(raw.get("dependent_span"), limit=240)
            prerequisite_span = _clean_text(raw.get("requires_result_of_span"), limit=240)
            if dependent_span and prerequisite_span:
                edges.append({
                    "dependent_span": dependent_span,
                    "requires_result_of_span": prerequisite_span,
                })
        return {
            "independent_verifier_feedback": {
                "authority": "candidate_blind_goal_inventory",
                "required_action": "redeclaration_preserving_candidate_blind_dependency_graph",
                "dependency_edges": edges,
                "constraints": [
                    "dependency_edges_are_literal_user_text_relations_not_oracle_answers",
                    "sentence_order_shared_topic_and_capability_absence_do_not_create_dependency",
                    "true_current_turn_result_dependency_must_be_preserved",
                    "do_not_change_requested_effect_to_fit_available_capabilities",
                ],
            }
        }
    return {}


'''
replace_region(
    planning,
    "def _granularity_repair_feedback(granularity: Any) -> dict[str, Any]:\n",
    "def validate_goal_declaration(\n",
    new_feedback,
)

print("attempt6 dependency root fix applied")
