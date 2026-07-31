from __future__ import annotations

"""Canonical customer presentation after the application coerce_runtime_outcome boundary.

API fields are projections, not truths; Runtime remains the only semantic
coercion and fail-closed outcome authority.
"""

from dataclasses import dataclass
from typing import Any, Literal

from agent_core.kernel.outcome_contract import (
    FAIL_CLOSED_CUSTOMER_SUMMARY,
    OUTCOME_TYPES,
    outcome_mapping,
)
from agent_core.presentation.contracts.runtime import project_transaction_status


PresentationMode = Literal["narrative", "structured", "interaction", "transaction_status", "notice"]


@dataclass(frozen=True)
class Presentation:
    mode: PresentationMode
    summary: str | None
    primary: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "summary": self.summary,
            "primary": dict(self.primary or {}) if self.primary else None,
        }


def _fail_closed_presentation() -> Presentation:
    return Presentation(
        "notice",
        FAIL_CLOSED_CUSTOMER_SUMMARY,
        {"type": "notice", "tone": "error", "content": FAIL_CLOSED_CUSTOMER_SUMMARY},
    )


def presentation_from_outcome(value: Any) -> Presentation | None:
    """Project an already-normalized RuntimeOutcome without owning it.

    The application boundary calls Runtime coercion before this function.  A
    direct malformed caller is still fail-closed, but Presentation never
    invents or repairs an execution outcome.
    """
    if value is None:
        return None
    data = outcome_mapping(value)
    if data is None:
        return _fail_closed_presentation()
    outcome_type = str(data.get("outcome_type") or "")
    summary = str(data.get("customer_safe_summary") or "").strip()
    if outcome_type not in OUTCOME_TYPES or not summary:
        return _fail_closed_presentation()
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    correlation_id = str(data.get("correlation_id") or "") or None
    if outcome_type in {"input_required", "authority_required", "draft_created", "interaction_redirect"}:
        return Presentation("interaction", summary, None)
    if outcome_type == "transaction_status":
        return Presentation(
            "transaction_status",
            summary,
            project_transaction_status(
                summary=summary or "办理状态",
                data=payload,
                trace_id=correlation_id,
            ),
        )
    if outcome_type in {
        "unsupported_cardinality", "unsupported_capability", "system_unavailable",
        "failure", "clarification", "preview_rejected",
    }:
        return Presentation(
            "notice",
            summary,
            {
                "type": "notice",
                "tone": "warning" if outcome_type != "failure" else "error",
                "content": summary,
            },
        )
    if outcome_type == "query":
        return Presentation("structured", summary, None)
    return Presentation("narrative", summary, None)
