"""Read projections for fixed ecommerce query capabilities."""
from __future__ import annotations

from typing import Any

from agent_core.business import BusinessServiceError
from agent_core.ledger import artifact_entry, scope_for_state, view_entry

from .context import (
    _actor_context_from_state,
    _business_service_error,
    _error,
    _fresh_order_rows_for_target,
    _logistics_query_filter,
    _ok,
    _read_user_id,
    _require_span,
    _result_payload,
    _target_members,
    _turn,
    _upsert_artifact,
    business_port,
)

def _execute_ecommerce_read(state: dict[str, Any], args: dict[str, Any], *, query_key: str) -> dict[str, Any]:
    """Shared implementation only; every public module handler selects one fixed query key.

    ``query_key`` is never model supplied and therefore cannot become a
    universal capability enum at the tool boundary.
    """
    reference_span = str(args.get("reference_span") or "")
    evidence = _require_span(state, reference_span, field="当前引用")
    if evidence:
        return evidence
    expected_shape = str(args.get("expected_shape") or "")
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    allowed_types = {"order"} if query_key.startswith("ecommerce.orders.") else {"order", "refund", "after_sales", "invoice"}
    target_info, target_error = _target_members(state, target, expected_shape=expected_shape, allowed_resource_types=allowed_types)
    if target_error:
        return target_error
    assert target_info is not None
    scope = scope_for_state(state)

    if query_key in {"ecommerce.orders.list", "ecommerce.order.details", "ecommerce.order.logistics"}:
        # The target was already validated against order-only resources above.
        rows, additions, row_error, stable_handles = _fresh_order_rows_for_target(state, target_info, source=f"module:{query_key}")
        if row_error:
            return row_error
        assert rows is not None
        labels = [f"{row.get('product_name') or '订单'}（订单 {row.get('order_id')}）" for row in rows]
        if query_key == "ecommerce.orders.list":
            view = view_entry(view_type="orders", member_handles=stable_handles, labels=labels, scope=scope, turn=_turn(state), source="module_ecommerce_read", query={"target": target, "reference_span": reference_span})
            result, all_entries = _result_payload(query_key, target_info=target_info, handles=stable_handles, labels=labels, scope=scope, state=state, additions=[*additions, view])
            # Keep verified business facts and internal ledger references distinct.
            # Customer-visible field naming happens exactly once later in the
            # e-commerce presentation contract projector.  Do not compress an
            # entity into ``label`` here: that would force later layers to
            # guess identity fields and can silently degrade a list into status
            # rows only.
            observations = [
                {
                    "reference_handle": handle,
                    "order_id": str(row.get("order_id") or ""),
                    "product_name": str(row.get("product_name") or ""),
                    "status": row.get("status"),
                    "amount": row.get("amount", row.get("price")),
                }
                for handle, row in zip(stable_handles, rows)
            ]
            return _ok({"capability_key": query_key, "view_handle": view["handle"], "result_handle": result["handle"], "count": len(rows), "orders": observations}, entries=all_entries)
        if query_key == "ecommerce.order.details":
            if expected_shape != "one":
                return _error("CAPABILITY_SHAPE_MISMATCH", "订单详情必须使用单对象范围。")
            result, all_entries = _result_payload(query_key, target_info=target_info, handles=stable_handles, labels=labels, scope=scope, state=state, additions=additions)
            return _ok({"capability_key": query_key, "result_handle": result["handle"], "order": rows[0]}, entries=all_entries)
        logistics_filter, filter_error = _logistics_query_filter(state, args)
        if filter_error:
            return filter_error
        bindings = [dict(item) for item in (args.get("constraint_bindings") or []) if isinstance(item, dict)]
        source_count = len(rows)
        if logistics_filter:
            # The Agent supplies verified source member ids, but the Business
            # Service owns the condition and applies it in SQL.  This is not an
            # Agent-side full query followed by a local filter.
            try:
                payload = business_port().query_resources(
                    _actor_context_from_state(state),
                    resource_type="logistics",
                    query_spec={
                        "scope": {"type": "selected_order_ids", "order_ids": [str(row.get("order_id") or "") for row in rows]},
                        "filters": dict(logistics_filter),
                        "answer_mode": "list",
                    },
                )
            except BusinessServiceError as exc:
                return _business_service_error("BUSINESS_QUERY_FAILED", exc)
            if not payload.get("success") or not isinstance(payload.get("data"), list):
                return _error("BUSINESS_QUERY_FAILED", str(payload.get("error") or "物流条件查询失败"))
            source_by_id = {str(row.get("order_id") or ""): (row, handle) for row, handle in zip(rows, stable_handles)}
            logistics_items: list[dict[str, Any]] = []
            matched_handles: list[str] = []
            matched_labels: list[str] = []
            for observed in payload.get("data") or []:
                if not isinstance(observed, dict):
                    continue
                order_id = str(observed.get("order_id") or "")
                source = source_by_id.get(order_id)
                if source is None:
                    return _error("BUSINESS_QUERY_SCOPE_MISMATCH", "业务服务返回了当前已验证范围外的物流记录。")
                row, handle = source
                logistics_items.append(
                    {
                        "order": row,
                        "logistics": {
                            "status": observed.get("delivery_status"),
                            "latest": observed.get("latest"),
                            "eta": observed.get("eta"),
                            "updated_at": observed.get("updated_at"),
                        },
                    }
                )
                matched_handles.append(handle)
                matched_labels.append(f"{row.get('product_name') or '订单'}（订单 {order_id}）")
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            matched_count = int(summary.get("matched_population_count") if str(summary.get("matched_population_count") or "").isdigit() else len(logistics_items))
            backend_conditions = dict(summary.get("applied_filters") or {})
            if backend_conditions != logistics_filter or matched_count != len(logistics_items):
                return _error("BUSINESS_QUERY_CONDITION_NOT_APPLIED", "业务服务未能证明已按当前物流条件执行查询。")
            result, all_entries = _result_payload(query_key, target_info=target_info, handles=matched_handles, labels=matched_labels, scope=scope, state=state, additions=additions)
            return _ok(
                {
                    "capability_key": query_key,
                    "result_handle": result["handle"],
                    "items": logistics_items,
                    "count": len(logistics_items),
                    "parameterization": {
                        "contract": "commerce.orders.logistics.query@1",
                        "constraint_bindings": bindings,
                        "required_backend_conditions": dict(logistics_filter),
                        "backend_applied_conditions": backend_conditions,
                        "source_population_count": int(summary.get("source_population_count") or source_count),
                        "matched_population_count": matched_count,
                        "presentation_population": "matched_members",
                    },
                },
                entries=all_entries,
            )

        # No condition was requested by this explicit call.  Every resolved
        # member is observed and the resulting population is the source
        # population.  A later alignment gate rejects a candidate that silently
        # dropped a decisive user condition before reaching this branch.
        logistics_items: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = business_port().read_resource(_actor_context_from_state(state), resource_type="logistics", resource_id=str(row.get("order_id")))
            except BusinessServiceError as exc:
                return _business_service_error("BUSINESS_QUERY_FAILED", exc)
            if not payload.get("success"):
                return _error("BUSINESS_QUERY_FAILED", str(payload.get("error") or "物流查询失败"))
            logistics_items.append({"order": row, "logistics": dict(payload.get("data") or {})})
        result, all_entries = _result_payload(query_key, target_info=target_info, handles=stable_handles, labels=labels, scope=scope, state=state, additions=additions)
        return _ok(
            {
                "capability_key": query_key,
                "result_handle": result["handle"],
                "items": logistics_items,
                "count": len(logistics_items),
                "parameterization": {
                    "contract": "commerce.orders.logistics.query@1",
                    "constraint_bindings": bindings,
                    "required_backend_conditions": {},
                    "backend_applied_conditions": {},
                    "source_population_count": source_count,
                    "matched_population_count": len(logistics_items),
                    "presentation_population": "observed_members",
                },
            },
            entries=all_entries,
        )

    resource_map = {"ecommerce.refunds.list": ("refund", "refund_id"), "ecommerce.after_sales.list": ("after_sales", "ticket_id"), "ecommerce.invoices.list": ("invoice", "invoice_id")}
    if query_key not in resource_map:
        return _error("UNKNOWN_CONTEXTUAL_CAPABILITY", "当前不支持该业务查询能力。")
    resource_type, id_key = resource_map[query_key]
    if expected_shape not in {"one", "collection"}:
        return _error("CAPABILITY_SHAPE_MISMATCH", "业务记录查询必须声明单对象或集合范围。")
    members = [
        dict(member)
        for member in list(target_info.get("members") or [])
        if isinstance(member, dict)
    ]
    if expected_shape == "one" and len(members) != 1:
        return _error("CAPABILITY_SHAPE_MISMATCH", "单对象业务记录查询必须解析到一个明确对象。")

    # Query every member in the runtime-verified target population.  The
    # model never supplies record rows and this loop never widens the scope:
    # each relation is derived from a permit-validated ledger member.
    rows: list[dict[str, Any]] = []
    query_targets: list[dict[str, Any]] = []
    for member in members:
        target_type = str(member.get("resource_type") or "")
        resource_id: str | None = None
        order_id: str | None = None
        if target_type == "order":
            order_id = str(member.get("resource_id") or "")
        elif target_type == resource_type:
            resource_id = str(member.get("resource_id") or "")
        else:
            return _error("HANDLE_TYPE_MISMATCH", "该对象不能用于当前业务进度查询。")
        try:
            payload = business_port().query_related_resources(
                _actor_context_from_state(state),
                resource_type=resource_type,
                relation={
                    "resource_id": resource_id,
                    "order_id": order_id,
                    "user_id": _read_user_id(state),
                },
                query_spec={"status": None},
            )
        except BusinessServiceError as exc:
            return _business_service_error("BUSINESS_QUERY_FAILED", exc)
        if not payload.get("success"):
            return _error("BUSINESS_QUERY_FAILED", str(payload.get("error") or "业务查询失败"))
        facts = member.get("facts") if isinstance(member.get("facts"), dict) else {}
        query_targets.append({
            "resource_type": target_type,
            "resource_id": str(member.get("resource_id") or ""),
            "order_id": str(order_id or facts.get("order_id") or ""),
            "product_name": str(facts.get("product_name") or ""),
            "label": str(member.get("label") or ""),
        })
        rows.extend(
            dict(row)
            for row in payload.get("data") or []
            if isinstance(row, dict)
        )

    # A population can contain overlapping order/record references.  Keep one
    # authoritative business record per domain identity while preserving
    # backend order for deterministic presentation.
    unique_rows: list[dict[str, Any]] = []
    seen_business_ids: set[str] = set()
    for row in rows:
        business_id = str(row.get(id_key) or "")
        if not business_id or business_id in seen_business_ids:
            continue
        seen_business_ids.add(business_id)
        unique_rows.append(row)
    rows = unique_rows
    additions = list(target_info.get("entries") or [])
    handles: list[str] = []
    labels: list[str] = []
    ledger = list(state.get("artifact_ledger") or [])
    for row in rows:
        business_id = str(row.get(id_key) or "")
        label = f"{resource_type}:{business_id}"
        art = artifact_entry(resource_type=resource_type, resource_id=business_id, label=label, facts=row, scope=scope, turn=_turn(state), source=f"module:{query_key}", freshness_version=int(row.get("version") or 1))
        ledger, saved = _upsert_artifact(ledger, art)
        additions.append(saved)
        handles.append(saved["handle"])
        labels.append(saved["label"])
    result, all_entries = _result_payload(query_key, target_info=target_info, handles=handles, labels=labels, scope=scope, state=state, additions=additions)
    # Keep verified business record identity and state explicit for the single
    # e-commerce presentation boundary.  Do not collapse these records into an
    # opaque ``label``; a browser must never guess identity semantics.
    observations = [
        {
            "record_reference": str(row.get(id_key) or ""),
            "record_kind": resource_type,
            "status": str(row.get("status") or ""),
            "updated_at": row.get("updated_at") or row.get("created_at"),
            "order_id": str(row.get("order_id") or ""),
        }
        for row in rows
    ]
    if len(query_targets) == 1:
        query_target = dict(query_targets[0])
    else:
        status = str((target_info.get("target") or {}).get("status") or "")
        scope_label = f"{status}订单范围" if status else "当前订单范围"
        query_target = {
            "resource_type": "collection",
            "resource_id": "",
            "order_id": "",
            "product_name": "",
            "label": f"{scope_label}（{len(query_targets)}个对象）",
        }
    return _ok({
        "capability_key": query_key,
        "result_handle": result["handle"],
        "items": observations,
        "count": len(observations),
        "query_target": query_target,
        "query_targets": query_targets,
    }, entries=all_entries)



def execute_list_orders(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _execute_ecommerce_read(state, args, query_key="ecommerce.orders.list")


def execute_get_order_details(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _execute_ecommerce_read(state, args, query_key="ecommerce.order.details")


def execute_get_order_logistics(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _execute_ecommerce_read(state, args, query_key="ecommerce.order.logistics")


def execute_list_refunds(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _execute_ecommerce_read(state, args, query_key="ecommerce.refunds.list")


def execute_list_after_sales_requests(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _execute_ecommerce_read(state, args, query_key="ecommerce.after_sales.list")


def execute_list_invoices(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return _execute_ecommerce_read(state, args, query_key="ecommerce.invoices.list")
