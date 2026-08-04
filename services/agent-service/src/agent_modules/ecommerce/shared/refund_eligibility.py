"""Refund eligibility and promotion helpers."""
from __future__ import annotations

from typing import Any

from agent_core.ledger import append_entries, eligibility_entry, find_handle, scope_for_state
from agent_core.resources.targets import TargetResolver
from agent_core.transaction.interaction import interaction_response_contract
from agent_core.transaction.operation_preparation import OperationPreparationRuntime
from agent_core.runtime.outcomes import outcome

from .context import _error, _ok, _require_current_or_resumed_span, _require_span, _runtime_registry, _target_members, _turn
from .prepare_actions import (
    _prepare_order_offer,
    _preview_order_action,
    _require_current_action_evidence,
    _runtime_result,
    _structured_reason_code,
    _transaction_context_available,
)

def execute_evaluate_refund_eligibility(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    error = _require_span(state, str(args.get("reference_span") or ""), field="当前引用")
    if error:
        return error
    error = _require_current_or_resumed_span(
        state, str(args.get("question_span") or ""), field="退款资格询问",
    )
    if error:
        return error
    reason_span = str(args.get("reason_span") or "")
    if reason_span:
        error = _require_current_or_resumed_span(state, reason_span, field="退款原因")
        if error:
            return error
    # The current Planner decides whether this is a qualification question;
    # the program has already verified question_span belongs to the user text.
    target, target_error = _target_members(state, args.get("target") if isinstance(args.get("target"), dict) else {}, expected_shape="one", allowed_resource_types={"order"}, target_authority="decision")
    if target_error:
        return target_error
    assert target is not None
    order_handle = str(target["member_handles"][0])
    working_state = {**state, "artifact_ledger": append_entries(state.get("artifact_ledger") or [], list(target.get("entries") or []))}
    reason = str(args.get("reason_span") or "")
    values = {"reason": reason}
    reason_code, reason_code_error = _structured_reason_code(
        state, args, plugin=_runtime_registry().operations.get("create_refund")
    )
    if reason_code_error:
        return reason_code_error
    if reason_code:
        values["reason_code"] = reason_code
    row, error, additions, preview = _preview_order_action(working_state, action_id="create_refund", operation="APPLY_REFUND", order_handle=order_handle, inputs=values, source="module:ecommerce.refund.eligibility")
    if error:
        return error
    assert row is not None and preview is not None
    eligible = str(preview.get("decision") or "") in {"ALLOWED", "NEEDS_REVIEW"}
    if not eligible:
        return _ok({"preview": preview, "eligible": False}, entries=additions)
    eligibility = eligibility_entry(action_id="create_refund", operation="APPLY_REFUND", target_handle=order_handle, input_values={**values, "expected_version": int((preview.get("snapshot") or {}).get("version") or row.get("version") or 1)}, preview=preview, scope=scope_for_state(state), turn=_turn(state), label="退款资格核验")
    return _ok({"preview": preview, "eligible": True, "eligibility_handle": eligibility["handle"], "target_label": row.get("product_name")}, entries=[*additions, eligibility])


def _prepare_refund_from_eligibility(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    # Eligibility is read-only evidence.  It must not become a side door that
    # supersedes a pending structured interaction through free chat.
    existing_interaction = interaction_response_contract(state)
    if existing_interaction is not None:
        interaction = existing_interaction.get("interaction") if isinstance(existing_interaction.get("interaction"), dict) else {}
        return _runtime_result(state, "prepare_refund_from_eligibility", {
            "ok": False,
            "code": "INTERACTION_REDIRECT",
            "message": "当前已有待办理事项，需要在办理卡中补充、确认或取消；聊天文字不会修改或提交该申请。",
            "data": {"interaction_id": interaction.get("interaction_id")},
        })
    context_error = _transaction_context_available(state)
    if context_error:
        return context_error
    action_span = str(args.get("action_span") or "")
    evidence = _require_current_action_evidence(state, action_span)
    if evidence:
        return evidence
    eligibility = find_handle(state.get("artifact_ledger") or [], str(args.get("eligibility_handle") or ""), scope=scope_for_state(state), allowed_kinds={"eligibility"})
    if not eligibility or str(eligibility.get("status") or "") != "eligible":
        return _error("INVALID_ELIGIBILITY", "退款资格核验不存在、已过期或不属于当前会话。")
    if str(eligibility.get("action_id") or "") != "create_refund":
        return _error("ELIGIBILITY_ACTION_MISMATCH", "该资格核验不能用于退款申请。")
    target_set = TargetResolver(_runtime_registry().resources).from_verified_members(
        resource_type="order",
        handles=[str(eligibility.get("target_handle") or "")],
        source="eligibility",
        evidence_handles=[str(eligibility.get("handle") or "")],
        resolution_basis="explicit_handle",
        resolved_at_turn=_turn(state),
    )
    prepared, capability_outcome = OperationPreparationRuntime(outcome_factory=outcome).prepare(
        action_id="create_refund",
        target_set=target_set,
        correlation_id=str(state.get("correlation_id") or "") or None,
    )
    if capability_outcome is not None:
        return _runtime_result(state, "prepare_refund_from_eligibility", {
            "ok": False,
            "code": "UNSUPPORTED_OPERATION_CAPABILITY",
            "message": capability_outcome.customer_safe_summary,
            "data": {"target_set": target_set.as_dict()},
        })
    assert prepared is not None
    # Eligibility is read-only evidence.  Values observed in an earlier chat
    # turn must not silently become persisted Draft input in the current runtime.  Fresh
    # preview runs with empty form values and the structured interaction owns
    # any required reason or reason_code.
    return _runtime_result(state, "prepare_refund_from_eligibility", _prepare_order_offer(
        state, action_id="create_refund", operation="APPLY_REFUND",
        order_handle=str(eligibility.get("target_handle") or ""),
        inputs={},
        label="退款申请", action_span=action_span, capability_snapshot=prepared.capability_snapshot,
    ))
