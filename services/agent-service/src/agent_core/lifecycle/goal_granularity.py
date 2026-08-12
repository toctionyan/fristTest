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


GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION = "goal-granularity-inventory-authority@3"


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
    reason_code: str,
    blind_self_audit_attempted: bool,
    active_structured_interaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze candidate-blind outcome decomposition, never dependencies."""
    payload: dict[str, Any] = {
        "version": GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION,
        "user_text_sha256": sha256(str(user_text or "").encode("utf-8")).hexdigest(),
        "outcome_spans": list(outcome_spans),
        "authority_scope": "outcome_inventory_only",
        "dependency_authority": "independent_goal_alignment",
        "reason_code": _text(reason_code, limit=120) or "blind_inventory_exact",
        "source": "model_blind_inventory",
        "independent": True,
        "candidate_blind": True,
        "blind_self_audit_attempted": bool(blind_self_audit_attempted),
        "active_structured_interaction_digest": _canonical_digest(dict(active_structured_interaction or {})),
    }
    payload["integrity_digest"] = _canonical_digest(payload)
    return payload


def _validate_inventory_authority(
    *,
    user_text: str,
    authority: Any,
    active_structured_interaction: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, tuple[str, ...], str | None]:
    if not isinstance(authority, dict):
        return None, (), "goal_granularity_inventory_authority_required"
    if str(authority.get("version") or "") != GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION:
        return None, (), "goal_granularity_inventory_authority_version_invalid"
    stored_digest = str(authority.get("integrity_digest") or "").strip()
    if not stored_digest:
        return None, (), "goal_granularity_inventory_authority_digest_required"
    digest_payload = dict(authority)
    digest_payload.pop("integrity_digest", None)
    if _canonical_digest(digest_payload) != stored_digest:
        return None, (), "goal_granularity_inventory_authority_digest_invalid"
    expected_user_digest = sha256(str(user_text or "").encode("utf-8")).hexdigest()
    if str(authority.get("user_text_sha256") or "") != expected_user_digest:
        return None, (), "goal_granularity_inventory_authority_user_text_mismatch"
    expected_interaction_digest = _canonical_digest(dict(active_structured_interaction or {}))
    if str(authority.get("active_structured_interaction_digest") or "") != expected_interaction_digest:
        return None, (), "goal_granularity_inventory_authority_interaction_mismatch"
    if authority.get("candidate_blind") is not True or authority.get("independent") is not True:
        return None, (), "goal_granularity_inventory_authority_not_independent"
    if str(authority.get("source") or "") != "model_blind_inventory":
        return None, (), "goal_granularity_inventory_authority_source_invalid"
    if str(authority.get("authority_scope") or "") != "outcome_inventory_only":
        return None, (), "goal_granularity_inventory_authority_scope_invalid"
    if str(authority.get("dependency_authority") or "") != "independent_goal_alignment":
        return None, (), "goal_granularity_inventory_authority_dependency_owner_invalid"
    raw_spans = authority.get("outcome_spans")
    if not isinstance(raw_spans, list):
        return None, (), "goal_granularity_inventory_authority_outcome_spans_invalid"
    exact_raw_spans = tuple(str(value) for value in raw_spans if str(value))
    outcome_spans = _literal_outcome_spans(user_text, raw_spans)
    if not outcome_spans or exact_raw_spans != outcome_spans:
        return None, (), "goal_granularity_inventory_authority_outcome_spans_not_literal"
    return dict(authority), outcome_spans, None


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
    authority: dict[str, Any],
    authority_reused: bool,
) -> GoalGranularityVerdict:
    matched, goal_to_outcome = _maximum_outcome_goal_matching(outcome_spans, goals)
    goal_count = len(goals)
    outcome_count = len(outcome_spans)
    details = {
        "candidate_blind": True,
        "authority_scope": "outcome_inventory_only",
        "dependency_authority": "independent_goal_alignment",
        "inventory_outcome_count": outcome_count,
        "declared_goal_count": goal_count,
        "matched_outcome_count": matched,
        "outcome_spans": list(outcome_spans),
        "blind_self_audit_attempted": bool(authority.get("blind_self_audit_attempted")),
        "inventory_authority_reused": bool(authority_reused),
        "inventory_authority": dict(authority),
    }
    if matched == outcome_count == goal_count:
        return GoalGranularityVerdict(
            "exact",
            str(authority.get("reason_code") or "blind_inventory_exact"),
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
                "evidence_span": span,
            })
    for goal_index, goal in enumerate(goals):
        if goal_index not in matched_goals:
            findings.append({
                "goal_id": str(goal.get("goal_id") or "") or None,
                "reason": "declared_goal_not_uniquely_mapped_to_blind_outcome",
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
    """Candidate-blind user-observable outcome inventory.

    Dependency semantics are owned by the independent GoalAlignmentVerifier.
    A second call here is decomposition-only self-audit, never dependency
    re-judgment.
    """

    def verify(
        self,
        *,
        user_text: str,
        goals: list[dict[str, Any]],
        active_structured_interaction: dict[str, Any] | None = None,
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
            "JSON only with verdict (exact|clarify), outcome_spans and reason_code. Every outcome_span must be a local "
            "literal contiguous substring of USER_TEXT. Do not judge or emit a Goal dependency graph; dependency "
            "semantics are owned by a separate independent alignment proof."
        )
        rules = [
            "A separately requested unsupported/open business effect is still an outcome and must remain in the inventory.",
            "A supported outcome and an unsupported outcome in the same turn remain two outcomes when the customer can judge them separately.",
            "Filters, target selectors, ordering, cardinality, exclusions, reasons, dates, addresses, status predicates and other form values stay inside the outcome they constrain; do not inventory them as separate outcomes and do not ask to clarify their execution-time interpretation.",
            "Implementation/support steps, policy loading, permission checks, database work, Draft creation, authorization and rendering are never outcomes unless the customer explicitly requests them as a business result.",
            "Eligibility is a separate outcome only when the customer explicitly asks to receive that conclusion independently; otherwise it can be a condition/support step for an action.",
            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",
            "A meta-level refusal, deferral or suppression of a prior optional action (for example asking not to proceed, submit or handle it for now) is interaction control, not a separately judgeable business outcome, when there is no matching ACTIVE_STRUCTURED_INTERACTION and the user does not request a business effect on an identified existing object.",
            "A direct business-effect request to cancel/delete/stop an identified existing business object remains an outcome. When ACTIVE_STRUCTURED_INTERACTION identifies a pending user-visible interaction and USER_TEXT explicitly cancels or stops that pending interaction, preserve that control outcome; do not absorb a separate read-only query into it.",
            "Do not inspect or re-judge any candidate Goal dependency declaration, execution order, IDs, tools, capability availability or transaction mechanics.",
            "Return each independently acceptable requested result exactly once. Sibling outcome spans must be non-overlapping local spans; never emit both a target phrase and the business action over that same target as separate outcomes.",
            "clarify only when ambiguity changes the number or identity of independently requested business outcomes; target membership, filters, status vocabulary, thresholds, current facts and slot values are not granularity ambiguity.",
            "Never omit an outcome merely because it appears unsupported, unusual, unavailable or outside the current deployment.",
        ]
        verifier_repair: str | None = None
        first_blind_outcome_spans: tuple[str, ...] = ()
        last_indeterminate = GoalGranularityVerdict(
            "indeterminate", "goal_granularity_inventory_unverified", (),
            "model_blind_inventory", True,
            {"candidate_blind": True, "authority_scope": "outcome_inventory_only"},
        )
        for attempt in range(2):
            verifier_payload: dict[str, Any] = {
                "USER_TEXT_UNTRUSTED": user_text,
                "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),
            }
            if attempt > 0 and first_blind_outcome_spans:
                # The second call remains candidate-blind. It sees only its own
                # first-pass hypotheses so it can challenge control/meta spans
                # instead of anchoring on the candidate Goal inventory.
                verifier_payload["FIRST_BLIND_OUTCOME_SPANS"] = list(first_blind_outcome_spans)
            try:
                response, _trace = invoke_model(
                    purpose="turn_goal_granularity_inventory_verifier",
                    model=get_model(),
                    payload=structured_verifier_messages(
                        role="turn_goal_granularity_inventory_verifier",
                        instruction=instruction,
                        decision_rules=rules,
                        payload=verifier_payload,
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
                    {
                        "exception": exc.__class__.__name__,
                        "error_category": category,
                        "candidate_blind": True,
                        "authority_scope": "outcome_inventory_only",
                    },
                )
            parsed = _extract_json(str(getattr(response, "content", response) or ""))
            if parsed is None:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", "goal_granularity_inventory_non_json", (),
                    "model_blind_inventory", True,
                    {"candidate_blind": True, "authority_scope": "outcome_inventory_only", "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return exactly one JSON object using only verdict, outcome_spans and reason_code; verdict must be exact or clarify, "
                        "every outcome_span must be a literal contiguous substring of USER_TEXT, and dependency edges must not be judged."
                    )
                    continue
                return last_indeterminate
            raw_verdict = _text(parsed.get("verdict"), limit=40).lower()
            if raw_verdict == "clarify":
                if attempt == 0:
                    verifier_repair = (
                        "Re-audit only candidate-blind outcome decomposition. Clarify is admissible only if ambiguity changes the number or identity "
                        "of independently requested business outcomes. target membership, filters/status vocabulary, thresholds, slot/form values, current facts and execution-time cardinality are downstream Runtime concerns, not outcome-granularity ambiguity. If boundaries are identifiable, return exact with each result once. "
                        "Return only verdict, outcome_spans and reason_code; do not judge dependency edges."
                    )
                    continue
                return GoalGranularityVerdict(
                    "clarify", _text(parsed.get("reason_code"), limit=120) or "blind_inventory_requires_clarification", (),
                    "model_blind_inventory", True,
                    {"candidate_blind": True, "authority_scope": "outcome_inventory_only", "verifier_repair_attempted": True},
                )
            if raw_verdict != "exact":
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", "goal_granularity_inventory_invalid_verdict", (),
                    "model_blind_inventory", True,
                    {"candidate_blind": True, "authority_scope": "outcome_inventory_only", "verifier_repair_attempted": attempt > 0, "raw_verdict": raw_verdict or None},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return the strict candidate-blind outcome inventory: verdict exact|clarify, literal outcome_spans and reason_code. "
                        "Do not judge dependency edges."
                    )
                    continue
                return last_indeterminate
            outcome_spans = _literal_outcome_spans(user_text, parsed.get("outcome_spans"))
            if not outcome_spans:
                last_indeterminate = GoalGranularityVerdict(
                    "indeterminate", "goal_granularity_inventory_missing_literal_spans", (),
                    "model_blind_inventory", True,
                    {"candidate_blind": True, "authority_scope": "outcome_inventory_only", "verifier_repair_attempted": attempt > 0},
                )
                if attempt == 0:
                    verifier_repair = (
                        "Return exact only with at least one local literal outcome_span from USER_TEXT. Inventory each business result once. "
                        "Do not judge dependency edges."
                    )
                    continue
                return last_indeterminate
            authority = _build_inventory_authority(
                user_text=user_text,
                outcome_spans=outcome_spans,
                reason_code=_text(parsed.get("reason_code"), limit=120) or "blind_inventory_exact",
                blind_self_audit_attempted=attempt > 0,
                active_structured_interaction=active_structured_interaction,
            )
            verdict = _evaluate_blind_inventory(
                user_text=user_text,
                goals=goals,
                outcome_spans=outcome_spans,
                authority=authority,
                authority_reused=False,
            )
            if verdict.exact or attempt > 0:
                return verdict
            first_blind_outcome_spans = outcome_spans
            verifier_repair = (
                "Adversarially re-audit the first candidate-blind outcome inventory from USER_TEXT only. FIRST_BLIND_OUTCOME_SPANS contains "
                "only your own first-pass literal hypotheses; it is not authority and contains no candidate Goal plan. Start each hypothesis "
                "as NOT an independently judgeable business outcome, then retain it only if the customer independently requests business "
                "information/result/change that can be judged complete or incomplete. A conversational refusal, deferral or suppression of "
                "execution (for example not proceeding/submitting/handling something for now) is interaction control rather than a second "
                "business outcome when ACTIVE_STRUCTURED_INTERACTION does not identify the pending interaction and no identified existing "
                "business object is itself being changed. In contrast, a direct cancel/delete/stop request on an identified existing business "
                "object remains an outcome, and an explicit cancel/stop of the supplied ACTIVE_STRUCTURED_INTERACTION remains a control outcome. "
                "Never prune a separately requested unsupported/open business effect merely because it is unusual or unavailable. Preserve a "
                "separate read-only query. Do not inspect candidate Goals, candidate count, tools, capabilities, oracle data or dependency edges. "
                "Return only verdict, outcome_spans and reason_code with each retained result exactly once."
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


def _active_structured_interaction_context(state: dict[str, Any]) -> dict[str, Any] | None:
    """Project only public pending-interaction identity for outcome inventory."""
    from agent_core.transaction.interaction import interaction_response_contract

    contract = interaction_response_contract(state)
    interaction = (
        contract.get("interaction")
        if isinstance(contract, dict) and isinstance(contract.get("interaction"), dict)
        else None
    )
    if interaction is None:
        return None
    return {
        "interaction_id": str(interaction.get("interaction_id") or ""),
        "lifecycle": str(interaction.get("lifecycle") or ""),
        "title": str(interaction.get("title") or ""),
        "target": str(interaction.get("target") or ""),
        "required_fields": [
            str(row.get("name") or "")
            for row in list(interaction.get("fields") or [])
            if isinstance(row, dict) and str(row.get("name") or "")
        ],
        "chat_write_authorized": False,
        "runtime_redirect_required": True,
    }


def verify_goal_granularity(
    *,
    state: dict[str, Any],
    goals: list[dict[str, Any]],
) -> GoalGranularityVerdict:
    user_text = _text(state.get("current_user_input"), limit=20_000)
    active_structured_interaction = _active_structured_interaction_context(state)
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
        validated_authority, outcome_spans, authority_error = _validate_inventory_authority(
            user_text=user_text,
            authority=frozen_authority,
            active_structured_interaction=active_structured_interaction,
        )
        if authority_error or validated_authority is None:
            return GoalGranularityVerdict(
                "indeterminate",
                authority_error or "goal_granularity_inventory_authority_invalid",
                (),
                "frozen_model_blind_inventory",
                True,
                {"candidate_blind": True, "authority_scope": "outcome_inventory_only", "inventory_authority_reused": True},
            )
        return _evaluate_blind_inventory(
            user_text=user_text,
            goals=goals,
            outcome_spans=outcome_spans,
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
        raw = (
            verifier.verify(
                user_text=user_text,
                goals=goals,
                active_structured_interaction=active_structured_interaction,
            )
            if isinstance(verifier, ModelGoalGranularityVerifier)
            else verifier.verify(user_text=user_text, goals=goals)
        )
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
