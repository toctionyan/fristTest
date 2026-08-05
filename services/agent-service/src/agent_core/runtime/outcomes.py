from __future__ import annotations

"""Discriminated runtime outcomes for customer-visible conclusions.

Low-level tools may continue returning raw facts.  The runtime converts those
facts at the decision boundary into this closed outcome vocabulary so the
renderer never treats an arbitrary ``ok=true`` payload as business success.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_core.kernel.outcome_contract import (
    FAIL_CLOSED_CUSTOMER_SUMMARY,
    OUTCOME_TYPES,
)


Effect = Literal["none", "draft_created", "input_required", "authority_required", "submitted", "committed", "unknown"]
NextInteraction = Literal["none", "open_form", "open_authority", "show_status", "need_selection", "retry_later"]

@dataclass(frozen=True)
class RuntimeOutcome:
    outcome_type: str
    effects: Effect
    safe_to_continue: bool
    correlation_id: str | None
    evidence_handles: tuple[str, ...]
    customer_safe_summary: str
    next_interaction: NextInteraction = "none"
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome_type": self.outcome_type,
            "effects": self.effects,
            "safe_to_continue": self.safe_to_continue,
            "correlation_id": self.correlation_id,
            "evidence_handles": list(self.evidence_handles),
            "customer_safe_summary": self.customer_safe_summary,
            "next_interaction": self.next_interaction,
            "payload": dict(self.payload),
        }


# Closed discriminated names.  These aliases make callers explicit without
# forcing a broad inheritance tree or loose dict conventions.
NarrativeOutcome = RuntimeOutcome
QueryOutcome = RuntimeOutcome
ClarificationOutcome = RuntimeOutcome
UnsupportedCapabilityOutcome = RuntimeOutcome
UnsupportedCardinalityOutcome = RuntimeOutcome
PreviewRejectedOutcome = RuntimeOutcome
DraftCreatedOutcome = RuntimeOutcome
InputRequiredOutcome = RuntimeOutcome
AuthorityRequiredOutcome = RuntimeOutcome
TransactionStatusOutcome = RuntimeOutcome
CommitOutcome = RuntimeOutcome
SubmissionUnknownOutcome = RuntimeOutcome
SystemUnavailableOutcome = RuntimeOutcome
FailureOutcome = RuntimeOutcome


_VALID_EFFECTS = {"none", "draft_created", "input_required", "authority_required", "submitted", "committed", "unknown"}
_VALID_NEXT_INTERACTIONS = {"none", "open_form", "open_authority", "show_status", "need_selection", "retry_later"}


def coerce_runtime_outcome(value: Any, *, correlation_id: str | None = None) -> RuntimeOutcome | None:
    """Validate an outcome crossing a runtime/presentation boundary.

    Graph state is serialized as dictionaries, so this function is the
    discriminated-union gate for deserialized or externally assembled values.
    Unknown/malformed values become a fail-closed outcome rather than a
    narrative fallback.
    """
    if value is None:
        return None
    data = value.as_dict() if isinstance(value, RuntimeOutcome) else (dict(value) if isinstance(value, dict) else None)
    if data is None:
        return fail_closed_outcome(correlation_id=correlation_id, reason="malformed_runtime_outcome")
    kind = str(data.get("outcome_type") or "")
    summary = str(data.get("customer_safe_summary") or "").strip()
    if kind not in OUTCOME_TYPES or not summary:
        return fail_closed_outcome(correlation_id=correlation_id or str(data.get("correlation_id") or "") or None, reason="invalid_runtime_outcome")
    effects = str(data.get("effects") or "none")
    next_interaction = str(data.get("next_interaction") or "none")
    if effects not in _VALID_EFFECTS or next_interaction not in _VALID_NEXT_INTERACTIONS:
        return fail_closed_outcome(correlation_id=correlation_id or str(data.get("correlation_id") or "") or None, reason="invalid_runtime_outcome_contract")
    return outcome(
        kind,
        effects=effects,
        safe_to_continue=bool(data.get("safe_to_continue")),
        correlation_id=correlation_id or str(data.get("correlation_id") or "") or None,
        evidence_handles=list(data.get("evidence_handles") or []),
        customer_safe_summary=summary,
        next_interaction=next_interaction,
        payload=dict(data.get("payload") or {}),
    )


def _handles(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values or () if str(value).strip()))


def outcome(
    outcome_type: str,
    *,
    effects: Effect = "none",
    safe_to_continue: bool = False,
    correlation_id: str | None = None,
    evidence_handles: list[str] | tuple[str, ...] | None = None,
    customer_safe_summary: str,
    next_interaction: NextInteraction = "none",
    payload: dict[str, Any] | None = None,
) -> RuntimeOutcome:
    kind = str(outcome_type or "").strip()
    if kind not in OUTCOME_TYPES:
        # Do not raise here: this path can execute while rendering an error.
        # Preserve only a safe diagnostic in the internal payload.
        kind = "failure"
        effects = "none"
        safe_to_continue = False
        customer_safe_summary = FAIL_CLOSED_CUSTOMER_SUMMARY
        next_interaction = "retry_later"
        payload = {"reason": "unknown_runtime_outcome", "received_outcome_type": str(outcome_type or "")}
    return RuntimeOutcome(
        outcome_type=kind,
        effects=effects,
        safe_to_continue=bool(safe_to_continue),
        correlation_id=str(correlation_id or "") or None,
        evidence_handles=_handles(evidence_handles),
        customer_safe_summary=str(customer_safe_summary),
        next_interaction=next_interaction,
        payload=dict(payload or {}),
    )


def fail_closed_outcome(*, correlation_id: str | None = None, reason: str = "unknown_runtime_outcome") -> RuntimeOutcome:
    return outcome(
        "failure",
        effects="none",
        safe_to_continue=False,
        correlation_id=correlation_id,
        customer_safe_summary=FAIL_CLOSED_CUSTOMER_SUMMARY,
        next_interaction="retry_later",
        payload={"reason": str(reason)},
    )


def is_outcome(value: Any) -> bool:
    if isinstance(value, RuntimeOutcome):
        return value.outcome_type in OUTCOME_TYPES and bool(value.customer_safe_summary)
    if not isinstance(value, dict):
        return False
    return (
        str(value.get("outcome_type") or "") in OUTCOME_TYPES
        and bool(str(value.get("customer_safe_summary") or "").strip())
        and str(value.get("effects") or "none") in _VALID_EFFECTS
        and str(value.get("next_interaction") or "none") in _VALID_NEXT_INTERACTIONS
    )


def from_tool_result(
    *,
    tool_name: str,
    result: dict[str, Any],
    correlation_id: str | None = None,
) -> RuntimeOutcome:
    """Create a conservative, customer-safe outcome for a safe-skill result."""
    payload = dict(result.get("data") or {}) if isinstance(result.get("data"), dict) else {}
    evidence = []
    for key in ("result_handle", "offer_handle", "eligibility_handle", "transaction_handle"):
        if payload.get(key):
            evidence.append(str(payload[key]))
    if not result.get("ok"):
        code = str(result.get("code") or "")
        message = str(result.get("message") or "")
        if code in {"UNKNOWN_OR_UNSUPPORTED_TOOL", "UNSUPPORTED_CAPABILITY"}:
            return outcome(
                "unsupported_capability",
                correlation_id=correlation_id,
                customer_safe_summary="当前系统未提供与该请求匹配的能力，未执行任何业务操作。",
                next_interaction="none",
                payload={"code": code},
            )
        if code == "UNSUPPORTED_TARGET_CARDINALITY":
            return outcome(
                "unsupported_cardinality",
                correlation_id=correlation_id,
                customer_safe_summary=message or "当前操作不支持一次处理多个对象；未创建或提交任何业务申请。",
                next_interaction="need_selection",
                payload={"code": code, **payload},
            )
        if code in {"TRANSACTION_CONTEXT_UNAVAILABLE", "TRANSACTION_REPOSITORY_UNAVAILABLE"}:
            return outcome(
                "system_unavailable",
                correlation_id=correlation_id,
                customer_safe_summary="当前无法确认或继续该办理记录，系统未创建或提交新的业务申请。请稍后刷新，或在事务中心查看已有记录。",
                next_interaction="retry_later",
                payload={"code": code},
            )
        if code == "INTERACTION_REDIRECT":
            return outcome(
                "interaction_redirect",
                correlation_id=correlation_id,
                customer_safe_summary="当前已有待办理事项；聊天文字不会修改、不会提交，也不会取消草稿。请在办理卡中补充、确认或取消。",
                next_interaction="open_form",
                payload={"interaction_id": payload.get("interaction_id")},
            )
        if code in {"SOURCE_SPAN_NOT_IN_CURRENT_USER_MESSAGE", "REASON_CODE_SPAN_REQUIRED", "INVALID_REASON_CODE"}:
            return outcome(
                "failure",
                correlation_id=correlation_id,
                customer_safe_summary="当前未获得可用于继续办理的明确业务依据；未创建或提交任何业务申请。请重新说明需要处理的事项。",
                next_interaction="none",
                payload={"code": code},
            )
        if code in {"CONTEXT_TARGET_NOT_UNIQUE", "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE", "NEED_TRANSACTION_SELECTION"}:
            return outcome(
                "clarification",
                correlation_id=correlation_id,
                customer_safe_summary=message or "当前存在多个可处理对象，请先选择其中一个。",
                next_interaction="need_selection",
                payload={"code": code, "candidates": list(result.get("candidates") or [])},
            )
        # Source-span, tool-stack and internal error text never becomes public.
        return outcome(
            "failure",
            correlation_id=correlation_id,
            customer_safe_summary="当前请求无法安全完成，未创建或提交任何业务申请。请重新说明需要查询或办理的事项。",
            next_interaction="none",
            payload={"code": code},
        )

    if bool(payload.get("offer_created")) or bool(payload.get("needs_input")) or "offer_handle" in payload:
        if payload.get("needs_input"):
            return outcome(
                "input_required",
                effects="input_required",
                safe_to_continue=True,
                correlation_id=correlation_id,
                evidence_handles=evidence,
                customer_safe_summary="已创建待办理草稿，还需要在办理卡中补充结构化信息；聊天文字不会自动写入或提交该申请。",
                next_interaction="open_form",
                payload=payload,
            )
        if payload.get("offer_created") or payload.get("offer_reused"):
            return outcome(
                "draft_created",
                effects="draft_created",
                safe_to_continue=True,
                correlation_id=correlation_id,
                evidence_handles=evidence,
                customer_safe_summary="已创建业务办理草稿，尚未提交任何业务申请。后续需要通过结构化办理卡继续。",
                next_interaction="open_authority",
                payload=payload,
            )
        return outcome(
            "preview_rejected",
            correlation_id=correlation_id,
            customer_safe_summary="当前业务预检未允许创建申请，未提交任何业务操作。",
            payload=payload,
        )
    if tool_name == "query_transaction_lifecycle":
        return outcome(
            "transaction_status",
            correlation_id=correlation_id,
            evidence_handles=evidence,
            customer_safe_summary=str(payload.get("message") or "已查询办理状态。"),
            next_interaction="show_status",
            payload=payload,
        )
    if bool(result.get("ok")) and not bool(payload.get("offer_created")) and not bool(payload.get("needs_input")):
        # Use the existing deterministic, evidence-backed renderer before the
        # result acquires a runtime_outcome.  This preserves concrete order,
        # logistics and policy facts instead of collapsing every successful
        # observation into a misleading generic success sentence.
        from agent_core.presentation.grounded import render_single_grounded_tool_result

        summary = render_single_grounded_tool_result(tool_name, result)
        return outcome(
            "clarification" if tool_name == "ask_context_clarification" else "query",
            correlation_id=correlation_id,
            evidence_handles=evidence,
            customer_safe_summary=summary,
            next_interaction="need_selection" if tool_name == "ask_context_clarification" else "none",
            payload=payload,
        )
    if tool_name == "report_unsupported_request":
        return outcome(
            "unsupported_capability",
            correlation_id=correlation_id,
            customer_safe_summary=str(payload.get("message") or "当前系统未提供与该请求匹配的能力，未执行任何业务操作。"),
            payload=payload,
        )
    return fail_closed_outcome(correlation_id=correlation_id, reason=f"unmapped_tool:{tool_name}")
