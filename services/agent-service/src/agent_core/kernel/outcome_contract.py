from __future__ import annotations

"""Stable, read-only vocabulary for RuntimeOutcome projection boundaries.

Runtime remains the only owner of outcome construction and coercion.  This
module only prevents downstream projection layers from importing the runtime
implementation merely to read the closed vocabulary or an ``as_dict`` view.
"""

from collections.abc import Mapping
from typing import Any, Protocol

OUTCOME_TYPES = frozenset({
    "narrative", "query", "clarification", "unsupported_capability",
    "unsupported_cardinality", "preview_rejected", "draft_created",
    "input_required", "authority_required", "transaction_status", "commit",
    "submission_unknown", "system_unavailable", "failure", "interaction_redirect",
})

FAIL_CLOSED_CUSTOMER_SUMMARY = (
    "系统未获得可继续办理的明确结果；未确认创建或提交任何业务申请。"
    "请刷新后查看事务中心，或重新说明需要查询的事项。"
)


class OutcomeReadModel(Protocol):
    """Read-only structural contract implemented by RuntimeOutcome."""

    outcome_type: str
    effects: str
    safe_to_continue: bool
    correlation_id: str | None
    evidence_handles: tuple[str, ...]
    customer_safe_summary: str
    next_interaction: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]: ...


class OutcomeFactory(Protocol):
    """Runtime-owned callable injected into downstream execution boundaries."""

    def __call__(
        self,
        outcome_type: str,
        *,
        effects: str = "none",
        safe_to_continue: bool = False,
        correlation_id: str | None = None,
        evidence_handles: list[str] | tuple[str, ...] | None = None,
        customer_safe_summary: str,
        next_interaction: str = "none",
        payload: dict[str, Any] | None = None,
    ) -> OutcomeReadModel: ...


def outcome_mapping(value: Any) -> dict[str, Any] | None:
    """Return a detached mapping from a RuntimeOutcome-like read model.

    The helper performs no semantic correction and creates no outcome.  Runtime
    owns those decisions.  Downstream callers use this only after the runtime
    boundary has normalized the value, with a defensive fail-closed projection
    for malformed direct callers.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        data = as_dict()
        return dict(data) if isinstance(data, Mapping) else None
    return None


__all__ = ["OUTCOME_TYPES", "FAIL_CLOSED_CUSTOMER_SUMMARY", "OutcomeReadModel", "OutcomeFactory", "outcome_mapping"]
