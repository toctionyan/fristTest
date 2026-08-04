"""Draft preparation helpers for fixed ecommerce write capabilities."""
from __future__ import annotations

from typing import Any

from agent_core.business import BusinessServiceError
from agent_core.ledger import active_entries, append_entries, offer_entry, scope_for_state
from agent_core.resources.targets import TargetResolver
from agent_core.runtime.outcomes import from_tool_result, outcome
from agent_core.transaction import is_reusable_draft, transition_draft
from agent_core.transaction.coordinator import persist_draft_from_offer
from agent_core.transaction.interaction import interaction_response_contract
from agent_core.transaction.operation_preparation import OperationPreparationRuntime

from .context import (
    _actor_context_from_state,
    _business_service_error,
    _error,
    _fresh_order_from_handle,
    _ok,
    _require_span,
    _runtime_registry,
    _target_members,
    _turn,
    business_port,
)

def _require_current_action_evidence(state: dict[str, Any], action_span: str) -> dict[str, Any] | None:
    """Generic evidence check; it never interprets action words or negation.

    Whether a user asks, cancels, corrects or applies is the single planner's
    semantic responsibility.  The program merely proves that the Planner did
    not invent the cited current-turn span.  Actual writes still require an
    explicit human confirmation over a rendered Offer.
    """
    return _require_span(state, action_span, field="业务动作原文")

