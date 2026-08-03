"""Formal ecommerce customer-service capability overlay: observations, drafts, and policy consultation.

The LLM owns the natural-language interpretation of references, collections,
differences, ordering and topic switches.  It expresses that interpretation as
a high-level contextual plan.  This plugin never contains a word-list for
pronouns or relationship phrases; it only evaluates typed, scope-checked set
expressions over verified ledger handles and calls the business service.

No tool accepts a raw business id.  The model can only reference opaque handles
created by an authority-backed prior tool result.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from agent_core.business import ActorContext, get_business_port
from agent_modules.ecommerce.contracts import public_capability_labels
from agent_core.config import retrieval_min_score, retrieval_top_k
from agent_core.composition import get_runtime_registry as _runtime_registry
from agent_core.business import BusinessServiceError
from agent_core.rag.retriever import retrieve
from agent_core.rag.access import normalize_scope, scope_filter
from agent_core.ledger import (
    active_entries,
    append_entries,
    artifact_entry,
    eligibility_entry,
    find_handle,
    offer_entry,
    result_entry,
    scope_for_state,
    view_entry,
)
from agent_core.security.roles import can
from agent_core.transaction import is_reusable_draft, transition_draft
from agent_core.transaction.coordinator import persist_draft_from_offer
from agent_core.transaction.operation_preparation import OperationPreparationRuntime
from agent_core.transaction.lifecycle_query import TransactionLifecycleQuery
from agent_core.resources.targets import TargetResolver
from agent_core.runtime.outcomes import from_tool_result
from agent_core.context.visible_result_refs import validate_runtime_result_ref
from agent_core.storage.repositories.base import TransactionLifecycleRepository
from agent_core.transaction.interaction import interaction_response_contract
from agent_core.lifecycle.goal_blockers import active_goal_blockers



from agent_modules.ecommerce.schemas import (
    CONSTRAINT_BINDINGS_SCHEMA,
    LOGISTICS_QUERY_SCHEMA,
    TARGET_SCHEMA,
)

def business_port():
    """Resolve the enabled module business port at call time.

    Keeping this indirection in the module context slice makes test and
    composition injection explicit without reviving the old execution facade.
    """
    return get_business_port()


def _normal(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _in_current_turn(state: dict[str, Any], span: str) -> bool:
    needle = _normal(span)
    return bool(needle) and needle in _normal(state.get("current_user_input"))


def _turn(state: dict[str, Any]) -> int:
    return int(state.get("turn_index") or 0)


def _read_user_id(state: dict[str, Any]) -> str | None:
    role = str(state.get("current_role") or "customer")
    return None if can(role, "business:read_any") else str(state.get("current_user_id") or "")


def _actor_context_from_state(state: dict[str, Any]) -> ActorContext:
    return ActorContext(
        user_id=str(state.get("current_user_id") or ""),
        role=str(state.get("current_role") or "customer"),
        tenant_id=str(state.get("current_tenant_id") or "") or None,
        subject_user_id=str(state.get("current_subject") or state.get("current_user_id") or "") or None,
        subject=str(state.get("current_subject") or state.get("current_user_id") or "") or None,
        permissions=tuple(str(item) for item in (state.get("actor_permissions") or []) if str(item)),
    )


def _error(code: str, message: str, *, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "code": code, "message": message}
    if candidates is not None:
        payload["candidates"] = candidates
    return payload


def _business_service_error(default_code: str, exc: BusinessServiceError) -> dict[str, Any]:
    """Project Adapter transport semantics into the finite Loop disposition.

    The Adapter has already exhausted only its bounded *read* retries.  This
    overlay does not retry by selecting another tool or by changing a user
    target.  A possible write acknowledgement loss is explicitly preserved as
    submission-unknown for the transaction runtime.
    """
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    if bool(payload.get("submission_unknown")):
        return _error("SUBMISSION_UNKNOWN", "业务系统尚未返回提交结果；系统不会重复提交，将按原提交记录对账。")
    if int(exc.status_code or 0) in {502, 503, 504} or bool(payload.get("retry_exhausted")):
        return _error("TRANSPORT_RETRY_EXHAUSTED", "业务服务暂时不可用，已完成有限重试；未改用其他目标或能力。")
    return _error(default_code, exc.message)


def _ok(
    data: dict[str, Any],
    *,
    entries: list[dict[str, Any]] | None = None,
    confirmation_requested: str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": data,
        "ledger_entries": entries or [],
        "confirmation_requested": confirmation_requested,
        "sources": list(sources or []),
    }


def _fresh_order_artifact(*, state: dict[str, Any], row: dict[str, Any], source: str, existing_handle: str | None = None) -> dict[str, Any]:
    order_id = str(row.get("order_id") or "")
    return artifact_entry(
        resource_type="order",
        resource_id=order_id,
        label=f"{row.get('product_name') or '订单'}（订单 {order_id}）",
        facts=dict(row),
        scope=scope_for_state(state),
        turn=_turn(state),
        source=source,
        freshness_version=int(row.get("version") or 1),
        handle=existing_handle,
    )


def _upsert_artifact(entries: list[dict[str, Any]], artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scope = artifact["scope"]
    for old in active_entries(entries, scope=scope, kind="artifact", resource_type=str(artifact.get("resource_type") or "")):
        if str(old.get("resource_id") or "") == str(artifact.get("resource_id") or ""):
            artifact["handle"] = old["handle"]
            break
    merged = append_entries(entries, [artifact])
    saved = find_handle(merged, artifact["handle"], scope=scope, allowed_kinds={"artifact"}, active_only=False) or artifact
    return merged, saved


def _list_orders(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        payload = business_port().query_resources(_actor_context_from_state(state), resource_type="order", query_spec={"user_id": _read_user_id(state)})
        if not payload.get("success"):
            return _error("BUSINESS_READ_FAILED", str(payload.get("error") or "订单服务返回失败")), []
        return {"ok": True, "rows": [dict(row) for row in payload.get("data") or [] if isinstance(row, dict)]}, []
    except BusinessServiceError as exc:
        return _business_service_error("BUSINESS_READ_FAILED", exc), []


def _match_orders(rows: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    needle = _normal(text)
    if not needle:
        return []
    digits = re.sub(r"\D", "", needle)
    needles = {needle}
    if len(needle) >= 2 and needle.endswith(("子", "儿")):
        needles.add(needle[:-1])
    matched: list[dict[str, Any]] = []
    for row in rows:
        values = [str(row.get("order_id") or ""), str(row.get("product_name") or ""), str(row.get("product_id") or "")]
        if digits and str(row.get("order_id") or "") == digits:
            matched.append(row)
            continue
        if any(candidate and (candidate in _normal(value) or _normal(value) in candidate) for candidate in needles for value in values if _normal(value)):
            matched.append(row)
    if matched:
        return matched

    # A planner may copy the complete predicate phrase into attribute_span
    # ("键盘售后政策") rather than only the entity token ("键盘").  Resolve
    # such spans exclusively against the finite, user-scoped catalog.  Keep
    # every equally good match so expected_shape still forces clarification
    # when the reference is not unique.
    embedded_classifier_aliases = {
        needle[index - 1]
        for index, char in enumerate(needle)
        if char in {"子", "儿"} and index > 0
    }
    if embedded_classifier_aliases:
        alias_matches = [
            row
            for row in rows
            if any(
                alias in _normal(row.get("product_name"))
                for alias in embedded_classifier_aliases
            )
        ]
        if alias_matches:
            return alias_matches

    def longest_common_span(left: str, right: str) -> int:
        if not left or not right:
            return 0
        previous = [0] * (len(right) + 1)
        best = 0
        for left_char in left:
            current = [0]
            for index, right_char in enumerate(right, start=1):
                value = previous[index - 1] + 1 if left_char == right_char else 0
                current.append(value)
                best = max(best, value)
            previous = current
        return best

    scored = [
        (
            row,
            max(
                longest_common_span(needle, _normal(row.get("product_name"))),
                longest_common_span(needle, _normal(row.get("product_id"))),
            ),
        )
        for row in rows
    ]
    best = max((score for _, score in scored), default=0)
    return [row for row, score in scored if best >= 2 and score == best]


_ORDER_STATUS_VALUES = {"待付款", "已付款", "待发货", "已发货", "运输中", "已签收", "已取消"}
_DELIVERY_STATUS_VALUES = {"待发货", "运输中", "派送中", "已签收", "已取消"}


def _require_span(state: dict[str, Any], span: str, *, field: str) -> dict[str, Any] | None:
    if not str(span or "").strip() or not _in_current_turn(state, str(span)):
        return _error("SOURCE_SPAN_NOT_IN_CURRENT_USER_MESSAGE", f"{field}必须来自当前用户原话。")
    return None


def _require_current_or_resumed_span(
    state: dict[str, Any], span: str, *, field: str,
) -> dict[str, Any] | None:
    """Accept current evidence or the exact suspended request on resume.

    A short clarification reply contributes the missing target, while the
    original question remains in the durable PendingClarification contract.
    Treating that verified source request as an illegal stale span made valid
    resumes fail after goal declaration had already accepted them.
    """
    if _in_current_turn(state, span):
        return None
    if str(span or "").strip() and any(
        _normal(span) in _normal(row.get("source_user_request"))
        for row in active_goal_blockers(state)
        if str(row.get("source_user_request") or "").strip()
    ):
        return None
    return _error("SOURCE_SPAN_NOT_IN_CURRENT_USER_MESSAGE", f"{field}必须来自当前用户原话或当前恢复的挂起请求。")


def _status_span_has_evidence(state: dict[str, Any], *, status: str, span: str) -> bool:
    if _in_current_turn(state, span):
        return True
    if _normal(span) != _normal(status):
        return False
    evidence = [status]
    if status.startswith("已") and len(status) > 1:
        body = status[1:]
        evidence.extend([f"已经{body}", body])
    return any(_in_current_turn(state, item) for item in evidence if item)


def _logistics_query_filter(state: dict[str, Any], args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate a declared logistics condition without re-parsing user text.

    A delivery-state condition has a distinct namespace from an order status.
    It is only valid when the model binds it to the formal ``query`` parameter
    and supplies a literal current-turn evidence span.  The resulting filter is
    forwarded to the Business Service unchanged.
    """
    query = args.get("query") if isinstance(args.get("query"), dict) else {}
    delivery_status = str(query.get("delivery_status") or "").strip()
    has_dispatched = "dispatched" in query
    dispatched = query.get("dispatched")
    if delivery_status and has_dispatched:
        return {}, _error("LOGISTICS_FILTER_CONFLICT", "精确物流状态与是否已发出不能同时筛选。")
    if has_dispatched and not isinstance(dispatched, bool):
        return {}, _error("INVALID_LOGISTICS_FILTER_VALUE", "是否已发出必须是布尔值。")
    parameter_path = "query.delivery_status" if delivery_status else "query.dispatched"
    bindings = [
        row for row in list(args.get("constraint_bindings") or [])
        if isinstance(row, dict)
        and str(row.get("parameter_path") or "") == parameter_path
    ]
    if not delivery_status and not has_dispatched:
        return {}, None
    if delivery_status not in _DELIVERY_STATUS_VALUES:
        return {}, _error("INVALID_LOGISTICS_FILTER_VALUE", "物流状态筛选值不在当前能力合同允许范围内。")
    if len(bindings) != 1:
        return {}, _error("CONSTRAINT_BINDING_MISSING", "物流条件缺少唯一的原话约束绑定。")
    delivery_span = str(bindings[0].get("source_span") or "").strip()
    normalized = delivery_status if delivery_status else dispatched
    if "normalized_value" in bindings[0] and bindings[0].get("normalized_value") != normalized:
        return {}, _error("CONSTRAINT_BINDING_VALUE_MISMATCH", "物流条件绑定值与正式参数不一致。")
    evidence = _require_span(state, delivery_span, field="物流状态筛选")
    if evidence:
        return {}, evidence
    if delivery_status:
        return {"delivery_status": delivery_status}, None
    return {"dispatched": dispatched}, None


