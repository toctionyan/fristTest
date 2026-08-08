from __future__ import annotations

"""Independent review of Goal granularity before capability discovery.

The review decides only whether model-declared Goals are user-observable
business outcomes.  It cannot inspect available tools, choose capabilities or
rewrite an unsupported business effect into a nearby one.
"""

from dataclasses import dataclass
import json
import re
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


class ModelGoalGranularityVerifier:
    def verify(
        self,
        *,
        user_text: str,
        goals: list[dict[str, Any]],
    ) -> GoalGranularityVerdict:
        from agent_core.config import get_model
        from agent_core.model_calls import invoke_model, structured_verifier_messages

        instruction = (
            "Judge whether each declared Goal is a user-observable independently acceptable business outcome. "
            "Do not inspect or infer available tools. Do not rewrite "
            "requested_effect. Mark over_split when an item is only a target "
            "constraint, input, condition, permission check, policy read, "
            "implementation/support step, transaction step, or presentation "
            "step. "
            "Mark under_split when distinct user-requested outcomes were collapsed. Return JSON only."
        )
        rules = [
            (
                "A Goal is independently observable when the user could "
                "separately judge success/failure, cancel/correct it, or require "
                "a separate completion proof."
            ),
            "Filters, ordering, cardinality and exclusions belong to the target expression, not separate Goals.",
            "Reasons, dates, addresses, email and similar form values are inputs, not separate Goals.",
            (
                "Eligibility is a separate Goal only when the user asks to "
                "receive that conclusion as an independent result; otherwise it "
                "may be a precondition/support step for a conditional action."
            ),
            (
                "Authorization, idempotency, ownership checks, policy loading, "
                "database calls, Draft creation and rendering are never user "
                "Goals unless explicitly requested as a business result."
            ),
            "Business-system implementation oddities must not change semantic Goal boundaries.",
            (
                "Use verdict exact|under_split|over_split|mixed|clarify. "
                "findings items: goal_id, reason, recommended_role, "
                "evidence_span."
            ),
        ]
        response, _trace = invoke_model(
            purpose="turn_goal_granularity_verifier",
            model=get_model(),
            payload=structured_verifier_messages(
                role="turn_goal_granularity_verifier",
                instruction=instruction,
                decision_rules=rules,
                payload={"USER_TEXT_UNTRUSTED": user_text, "DECLARED_GOALS": goals},
            ),
        )
        parsed = _extract_json(str(getattr(response, "content", response) or ""))
        if parsed is None:
            return GoalGranularityVerdict(
                "indeterminate",
                "goal_granularity_non_json",
                (),
                "model",
                True,
                {},
            )
        return _normalize(
            parsed,
            user_text=user_text,
            goal_ids={str(row.get("goal_id") or "") for row in goals},
            source="model",
            independent=True,
        )


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