def _normalized_offer_inputs(values: dict[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dict(values or {}).items() if k not in {"action_span", "action_turn", "expected_version"}}


def _existing_equivalent_offer(state: dict[str, Any], *, action_id: str, target_handle: str, inputs: dict[str, Any]) -> dict[str, Any] | None:
    expected = _normalized_offer_inputs(inputs)
    for offer in active_entries(state.get("artifact_ledger") or [], scope=scope_for_state(state), kind="offer"):
        if not is_reusable_draft(offer):
            continue
        if str(offer.get("action_id") or "") == action_id and str(offer.get("target_handle") or "") == str(target_handle) and _normalized_offer_inputs(dict(offer.get("input_values") or {})) == expected:
            return offer
    return None


def _required_inputs(preview_or_offer: dict[str, Any]) -> list[dict[str, Any]]:
    preview = preview_or_offer.get("preview") if isinstance(preview_or_offer.get("preview"), dict) else preview_or_offer
    rows = preview.get("required_inputs") if isinstance(preview, dict) else None
    if not rows and isinstance(preview_or_offer.get("required_inputs"), list):
        rows = preview_or_offer.get("required_inputs")
    return [dict(item) for item in rows or [] if isinstance(item, dict) and str(item.get("name") or "").strip()]


def _stored_offer_inputs(*, inputs: dict[str, Any], action_span: str, state: dict[str, Any], row: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
    """Return only durable business inputs.

    ``action_span`` is current-turn authorization evidence, not a business
    form field.  Keeping it outside ``input_values`` prevents it from leaking
    into preview/commit payloads or being reinterpreted after a refresh.
    """
    return {
        **dict(inputs or {}),
        "expected_version": int((preview.get("snapshot") or {}).get("version") or row.get("version") or 1),
    }


def _action_evidence(*, action_span: str, state: dict[str, Any]) -> dict[str, Any]:
    return {"span": str(action_span), "turn": _turn(state)}


def _preview_order_action(state: dict[str, Any], *, action_id: str, operation: str, order_handle: str, inputs: dict[str, Any], source: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    row, error, additions, _ = _fresh_order_from_handle(state, order_handle, source=source)
    if error:
        return None, error, additions, None
    assert row is not None
    plugin = _runtime_registry().operations.get(action_id)
    if plugin is None:
        return row, _error("UNKNOWN_CONTEXTUAL_ACTION", "当前不支持该业务申请动作。"), additions, None
    try:
        payload = plugin.preview(
            business_port(),
            _actor_context_from_state(state),
            target={"resource_type": "order", "resource_id": str(row.get("order_id")), "order_id": str(row.get("order_id"))},
            input_values=inputs,
        )
    except BusinessServiceError as exc:
        return row, _business_service_error("PREVIEW_FAILED", exc), additions, None
    if not payload.get("success"):
        return row, _error("PREVIEW_FAILED", str(payload.get("error") or "业务预览失败")), additions, None
    return row, None, additions, dict(payload.get("data") or {})


def _prepare_order_offer(
    state: dict[str, Any],
    *,
    action_id: str,
    operation: str,
    order_handle: str,
    inputs: dict[str, Any],
    label: str,
    action_span: str,
    capability_snapshot: dict[str, Any] | None = None,
    suggested_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    guard = _require_current_action_evidence(state, action_span)
    if guard:
        return guard
    existing = _existing_equivalent_offer(state, action_id=action_id, target_handle=order_handle, inputs=inputs)
    if existing:
        draft_state = str(existing.get("draft_state") or "")
        data = {
            "preview": dict(existing.get("preview") or {}),
            "offer_created": False,
            "offer_handle": existing["handle"],
            "offer_label": existing.get("label"),
        }
        if draft_state == "NEEDS_INPUT":
            data["needs_input"] = True
            data["required_inputs"] = _required_inputs(existing)
        else:
            data["offer_reused"] = True
        return _ok(data)
    row, error, additions, preview = _preview_order_action(state, action_id=action_id, operation=operation, order_handle=order_handle, inputs=inputs, source=f"prepare:{action_id}")
    if error:
        return error
    assert row is not None and preview is not None
    decision = str(preview.get("decision") or "")
    stored_inputs = _stored_offer_inputs(inputs=inputs, action_span=action_span, state=state, row=row, preview=preview)
    if decision == "NEEDS_INPUT":
        offer = offer_entry(action_id=action_id, operation=operation, target_handle=order_handle, input_values=stored_inputs, preview=preview, scope=scope_for_state(state), turn=_turn(state), label=label)
        offer["action_evidence"] = _action_evidence(action_span=action_span, state=state)
        if capability_snapshot:
            offer["operation_capability_snapshot"] = dict(capability_snapshot)
            offer["operation_capability_id"] = str(capability_snapshot.get("capability_id") or "")
            offer["operation_capability_version"] = str(capability_snapshot.get("version") or "")
            offer["operation_capability_digest"] = str(capability_snapshot.get("digest") or "")
        if suggested_input:
            offer["suggested_input"] = dict(suggested_input)
        offer = transition_draft(offer, "NEEDS_INPUT")
        offer["required_inputs"] = _required_inputs(preview)
        # Semantic field metadata belongs to the ActionDraft contract, not a
        # particular page.  Clients may render it as a web card, native form,
        # voice prompt or another interaction without knowing this operation.
        offer["input_schema"] = _required_inputs(preview)
        # Draft persistence is independent of the Graph checkpoint; Ledger
        # retains only a projection/evidence carrier.
        persist_draft_from_offer(state=state, offer=offer, draft_state="NEEDS_INPUT")
        return _ok({
            "preview": preview,
            "offer_created": False,
            "needs_input": True,
            "offer_handle": offer["handle"],
            "offer_label": label,
            "required_inputs": offer["required_inputs"],
        }, entries=[*additions, offer])
    if decision not in {"ALLOWED", "NEEDS_REVIEW"}:
        return _ok({"preview": preview, "offer_created": False}, entries=additions)
    offer = offer_entry(action_id=action_id, operation=operation, target_handle=order_handle, input_values=stored_inputs, preview=preview, scope=scope_for_state(state), turn=_turn(state), label=label)
    offer["action_evidence"] = _action_evidence(action_span=action_span, state=state)
    if capability_snapshot:
        offer["operation_capability_snapshot"] = dict(capability_snapshot)
        offer["operation_capability_id"] = str(capability_snapshot.get("capability_id") or "")
        offer["operation_capability_version"] = str(capability_snapshot.get("version") or "")
        offer["operation_capability_digest"] = str(capability_snapshot.get("digest") or "")
    if suggested_input:
        offer["suggested_input"] = dict(suggested_input)
    offer = transition_draft(offer, str(offer.get("draft_state") or "READY"))
    persist_draft_from_offer(state=state, offer=offer, draft_state="READY")
    return _ok({"preview": preview, "offer_created": True, "offer_handle": offer["handle"], "offer_label": label}, entries=[*additions, offer])


def _operation_label(action_id: str, plugin_label: str) -> str:
    return {
        "create_refund": "退款申请",
        "create_after_sales_request": "售后申请",
        "create_invoice": "开票申请",
    }.get(action_id, plugin_label)


def _contextual_inputs_for_plugin(
    plugin: Any,
    state: dict[str, Any],
    args: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
    """Return persisted inputs, non-persistent suggestions, and validation error.

    Chat is evidence-only: free-form model/chat spans may offer a
    suggested value to the structured interaction but never populate
    ``Draft.input_values`` or satisfy a required field.
    """
    suggestions: dict[str, Any] = {}
    for field in getattr(plugin, "input_schema", ()) or ():
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "")
        label = str(field.get("label") or name)
        if name == "reason":
            text = str(args.get("reason_span") or "")
            if text:
                error = _require_span(state, text, field=label)
                if error:
                    return None, {}, error
                suggestions[name] = {"value": text, "evidence_span": text}
        elif name == "reason_code":
            code, error = _structured_reason_code(state, args, plugin=plugin)
            if error:
                return None, {}, error
            if code:
                suggestions[name] = {"value": code, "evidence_span": str(args.get("reason_code_span") or "")}
        elif name == "invoice_title":
            text = str(args.get("invoice_title_span") or "")
            if text:
                error = _require_span(state, text, field=label)
                if error:
                    return None, {}, error
                suggestions[name] = {"value": text, "evidence_span": text}
        elif name in args and args.get(name) not in (None, ""):
            # Raw free-form planner args may be suggestions but not transaction input.
            suggestions[name] = {"value": args.get(name), "evidence_span": ""}
    if str(getattr(plugin, "business_operation", "")) == "APPLY_AFTER_SALES":
        service_type, error = _structured_service_type(state, args)
        if error:
            return None, {}, error
        if service_type:
            suggestions["service_type"] = {"value": service_type, "evidence_span": str(args.get("service_type_span") or "")}
    return {}, suggestions, None

def _structured_service_type(state: dict[str, Any], args: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    service_type = str(args.get("service_type") or "").strip()
    evidence_span = str(args.get("service_type_span") or "").strip()
    if not service_type and not evidence_span:
        return None, None
    if service_type not in {"repair", "exchange", "return", "general"}:
        return None, _error("INVALID_SERVICE_TYPE", "售后类型必须是业务 Schema 允许的结构化值。")
    evidence = _require_span(state, evidence_span, field="售后类型原文")
    if evidence:
        return None, evidence
    return service_type, None


def _allowed_reason_codes(plugin: Any | None) -> set[str]:
    """Read enum values from the action plugin, never a local language map."""
    if plugin is None:
        return set()
    for field in getattr(plugin, "input_schema", ()) or ():
        if not isinstance(field, dict) or str(field.get("name") or "") != "reason_code":
            continue
        values: set[str] = set()
        for option in field.get("options") or []:
            value = option.get("value") if isinstance(option, dict) else option
            if str(value or "").strip():
                values.add(str(value).strip())
        return values
    return set()


def _structured_reason_code(
    state: dict[str, Any],
    args: dict[str, Any],
    *,
    plugin: Any | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Accept only an explicit model enum backed by a current-turn span.

    If the model cannot classify the reason reliably, leave the value empty.
    The persisted draft then exposes the plugin's structured choice field and
    becomes NEEDS_INPUT instead of guessing from Chinese label substrings.
    """
    reason_code = str(args.get("reason_code") or "").strip()
    evidence_span = str(args.get("reason_code_span") or "").strip()
    if not reason_code:
        return None, None
    allowed = _allowed_reason_codes(plugin)
    if not allowed or reason_code not in allowed:
        return None, _error("INVALID_REASON_CODE", "问题类型必须是当前业务 Schema 允许的结构化值。")
    if not evidence_span:
        return None, _error("REASON_CODE_SPAN_REQUIRED", "模型预填问题类型时必须提供当前用户原文证据。")
    evidence = _require_span(state, evidence_span, field="问题类型原文")
    if evidence:
        return None, evidence
    return reason_code, None


def _transaction_context_available(state: dict[str, Any]) -> dict[str, Any] | None:
    health = state.get("context_health") if isinstance(state.get("context_health"), dict) else {}
    if str(health.get("transactions") or "ok") != "ok":
        return _error(
            "TRANSACTION_CONTEXT_UNAVAILABLE",
            "当前无法确认此前办理状态。为避免重复办理，本次不会创建新的业务申请；请稍后刷新或在事务中心查看。",
        )
    return None

def _runtime_result(state: dict[str, Any], tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Attach the closed runtime outcome at the decision boundary."""
    if not isinstance(result, dict):
        result = _error("SKILL_RUNTIME_ERROR", "工具未返回结构化结果。")
    if not isinstance(result.get("runtime_outcome"), dict):
        result["runtime_outcome"] = from_tool_result(
            tool_name=tool_name,
            result=result,
            correlation_id=str(state.get("correlation_id") or "") or None,
        ).as_dict()
    return result


def _target_set_from_info(state: dict[str, Any], target_info: dict[str, Any], *, resource_type: str = "order"):
    target = target_info.get("target") if isinstance(target_info.get("target"), dict) else {}
    mode = str(target_info.get("mode") or target.get("mode") or "collection")
    basis_map = {
        "artifact": "explicit_handle",
        "collection": "collection",
        "set_operation": "set_operation",
        "all_orders": "all_orders",
        "entity_match": "entity_match",
        "pipeline": "controlled_pipeline",
    }
    evidence = list(target_info.get("member_handles") or [])
    for key in ("left_handle", "right_handle", "source_handle"):
        if target.get(key):
            evidence.append(str(target.get(key)))
    return TargetResolver(_runtime_registry().resources).from_verified_members(
        resource_type=resource_type,
        handles=list(target_info.get("member_handles") or []),
        source=mode,
        evidence_handles=evidence,
        resolution_basis=basis_map.get(mode, "collection"),
        resolved_at_turn=_turn(state),
    )


def _prepare_ecommerce_operation(state: dict[str, Any], args: dict[str, Any], *, action_id: str, tool_name: str) -> dict[str, Any]:
    # A pending form/authority cannot be modified, cancelled or superseded by
    # free chat.  The user may still issue read-only queries in the same turn;
    # only a new write request is redirected to the existing structured card.
    existing_interaction = interaction_response_contract(state)
    if existing_interaction is not None:
        interaction = existing_interaction.get("interaction") if isinstance(existing_interaction.get("interaction"), dict) else {}
        redirect = {
            "ok": False,
            "code": "INTERACTION_REDIRECT",
            "message": "当前已有待办理事项，需要在办理卡中补充、确认或取消；聊天文字不会修改或提交该申请。",
            "data": {"interaction_id": interaction.get("interaction_id")},
        }
        return _runtime_result(state, tool_name, redirect)
    context_error = _transaction_context_available(state)
    if context_error:
        return _runtime_result(state, tool_name, context_error)
    action = str(action_id or "")
    if action not in _runtime_registry().preparable_action_ids():
        return _runtime_result(state, tool_name, _error("MODULE_OPERATION_NOT_REGISTERED", "当前模块未注册该业务申请动作。"))
    reference_error = _require_span(state, str(args.get("reference_span") or ""), field="当前引用")
    if reference_error:
        return _runtime_result(state, tool_name, reference_error)
    action_error = _require_current_action_evidence(state, str(args.get("action_span") or ""))
    if action_error:
        return _runtime_result(state, tool_name, action_error)

    # Resolve a collection first.  Capability validation owns the
    # cardinality decision before preview or Draft creation.
    target_info, target_error = _target_members(
        state,
        args.get("target") if isinstance(args.get("target"), dict) else {},
        expected_shape="collection",
        allowed_resource_types={"order"},
        target_authority="write",
    )
    if target_error:
        return _runtime_result(state, tool_name, target_error)
    assert target_info is not None
    target_set = _target_set_from_info(state, target_info)
    prepared, capability_outcome = OperationPreparationRuntime(outcome_factory=outcome).prepare(
        action_id=action,
        target_set=target_set,
        correlation_id=str(state.get("correlation_id") or "") or None,
    )
    if capability_outcome is not None:
        return {
            "ok": False,
            "code": "UNSUPPORTED_TARGET_CARDINALITY" if capability_outcome.outcome_type == "unsupported_cardinality" else "UNSUPPORTED_OPERATION_CAPABILITY",
            "message": capability_outcome.customer_safe_summary,
            "data": {"target_set": target_set.as_dict(), **dict(capability_outcome.payload)},
            "ledger_entries": list(target_info.get("entries") or []),
            "sources": [],
            "runtime_outcome": capability_outcome.as_dict(),
        }
    assert prepared is not None
    # A valid capability is exactly_one, so only now may the runtime
    # choose the single resolved handle and execute preview.
    order_handle = str(prepared.target_set.handles[0])
    working_state = {**state, "artifact_ledger": append_entries(state.get("artifact_ledger") or [], list(target_info.get("entries") or []))}
    values, suggested_input, value_error = _contextual_inputs_for_plugin(prepared.plugin, state, args)
    if value_error:
        return _runtime_result(state, tool_name, value_error)
    assert values is not None
    result = _prepare_order_offer(
        working_state,
        action_id=prepared.plugin.action_id,
        operation=prepared.plugin.business_operation,
        order_handle=order_handle,
        inputs=values,
        label=_operation_label(prepared.plugin.action_id, prepared.plugin.label),
        action_span=str(args.get("action_span") or ""),
        capability_snapshot=prepared.capability_snapshot,
        suggested_input=suggested_input,
    )
    return _runtime_result(state, tool_name, result)



def execute_prepare_cancel_order(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _prepare_ecommerce_operation(state, args, action_id="cancel_order", tool_name="prepare_cancel_order")


def execute_prepare_after_sales_request(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _prepare_ecommerce_operation(state, args, action_id="create_after_sales_request", tool_name="prepare_after_sales_request")


def execute_prepare_refund(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _prepare_ecommerce_operation(state, args, action_id="create_refund", tool_name="prepare_refund")


def execute_prepare_invoice(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _prepare_ecommerce_operation(state, args, action_id="create_invoice", tool_name="prepare_invoice")
