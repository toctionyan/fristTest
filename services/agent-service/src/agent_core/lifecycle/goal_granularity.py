from __future__ import annotations

"""Independent review of Goal granularity before capability discovery.

The review decides only whether model-declared Goals are user-observable
business outcomes.  It cannot inspect available tools, choose capabilities or
rewrite an unsupported business effect into a nearby one.
"""

from dataclasses import dataclass
from hashlib import sha256
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
_ALLOWED_DEPENDENCY_BASIS_KINDS = {
    "result_reference",
    "result_condition",
    "result_value_input",
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


def _audited_dependency_edges(
    user_text: str,
    outcome_spans: tuple[str, ...],
    values: Any,
) -> tuple[tuple[tuple[int, int], ...], tuple[dict[str, Any], ...], str | None]:
    """Validate the dedicated candidate-blind dependency-basis audit.

    Outcome decomposition is already frozen for this audit.  Every retained
    dependency must identify a literal subspan inside the dependent outcome and
    classify why that subspan requires the earlier *result*.  The program does
    not interpret that language; it only validates the bounded evidence shape.
    """
    if not isinstance(values, list):
        return (), (), "blind_dependency_basis_edges_required"
    edges: list[tuple[int, int]] = []
    basis_rows: list[dict[str, Any]] = []
    for edge_index, raw in enumerate(values):
        if not isinstance(raw, dict):
            return (), (), f"blind_dependency_basis_edge_invalid:{edge_index}"
        dependent_span = _text(raw.get("dependent_span"), limit=240)
        prerequisite_span = _text(raw.get("requires_result_of_span"), limit=240)
        basis_kind = _text(raw.get("basis_kind"), limit=80).lower()
        basis_span = _text(raw.get("basis_span"), limit=240)
        if basis_kind not in _ALLOWED_DEPENDENCY_BASIS_KINDS:
            return (), (), f"blind_dependency_basis_kind_invalid:{edge_index}"
        dependent_matches = [
            index for index, span in enumerate(outcome_spans)
            if _spans_correspond(dependent_span, span)
        ]
        prerequisite_matches = [
            index for index, span in enumerate(outcome_spans)
            if _spans_correspond(prerequisite_span, span)
        ]
        if len(dependent_matches) != 1 or len(prerequisite_matches) != 1:
            return (), (), f"blind_dependency_basis_edge_not_uniquely_bound:{edge_index}"
        dependent_index = dependent_matches[0]
        prerequisite_index = prerequisite_matches[0]
        if dependent_index == prerequisite_index:
            return (), (), f"blind_dependency_basis_self_edge:{edge_index}"
        canonical_dependent = outcome_spans[dependent_index]
        if not basis_span or basis_span not in user_text or basis_span not in canonical_dependent:
            return (), (), f"blind_dependency_basis_span_not_in_dependent_outcome:{edge_index}"
        edge = (dependent_index, prerequisite_index)
        if edge in edges:
            return (), (), f"blind_dependency_basis_duplicate_edge:{edge_index}"
        edges.append(edge)
        basis_rows.append(
            {
                "dependent_span": canonical_dependent,
                "requires_result_of_span": outcome_spans[prerequisite_index],
                "basis_kind": basis_kind,
                "basis_span": basis_span,
            }
        )
    return tuple(edges), tuple(basis_rows), None


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


GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION = "goal-granularity-inventory-authority@1"


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
    dependency_edge_basis: tuple[dict[str, Any], ...] = (),
    dependency_basis_audited: bool = False,
) -> dict[str, Any]:
    """Freeze only candidate-blind evidence, never candidate Goal structure.

    A declaration repair may change candidate Goals, but it must not cause the
    independent semantic authority already returned to that model to move on
    the next validation attempt.  Outcome inventory and the dedicated blind
    dependency-basis audit are therefore frozen together.
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
        "dependency_edge_basis": [dict(row) for row in dependency_edge_basis],
        "dependency_basis_audited": bool(dependency_basis_audited),
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
    if len(outcome_spans) > 1:
        if authority.get("dependency_basis_audited") is not True:
            return None, (), (), "goal_granularity_inventory_authority_dependency_basis_not_audited"
        audited_edges, basis_rows, basis_error = _audited_dependency_edges(
            user_text,
            outcome_spans,
            authority.get("dependency_edge_basis"),
        )
        if basis_error:
            return None, (), (), f"goal_granularity_inventory_authority_{basis_error}"
        if audited_edges != dependency_edges:
            return None, (), (), "goal_granularity_inventory_authority_dependency_basis_graph_mismatch"
        if tuple(dict(row) for row in basis_rows) != tuple(
            dict(row) for row in list(authority.get("dependency_edge_basis") or [])
            if isinstance(row, dict)
        ):
            return None, (), (), "goal_granularity_inventory_authority_dependency_basis_not_canonical"
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
        "dependency_edge_basis": [dict(row) for row in list(authority.get("dependency_edge_basis") or []) if isinstance(row, dict)],
        "dependency_basis_audited": bool(authority.get("dependency_basis_audited")),
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


def _run_dependency_basis_audit(
    *,
    user_text: str,
    outcome_spans: tuple[str, ...],
    proposed_edges: tuple[tuple[int, int], ...],
) -> tuple[tuple[tuple[int, int], ...], tuple[dict[str, Any], ...], str, GoalGranularityVerdict | None]:
    """Use the second blind call only to certify result-dependency basis.

    This call cannot change the first call's outcome inventory and never sees
    candidate Goals, tools or capabilities.  It therefore removes the moving
    authority that previously let an execution-support edge oscillate across
    declaration repair while staying inside the existing two-call envelope.
    """
    from agent_core.config import get_model
    from agent_core.model_calls import (
        classify_model_failure,
        invoke_model,
        is_environmental_model_failure_category,
        structured_verifier_messages,
    )

    instruction = (
        "Audit only the true current-turn result-dependency graph over FIXED_OUTCOME_SPANS. "
        "Do not change, merge, split or add outcome spans. PROPOSED_DEPENDENCY_EDGES are a non-authoritative first-pass suggestion. "
        "Return JSON only with verdict (exact|clarify), dependency_edges and reason_code. Each retained dependency edge must contain dependent_span, requires_result_of_span, basis_kind and basis_span. "
        "basis_kind must be result_reference, result_condition or result_value_input. basis_span must be a literal substring inside the dependent outcome that specifically expresses use of the earlier current-turn result. Return dependency_edges=[] when no outcome truly needs another outcome's result."
    )
    rules = [
        "A dependency exists only when the customer-visible meaning of the later outcome needs the earlier current-turn result for its target, condition, input or independently acceptable completion.",
        "If a business object, descriptor or scope is already stated literally anywhere in the same USER_TEXT and the later outcome merely omits repeating it, that is same-turn ellipsis, not a dependency on the earlier result.",
        "A lookup needed only to turn an already-stated descriptor into an ID, artifact handle, transaction target or implementation input is execution-support dataflow and must not be retained as a dependency.",
        "Sentence order, then/然后/再/另外, shared topic/scope, likely execution order and capability availability are never dependency evidence.",
        "A later outcome that literally refers to the not-yet-produced earlier result with it/this/that/其中/这个/该结果, or a condition/value that explicitly consumes that result, may retain an edge when basis_span identifies that reference inside the dependent outcome.",
        "Do not infer tools, capabilities, database operations, IDs or transaction mechanics.",
        "clarify only if the user text itself cannot determine whether one user-observable outcome depends on another result; target membership or execution details are not enough.",
    ]
    proposed = [
        {
            "dependent_span": outcome_spans[dependent],
            "requires_result_of_span": outcome_spans[prerequisite],
        }
        for dependent, prerequisite in proposed_edges
    ]
    try:
        response, _trace = invoke_model(
            purpose="turn_goal_dependency_basis_verifier",
            model=get_model(),
            payload=structured_verifier_messages(
                role="turn_goal_dependency_basis_verifier",
                instruction=instruction,
                decision_rules=rules,
                payload={
                    "USER_TEXT_UNTRUSTED": user_text,
                    "FIXED_OUTCOME_SPANS": list(outcome_spans),
                    "PROPOSED_DEPENDENCY_EDGES": proposed,
                },
            ),
        )
    except Exception as exc:
        category = classify_model_failure(exc)
        if is_environmental_model_failure_category(category):
            raise
        return (), (), "goal_dependency_basis_audit_unavailable", GoalGranularityVerdict(
            "indeterminate",
            "goal_dependency_basis_audit_unavailable",
            (),
            "model_blind_dependency_audit",
            True,
            {"candidate_blind": True, "exception": exc.__class__.__name__, "error_category": category},
        )
    parsed = _extract_json(str(getattr(response, "content", response) or ""))
    if parsed is None:
        return (), (), "goal_dependency_basis_audit_non_json", GoalGranularityVerdict(
            "indeterminate",
            "goal_dependency_basis_audit_non_json",
            (),
            "model_blind_dependency_audit",
            True,
            {"candidate_blind": True},
        )
    verdict = _text(parsed.get("verdict"), limit=40).lower()
    if verdict == "clarify":
        return (), (), _text(parsed.get("reason_code"), limit=120) or "goal_dependency_basis_requires_clarification", GoalGranularityVerdict(
            "clarify",
            _text(parsed.get("reason_code"), limit=120) or "goal_dependency_basis_requires_clarification",
            (),
            "model_blind_dependency_audit",
            True,
            {"candidate_blind": True, "fixed_outcome_spans": list(outcome_spans)},
        )
    if verdict != "exact":
        return (), (), "goal_dependency_basis_audit_invalid_verdict", GoalGranularityVerdict(
            "indeterminate",
            "goal_dependency_basis_audit_invalid_verdict",
            (),
            "model_blind_dependency_audit",
            True,
            {"candidate_blind": True, "raw_verdict": verdict or None},
        )
    edges, basis_rows, error = _audited_dependency_edges(
        user_text,
        outcome_spans,
        parsed.get("dependency_edges"),
    )
    if error:
        return (), (), error, GoalGranularityVerdict(
            "indeterminate",
            error,
            (),
            "model_blind_dependency_audit",
            True,
            {"candidate_blind": True, "fixed_outcome_spans": list(outcome_spans)},
        )
    return edges, basis_rows, _text(parsed.get("reason_code"), limit=120) or "blind_dependency_basis_exact", None


class ModelGoalGranularityVerifier:
    """Candidate-blind outcome inventory plus a separate dependency-basis audit.

    The first model call sees only current USER_TEXT and inventories independent
    user-observable outcomes.  When that inventory already matches the declared
    outcome count, the second and final blind call is dedicated only to the true
    result-dependency graph over those fixed spans.  If the first inventory
    itself needs a candidate-blind repair, the final repair response must include
    the same dependency-basis evidence for its corrected spans.  Thus at most two
    model calls are used, no candidate Goal plan is disclosed, and the authority
    frozen for declaration repair is complete rather than moving later.
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
            "literal contiguous substring of USER_TEXT. dependency_edges are only a first-pass suggestion; Runtime independently audits dependency basis before freezing any multi-outcome authority."
        )
        rules = [
            "A separately requested unsupported/open business effect is still an outcome and must remain in the inventory.",
            "A supported outcome and an unsupported outcome in the same turn remain two outcomes when the customer can judge them separately.",
            "Filters, target selectors, ordering, cardinality, exclusions, reasons, dates, addresses, status predicates and other form values stay inside the outcome they constrain; do not inventory them as separate outcomes and do not ask to clarify their execution-time interpretation.",
            "Implementation/support steps, policy loading, permission checks, database work, Draft creation, authorization and rendering are never outcomes unless the customer explicitly requests them as a business result.",
            "Eligibility is a separate outcome only when the customer explicitly asks to receive that conclusion independently; otherwise it can be a condition/support step for an action.",
            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",
            "dependency_edges express true current-turn result dependency only; sentence order, shared topic/object/scope and execution-support dataflow do not create an edge.",
            "When a later outcome omits its target but an earlier phrase in the same USER_TEXT already names the reusable business object or scope, inherit that stated scope as ellipsis; that is not a dependency on the earlier Goal result by itself.",
            "A lookup needed only to convert an already-stated target into an ID/artifact/transaction input is implementation support, not semantic dependency.",
            "A later outcome that refers to the not-yet-produced earlier result or is explicitly conditional on that result may require an edge; the independent dependency audit certifies the basis.",
            "Return each independently acceptable requested result exactly once. Sibling outcome spans must be non-overlapping local spans; never emit both a target phrase and the business action over that same target as separate outcomes.",
            "clarify only when ambiguity changes the number or identity of independently requested business outcomes; target membership, filters, status vocabulary, thresholds, current facts and slot values are not granularity ambiguity.",
            "Never omit an outcome merely because it appears unsupported, unusual, unavailable or outside the current deployment.",
        ]
        final_repair_dependency_contract = (
            " This is the final candidate-blind repair response, so its dependency_edges must also be freeze-ready: return [] when the corrected outcomes are independent; for every retained edge include dependent_span, requires_result_of_span, basis_kind and basis_span. basis_kind must be result_reference, result_condition or result_value_input, and basis_span must be a literal substring inside the dependent outcome that specifically expresses use of the earlier current-turn result. Do not inspect candidate Goals, tools or capabilities."
        )
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
                        "Every outcome_span must be a local literal substring of USER_TEXT."
                        + final_repair_dependency_contract
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
                        "are identifiable, return exact with each independently acceptable result exactly once as non-overlapping literal spans."
                        + final_repair_dependency_contract
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
                        "Return the candidate-blind business-outcome inventory in the strict JSON contract: verdict exact|clarify, literal outcome_spans, dependency_edges and reason_code."
                        + final_repair_dependency_contract
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
                        "Return exact only with at least one local literal outcome_span from USER_TEXT. Inventory each independently acceptable business result exactly once."
                        + final_repair_dependency_contract
                    )
                    continue
                return last_indeterminate
            first_edges, dependency_error = _literal_dependency_edges(
                user_text, outcome_spans, parsed.get("dependency_edges")
            )
            if dependency_error:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", dependency_error, (), "model_blind_inventory", True,
                    {"candidate_blind": True, "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return dependency_edges as an array using only the literal outcome_spans."
                        + final_repair_dependency_contract
                    )
                    continue
                return last_indeterminate

            matched, _goal_to_outcome = _maximum_outcome_goal_matching(outcome_spans, goals)
            structural_outcome_match = matched == len(outcome_spans) == len(goals)
            if attempt == 0 and structural_outcome_match and len(outcome_spans) > 1:
                audited_edges, basis_rows, audit_reason, audit_failure = _run_dependency_basis_audit(
                    user_text=user_text,
                    outcome_spans=outcome_spans,
                    proposed_edges=first_edges,
                )
                if audit_failure is not None:
                    return audit_failure
                authority = _build_inventory_authority(
                    user_text=user_text,
                    outcome_spans=outcome_spans,
                    dependency_edges=audited_edges,
                    reason_code=audit_reason,
                    blind_self_audit_attempted=True,
                    dependency_edge_basis=basis_rows,
                    dependency_basis_audited=True,
                )
                return _evaluate_blind_inventory(
                    user_text=user_text,
                    goals=goals,
                    outcome_spans=outcome_spans,
                    dependency_edges=audited_edges,
                    authority=authority,
                    authority_reused=False,
                )

            if attempt > 0 and len(outcome_spans) > 1:
                audited_edges, basis_rows, basis_error = _audited_dependency_edges(
                    user_text,
                    outcome_spans,
                    parsed.get("dependency_edges"),
                )
                if basis_error:
                    return GoalGranularityVerdict(
                        "indeterminate",
                        basis_error,
                        (),
                        "model_blind_inventory",
                        True,
                        {
                            "candidate_blind": True,
                            "verifier_repair_attempted": True,
                            "outcome_spans": list(outcome_spans),
                            "dependency_basis_audited": False,
                        },
                    )
                authority = _build_inventory_authority(
                    user_text=user_text,
                    outcome_spans=outcome_spans,
                    dependency_edges=audited_edges,
                    reason_code=_text(parsed.get("reason_code"), limit=120) or "blind_inventory_repaired_and_dependency_audited",
                    blind_self_audit_attempted=True,
                    dependency_edge_basis=basis_rows,
                    dependency_basis_audited=True,
                )
                return _evaluate_blind_inventory(
                    user_text=user_text,
                    goals=goals,
                    outcome_spans=outcome_spans,
                    dependency_edges=audited_edges,
                    authority=authority,
                    authority_reused=False,
                )

            authority = _build_inventory_authority(
                user_text=user_text,
                outcome_spans=outcome_spans,
                dependency_edges=(),
                reason_code=_text(parsed.get("reason_code"), limit=120) or "blind_inventory_exact",
                blind_self_audit_attempted=attempt > 0,
                dependency_edge_basis=(),
                dependency_basis_audited=len(outcome_spans) <= 1,
            )
            verdict = _evaluate_blind_inventory(
                user_text=user_text,
                goals=goals,
                outcome_spans=outcome_spans,
                dependency_edges=(),
                authority=authority,
                authority_reused=False,
            )
            if verdict.exact or attempt > 0:
                return verdict
            verifier_repair = (
                "Run a candidate-blind self-audit of USER_TEXT only. Return each independently acceptable business result exactly once. "
                "Do not duplicate a target phrase and its enclosing business action as two outcomes. Filters, status predicates, target selectors, ordering, exclusions, cardinality and form values stay inside the outcome they constrain. "
                "A later omitted target may inherit an explicitly stated same-turn business object/scope without depending on an earlier result."
                + final_repair_dependency_contract
            )
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
    frozen_authority = _inventory_authority_from_state(state)
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
    "GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION",
    "GoalGranularityVerdict",
    "GoalGranularityVerifier",
    "ModelGoalGranularityVerifier",
    "CandidateOnlyGoalGranularityVerifier",
    "_goal_granularity_mode",
    "verify_goal_granularity",
]
