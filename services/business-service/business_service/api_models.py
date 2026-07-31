"""HTTP request contracts owned by the Business Service API boundary."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .domain import ISSUE_REASON_OPTIONS


CANCEL_REASON_OPTIONS = (
    {"value": "not_needed", "label": "不想要了"},
    {"value": "duplicate", "label": "重复下单"},
    {"value": "bad_reputation", "label": "口碑不好"},
    {"value": "other", "label": "其他"},
)


def _cancel_reason_input() -> dict[str, Any]:
    return {
        "name": "reason",
        "label": "取消原因",
        "input_kind": "select",
        "allow_custom": True,
        "step": 1,
        "step_title": "选择取消原因",
        "options": [dict(item) for item in CANCEL_REASON_OPTIONS],
    }


def _issue_type_input() -> dict[str, Any]:
    return {
        "name": "reason_code",
        "label": "问题类型",
        "input_kind": "select",
        "allow_custom": True,
        "step": 1,
        "step_title": "选择问题类型",
        "options": [dict(item) for item in ISSUE_REASON_OPTIONS],
    }


def _issue_description_input(label: str) -> dict[str, Any]:
    return {
        "name": "reason",
        "label": label,
        "input_kind": "textarea",
        "placeholder": "请描述具体情况，例如“收到时杯角有破损”。",
        "step": 2,
        "step_title": "描述问题",
    }

# ------------------------------ request models ------------------------------
class OrderQueryRequest(BaseModel):
    user_id: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    aggregate: dict[str, Any] = Field(default_factory=dict)
    answer_mode: str = "list"


class LogisticsQueryRequest(BaseModel):
    """Authoritative, parameterized logistics query.

    The Agent may select this normal business read only when its declared
    filters can fully express the requested condition.  Filters are applied in
    this service's SQL query; callers must not download an unfiltered
    population and claim to have performed a server-side filtered query.
    """

    user_id: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    answer_mode: str = "list"


class CancelOrderRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
    expected_version: int = Field(ge=1)


class AddressChangeRequest(BaseModel):
    address: str = Field(min_length=5, max_length=300)
    expected_version: int = Field(ge=1)


class DeliveryUrgeRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ApplicationBase(BaseModel):
    # ``user_id`` is retained only as a short migration alias for existing UI/
    # Agent payloads. It never controls the actor. subject_user_id makes
    # on-behalf business explicit and is authorization-checked server-side.
    subject_user_id: str | None = None
    user_id: str | None = None

    @property
    def requested_subject(self) -> str | None:
        return self.subject_user_id or self.user_id


class AfterSalesCreateRequest(ApplicationBase):
    order_id: str
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=1000)
    reason_code: Literal["QUALITY_ISSUE", "SPEC_MISMATCH", "WRONG_ITEM", "OTHER"] | None = None


class RefundCreateRequest(ApplicationBase):
    order_id: str
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=1000)
    reason_code: Literal["QUALITY_ISSUE", "SPEC_MISMATCH", "WRONG_ITEM", "OTHER"] | None = None


class InvoiceCreateRequest(ApplicationBase):
    order_id: str
    expected_version: int = Field(ge=1)
    invoice_title: str = Field(min_length=2, max_length=200)


class ComplaintCreateRequest(ApplicationBase):
    reason: str = Field(min_length=2, max_length=2000)


class HumanHandoffCreateRequest(ApplicationBase):
    reason: str = Field(min_length=1, max_length=2000)


class CommandRequest(BaseModel):
    command: str = Field(min_length=2, max_length=64)
    expected_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1000)


class OperationPreviewRequest(BaseModel):
    """Normal business capability projection for any UI or Agent.

    The caller asks whether a named business operation can run against the
    latest server-owned resource state. This endpoint performs no mutation.
    """

    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=128)
    operation: str = Field(min_length=2, max_length=64)
    input: dict[str, Any] = Field(default_factory=dict)


class OperationCommandRequest(BaseModel):
    """Stable Agent/UI command envelope; authority comes from authenticated actor."""

    contract: Literal["business.operation.command@1"]
    # Command identity is an integrity/reconciliation reference only.  It is
    # never an authorization override and business idempotency remains keyed
    # by authenticated tenant/actor/command name plus the idempotency header.
    command_id: str | None = Field(default=None, min_length=8, max_length=160)
    action_id: str = Field(min_length=2, max_length=128)
    operation: str = Field(min_length=2, max_length=128)
    target: dict[str, Any] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    actor_scope: dict[str, Any] = Field(default_factory=dict)


class LegacyReviewRequest(BaseModel):
    """Deliberately rejected legacy shape.

    Keeping a typed model lets old callers receive a precise migration error
    instead of accidentally retaining status/reviewer write capability.
    """

    status: str
    operator_note: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None


# ------------------------------ helpers ------------------------------------