def _structured_status_filter(state: dict[str, Any], target: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Validate a model-selected status value without re-parsing language.

    ``status_span`` proves the user mentioned a filtering expression.  The
    planner alone maps that expression to ``status``.  The program only checks
    that the selected value belongs to the declared business enum.
    """
    status = str(target.get("status") or "").strip()
    span = str(target.get("status_span") or "").strip()
    if not status and not span:
        return None, None
    if not status:
        return None, _error("FILTER_VALUE_MISSING", "状态筛选计划缺少结构化状态值。")
    if status not in _ORDER_STATUS_VALUES:
        return None, _error("INVALID_STATUS_VALUE", "状态筛选值不在当前业务 Schema 允许范围内。")
    if not _status_span_has_evidence(state, status=status, span=span):
        return None, _error("SOURCE_SPAN_NOT_IN_CURRENT_USER_MESSAGE", "状态筛选必须来自当前用户原话。")
    return status, None


def _fresh_order_from_handle(state: dict[str, Any], handle: str, *, source: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]], str | None]:
    artifact = find_handle(state.get("artifact_ledger") or [], handle, scope=scope_for_state(state), allowed_kinds={"artifact"}, allowed_resource_types={"order"})
    if not artifact:
        return None, _error("INVALID_OR_UNSCOPED_HANDLE", "订单对象不存在、已过期，或不属于当前用户和会话。"), [], None
    try:
        payload = business_port().read_resource(_actor_context_from_state(state), resource_type="order", resource_id=str(artifact["resource_id"]), query={"user_id": _read_user_id(state)})
        if not payload.get("success") or not isinstance(payload.get("data"), dict):
            return None, _error("BUSINESS_READ_FAILED", str(payload.get("error") or "读取订单失败")), [], None
        updated = _fresh_order_artifact(state=state, row=dict(payload["data"]), source=source, existing_handle=artifact["handle"])
        return dict(payload["data"]), None, [updated], artifact["handle"]
    except BusinessServiceError as exc:
        return None, _business_service_error("BUSINESS_READ_FAILED", exc), [], None


def _collection_entry(state: dict[str, Any], handle: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    entry = find_handle(state.get("artifact_ledger") or [], handle, scope=scope_for_state(state), allowed_kinds={"view", "result"})
    if not entry:
        return None, _error("INVALID_OR_UNSCOPED_COLLECTION", "集合或查询结果不存在、已过期，或不属于当前用户和会话。")
    return entry, None


def _collection_members(state: dict[str, Any], handle: str, *, allowed_resource_types: set[str] | None = None) -> tuple[list[str] | None, dict[str, Any] | None]:
    entry, error = _collection_entry(state, handle)
    raw_members: list[str]
    if error:
        result_ref, result_ref_error = validate_runtime_result_ref(
            state=state,
            result_ref=handle,
            expected_shape="collection",
        )
        if result_ref_error is not None or result_ref is None:
            return None, error
        raw_members = [str(value) for value in list(result_ref.get("member_handles") or []) if str(value)]
    else:
        assert entry is not None
        raw_members = [str(value) for value in list(entry.get("member_handles") or []) if str(value)]
    members: list[str] = []
    for item_handle in raw_members:
        art = find_handle(state.get("artifact_ledger") or [], str(item_handle), scope=scope_for_state(state), allowed_kinds={"artifact"}, allowed_resource_types=allowed_resource_types)
        if art:
            members.append(art["handle"])
    return list(dict.fromkeys(members)), None


def _artifact_members(state: dict[str, Any], handle: str, *, allowed_resource_types: set[str] | None = None) -> tuple[list[str] | None, dict[str, Any] | None]:
    artifact = find_handle(state.get("artifact_ledger") or [], handle, scope=scope_for_state(state), allowed_kinds={"artifact"}, allowed_resource_types=allowed_resource_types)
    if artifact:
        return [artifact["handle"]], None

    # A one-shaped, release-backed result may structurally refer to one
    # underlying artifact (currently eligibility evidence is the principal
    # producer).  Resolve only the exact member declared by that validated
    # ResultRef; never infer a target from text or from arbitrary ledger data.
    result_ref, result_ref_error = validate_runtime_result_ref(
        state=state,
        result_ref=handle,
        expected_shape="one",
    )
    members = [str(value) for value in list((result_ref or {}).get("member_handles") or []) if str(value)]
    if result_ref_error is None and len(members) == 1:
        target = find_handle(
            state.get("artifact_ledger") or [],
            members[0],
            scope=scope_for_state(state),
            allowed_kinds={"artifact"},
            allowed_resource_types=allowed_resource_types,
        )
        if target:
            return [str(target["handle"])], None
    return None, _error("INVALID_OR_UNSCOPED_HANDLE", "对象不存在、已过期，或不属于当前用户和会话。")


def _dedup(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _set_operation_left_handle(target: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    left_handle = str(target.get("left_handle") or "").strip()
    if left_handle:
        return left_handle, None
    return None, _error("SET_OPERATION_LEFT_HANDLE_MISSING", "集合操作缺少 left_handle。")


def _make_order_artifacts(state: dict[str, Any], rows: list[dict[str, Any]], *, source: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    ledger = list(state.get("artifact_ledger") or [])
    artifacts: list[dict[str, Any]] = []
    handles: list[str] = []
    labels: list[str] = []
    for row in rows:
        artifact = _fresh_order_artifact(state=state, row=row, source=source)
        ledger, saved = _upsert_artifact(ledger, artifact)
        artifacts.append(saved)
        handles.append(saved["handle"])
        labels.append(saved["label"])
    return artifacts, _dedup(handles), labels


def _target_members(state: dict[str, Any], target: dict[str, Any], *, expected_shape: str, allowed_resource_types: set[str]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Evaluate a model-selected, generic contextual plan.

    This function does not inspect natural-language pronouns.  It validates
    current-turn evidence spans, ledger scopes and generic set operators.
    """
    if not isinstance(target, dict):
        return None, _error("INVALID_CONTEXT_TARGET", "上下文目标必须是结构化计划。")
    mode = str(target.get("mode") or "")
    scope = scope_for_state(state)
    additions: list[dict[str, Any]] = []
    member_handles: list[str] = []

    if mode == "all_orders":
        listed, _ = _list_orders(state)
        if not listed.get("ok"):
            return None, listed
        rows = list(listed.get("rows") or [])
        attribute_span = str(target.get("attribute_span") or "").strip()
        if attribute_span:
            evidence = _require_span(state, attribute_span, field="商品筛选")
            if evidence:
                return None, evidence
            rows = _match_orders(rows, attribute_span)
        status, status_error = _structured_status_filter(state, target)
        if status_error:
            return None, status_error
        if status:
            rows = [row for row in rows if str(row.get("status") or "") == status]
        arts, handles, _ = _make_order_artifacts(state, rows, source="contextual_all_orders")
        additions.extend(arts)
        member_handles = handles
    elif mode == "entity_match":
        attribute_span = str(target.get("attribute_span") or "").strip()
        evidence = _require_span(state, attribute_span, field="对象称呼")
        if evidence:
            return None, evidence
        listed, _ = _list_orders(state)
        if not listed.get("ok"):
            return None, listed
        rows = _match_orders(list(listed.get("rows") or []), attribute_span)
        arts, handles, _ = _make_order_artifacts(state, rows, source="contextual_entity_match")
        additions.extend(arts)
        member_handles = handles
    elif mode == "artifact":
        left_handle = str(target.get("left_handle") or "")
        members, error = _artifact_members(state, left_handle, allowed_resource_types=allowed_resource_types)
        if error:
            return None, error
        member_handles = members or []
    elif mode == "collection":
        left_handle = str(target.get("left_handle") or "")
        members, error = _collection_members(state, left_handle, allowed_resource_types=allowed_resource_types)
        if error:
            return None, error
        member_handles = members or []
    elif mode == "set_operation":
        operation = str(target.get("operator") or "")
        left_handle, left_handle_error = _set_operation_left_handle(target)
        if left_handle_error:
            return None, left_handle_error
        assert left_handle is not None
        left, error = _collection_members(state, left_handle, allowed_resource_types=allowed_resource_types)
        if error:
            return None, error
        assert left is not None
        if operation == "identity":
            member_handles = left
        elif operation in {"difference", "union", "intersection"}:
            right_handle = str(target.get("right_handle") or "")
            right, right_error = _collection_members(state, right_handle, allowed_resource_types=allowed_resource_types)
            if right_error:
                right, right_error = _artifact_members(
                    state,
                    right_handle,
                    allowed_resource_types=allowed_resource_types,
                )
            if right_error:
                return None, right_error
            assert right is not None
            right_set = set(right)
            if operation == "difference":
                member_handles = [value for value in left if value not in right_set]
            elif operation == "union":
                member_handles = _dedup([*left, *right])
            else:
                member_handles = [value for value in left if value in right_set]
        elif operation == "ordinal":
            position = int(target.get("position") or 0)
            if position < 1 or position > len(left):
                return None, _error("ORDINAL_OUT_OF_RANGE", "集合中不存在该序号。")
            member_handles = [left[position - 1]]
        elif operation == "take":
            limit = int(target.get("limit") or 0)
            if limit < 1:
                return None, _error("INVALID_SET_LIMIT", "集合截取数量必须大于零。")
            member_handles = left[:limit]
        elif operation == "filter":
            status, status_error = _structured_status_filter(state, target)
            if status_error:
                return None, status_error
            if not status:
                return None, _error("FILTER_ARGUMENT_MISSING", "集合筛选需要结构化状态值和当前原文证据。")
            filtered: list[str] = []
            for handle in left:
                row, row_error, fresh_entries, _ = _fresh_order_from_handle(state, handle, source="contextual_set_filter")
                if row_error:
                    return None, row_error
                additions.extend(fresh_entries)
                if row and str(row.get("status") or "") == status:
                    filtered.append(handle)
            member_handles = filtered
        elif operation == "sort":
            field = str(target.get("sort_field") or "")
            direction = str(target.get("sort_direction") or "asc")
            sort_evidence = _require_span(
                state,
                str(target.get("sort_span") or ""),
                field="排序条件",
            )
            if sort_evidence:
                return None, sort_evidence
            if field not in {"created_at", "amount", "order_id"}:
                return None, _error("INVALID_SORT_FIELD", "当前不支持该排序字段。")
            records: list[tuple[str, Any]] = []
            for handle in left:
                row, row_error, fresh_entries, _ = _fresh_order_from_handle(state, handle, source="contextual_set_sort")
                if row_error:
                    return None, row_error
                additions.extend(fresh_entries)
                records.append((handle, row.get(field) if row else None))
            member_handles = [handle for handle, _ in sorted(records, key=lambda row: (row[1] is None, row[1]), reverse=direction == "desc")]
        else:
            return None, _error("INVALID_SET_OPERATION", "当前不支持该集合操作。")
    else:
        return None, _error("INVALID_CONTEXT_TARGET", "当前不支持该上下文目标类型。")

    members: list[dict[str, Any]] = []
    for handle in _dedup(member_handles):
        artifact = find_handle(state.get("artifact_ledger") or [], handle, scope=scope, allowed_kinds={"artifact"}, allowed_resource_types=allowed_resource_types)
        # Newly created artifacts are in additions but not yet on state ledger.
        if not artifact:
            artifact = next((item for item in additions if str(item.get("handle") or "") == handle), None)
        if artifact:
            members.append(artifact)
    member_handles = [str(item["handle"]) for item in members]
    if expected_shape == "one" and len(member_handles) != 1:
        if not member_handles:
            return None, _error("EMPTY_CONTEXT_TARGET", "当前上下文目标没有匹配对象。")
        return None, _error("CONTEXT_TARGET_NOT_UNIQUE", "当前引用对应多个对象，请明确范围或对象。", candidates=[{"handle": item["handle"], "label": item.get("label")} for item in members])
    if expected_shape not in {"one", "collection"}:
        return None, _error("INVALID_EXPECTED_SHAPE", "目标范围必须声明为单个对象或集合。")
    return {"member_handles": member_handles, "members": members, "entries": additions, "mode": mode, "target": deepcopy(target)}, None


def _fresh_order_rows_for_target(state: dict[str, Any], target_info: dict[str, Any], *, source: str) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    rows: list[dict[str, Any]] = []
    additions = list(target_info.get("entries") or [])
    # A same-turn all-orders/entity-match plan creates fresh artifacts before
    # the graph appends them to state.  Use a transient merged ledger only for
    # this tool execution; persistent state is still updated exclusively from
    # the returned ledger_entries.
    working_state = {**state, "artifact_ledger": append_entries(state.get("artifact_ledger") or [], additions)}
    handles: list[str] = []
    for handle in target_info.get("member_handles") or []:
        row, error, fresh_entries, stable_handle = _fresh_order_from_handle(working_state, str(handle), source=source)
        if error:
            return None, additions, error, handles
        assert row is not None and stable_handle is not None
        rows.append(row)
        additions.extend(fresh_entries)
        handles.append(stable_handle)
    return rows, additions, None, handles


def _result_payload(capability: str, *, target_info: dict[str, Any], handles: list[str], labels: list[str], scope: dict[str, str], state: dict[str, Any], additions: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = result_entry(capability=capability, member_handles=handles, labels=labels, scope=scope, turn=_turn(state), source_target={"mode": target_info.get("mode"), "target": target_info.get("target")})
    return result, [*additions, result]



# These helpers are deliberately module-private to the ecommerce overlay.  The
# sibling slices import the explicit module-private surface so the old monolith
# is not recreated; ``__all__`` preserves private helper availability across
# those sibling slices after the boundary split.
__all__ = [name for name in globals() if not name.startswith("__")]
