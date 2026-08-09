from __future__ import annotations

"""Independent review of Goal granularity before capability discovery.

The review decides only whether model-declared Goals are user-observable
business outcomes.  It cannot inspect available tools, choose capabilities or
rewrite an unsupported business effect into a nearby one.
"""

from dataclasses import dataclass
import json
import re
import unicodedata
from typing import Any, Protocol

from agent_core.runtime.profile import resolve_verifier_mode

_ALLOWED_VERDICTS = {"exact", "under_split", "over_split", "mixed", "clarify", "indeterminate"}
_ALLOWED_ROLES = {
    "goal",
    "target_constraint",
    "input_candidate",
    "condition",
    "capability_precondition",
    "support_step",
    "business_service_validation",
    "transaction_step",
    "presentation_step",
}


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    candidates = [raw]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


@dataclass(frozen=True)
class GoalGranularityVerdict:
    verdict: str
    reason_code: str
    findings: tuple[dict[str, Any], ...]
    source: str
    independent: bool
    details: dict[str, Any]

    @property
    def exact(self) -> bool:
        return self.verdict == "exact"

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason_code": self.reason_code,
            "findings": [dict(row) for row in self.findings],
            "source": self.source,
            "independent": self.independent,
            "details": dict(self.details),
        }


class GoalGranularityVerifier(Protocol):
    def verify(
        self,
        *,
        user_text: str,
        goals: list[dict[str, Any]],
    ) -> GoalGranularityVerdict | dict[str, Any]: ...


def _normalize(
    value: GoalGranularityVerdict | dict[str, Any],
    *,
    user_text: str,
    goal_ids: set[str],
    source: str,
    independent: bool,
) -> GoalGranularityVerdict:
    if isinstance(value, GoalGranularityVerdict):
        raw = value.as_dict()
    elif isinstance(value, dict):
        raw = dict(value)
    else:
        raw = {}
    verdict = _text(raw.get("verdict"), limit=40).lower()
    if verdict not in _ALLOWED_VERDICTS:
        verdict = "indeterminate"
    findings: list[dict[str, Any]] = []
    for item in list(raw.get("findings") or []):
        if not isinstance(item, dict):
            continue
        goal_id = _text(item.get("goal_id"), limit=200)
        role = _text(item.get("recommended_role"), limit=80)
        evidence_span = _text(item.get("evidence_span"), limit=240)
        if goal_id and goal_id not in goal_ids:
            continue
        if role and role not in _ALLOWED_ROLES:
            role = ""
        if evidence_span and evidence_span not in user_text:
            evidence_span = ""
        findings.append(
            {
                "goal_id": goal_id or None,
                "reason": (
                    _text(item.get("reason"), limit=240) or "unclassified"
                ),
                "recommended_role": role or None,
                "evidence_span": evidence_span or None,
            }
        )
    reason_code = (
        _text(raw.get("reason_code"), limit=120)
        or "goal_granularity_unclassified"
    )
    return GoalGranularityVerdict(
        verdict=verdict,
        reason_code=reason_code,
        findings=tuple(findings),
        source=_text(raw.get("source"), limit=80) or source,
        independent=bool(raw.get("independent", independent)),
        details=dict(raw.get("details") or {}),
    )


def _blind_span_key(value: Any) -> str:
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


class ModelGoalGranularityVerifier:
    """Candidate-blind inventory plus deterministic evidence-span comparison.

    The model sees only current USER_TEXT. A first structural disagreement gets
    one second candidate-blind self-audit; the audit is never told candidate
    Goals, candidate count, matching result, tools or capabilities.
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
            "Sentence order, shared topic/object, conjunctions, and unsupported/open capability status never create a dependency edge by themselves.",
            "A later outcome that refers to the not-yet-produced earlier result (for example it/this/that/其中/这个/该结果) or is explicitly conditional on that result requires an edge.",
            "Return each independently acceptable requested result exactly once. Sibling outcome spans must be non-overlapping local spans; never emit both a phrase and a nested/expanded version of that same outcome as separate outcomes.",
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
                        "literal spans. You still must not see or infer any candidate Goal plan or capability."
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
                    "Run a candidate-blind self-audit of USER_TEXT only. Return each independently acceptable business result exactly once and also re-audit "
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
                        "evidence_span": str(goal.get("evidence_span") or "") if str(goal.get("evidence_span") or "") in user_text else None,
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
            return GoalGranularityVerdict(verdict, reason_code, tuple(findings), "model_blind_inventory", True, details)
        return last_indeterminate



def _goal_granularity_mode() -> str:
    return resolve_verifier_mode(
        "GOAL_GRANULARITY_VERIFIER_MODE",
        local_default="candidate",
    )


class CandidateOnlyGoalGranularityVerifier:
    """Local fallback is a candidate assertion, never production proof."""

    def verify(
        self,
        *,
        user_text: str,
        goals: list[dict[str, Any]],
    ) -> GoalGranularityVerdict:
        del user_text
        return GoalGranularityVerdict(
            "exact",
            "local_candidate_granularity_only",
            (),
            "candidate_only",
            False,
            {"goal_count": len(goals)},
        )


def verify_goal_granularity(
    *,
    state: dict[str, Any],
    goals: list[dict[str, Any]],
) -> GoalGranularityVerdict:
    user_text = _text(state.get("current_user_input"), limit=20_000)
    ids = {
        str(row.get("goal_id") or "")
        for row in goals
        if str(row.get("goal_id") or "")
    }
    injected = state.get("goal_granularity_verifier")
    if injected is not None:
        try:
            method = getattr(injected, "verify", None)
            raw = (
                method(user_text=user_text, goals=goals)
                if callable(method)
                else injected(user_text=user_text, goals=goals)
            )
            return _normalize(
                raw,
                user_text=user_text,
                goal_ids=ids,
                source="injected",
                independent=True,
            )
        except Exception as exc:
            return GoalGranularityVerdict(
                "indeterminate",
                "goal_granularity_verifier_failed",
                (),
                "injected",
                True,
                {"exception": exc.__class__.__name__},
            )
    mode = _goal_granularity_mode()
    if mode == "disabled":
        return GoalGranularityVerdict(
            "indeterminate",
            "goal_granularity_verifier_disabled",
            (),
            "disabled",
            False,
            {},
        )
    verifier: GoalGranularityVerifier = (
        ModelGoalGranularityVerifier()
        if mode == "model"
        else CandidateOnlyGoalGranularityVerifier()
    )
    try:
        raw = verifier.verify(user_text=user_text, goals=goals)
        return _normalize(
            raw,
            user_text=user_text,
            goal_ids=ids,
            source="model" if mode == "model" else "candidate_only",
            independent=mode == "model",
        )
    except Exception as exc:
        return GoalGranularityVerdict(
            "indeterminate",
            "goal_granularity_verifier_unavailable",
            (),
            "model",
            True,
            {"exception": exc.__class__.__name__},
        )


__all__ = [
    "GoalGranularityVerdict",
    "GoalGranularityVerifier",
    "ModelGoalGranularityVerifier",
    "CandidateOnlyGoalGranularityVerifier",
    "_goal_granularity_mode",
    "verify_goal_granularity",
]
