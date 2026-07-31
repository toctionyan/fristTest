"""E-commerce observation adapter using only canonical formal contracts.

No unstructured ``label/title/id`` display block can leave this adapter. Every
primary structured result is projected exactly once through an e-commerce
contract and then released by the core StructuredResultReleaseGate.
"""

from __future__ import annotations

from typing import Any

from agent_core.presentation.actions import customer_actions_for_business_codes
from agent_modules.ecommerce.presentation.contracts import (
    presentation_contract_manifests,
    presentation_renderer_registrations,
    project_business_status_list,
    project_advisory,
    project_eligibility_decision,
    project_logistics_overview,
    project_next_actions,
    project_order_list,
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _logistics_observation(item: dict[str, Any]) -> dict[str, Any]:
    """Keep verified query fields; do not reinterpret aliases for display."""
    order = item.get("order") if isinstance(item.get("order"), dict) else {}
    logistics = item.get("logistics") if isinstance(item.get("logistics"), dict) else {}
    status = _text(logistics.get("status")) or _text(order.get("status"))
    return {
        "order_id": _text(order.get("order_id")),
        "product_name": _text(order.get("product_name")),
        "status": status,
        "latest": _text(logistics.get("latest")),
        "estimate": logistics.get("estimate"),
    }


def _next_actions_block(
    *,
    order: dict[str, Any],
    codes: list[str],
    title: str,
    summary: str,
    input_hints: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    order_id = _text(order.get("order_id"))
    product_name = _text(order.get("product_name"))
    if not order_id or not product_name:
        return None
    actions = customer_actions_for_business_codes(codes, resource_type="order", resource_id=order_id)
    if not actions:
        return None
    hints = dict(input_hints or {})
    for action in actions:
        # Core public metadata is resource-generic.  This ecommerce projection
        # owns the order-specific UI contract and makes the canonical
        # resource id explicit as ``order_id`` only at this outer boundary.
        action["target"] = {"resource_type": "order", "order_id": order_id}
        if str(action.get("action_id") or "") in {"create_refund", "create_after_sales_request"} and hints:
            action["input_hints"] = hints
    return project_next_actions(
        order_id=order_id,
        product_name=product_name,
        actions=actions,
        title=title,
        summary=summary,
        trace_id=trace_id,
    )


class EcommerceObservationAdapter:
    """Select one primary e-commerce contract block per formal turn."""

    adapter_id = "ecommerce.observations.v4"
    priority = 100

    # This map is both runtime documentation and an architecture-test surface:
    # a capability may not merely name a registered contract; the adapter must
    # declare the concrete trace-to-contract route that can actually emit it.
    TRACE_PRESENTATION_ROUTES = {
        "list_orders": "commerce.order_list@1",
        "get_order_details": "commerce.order_list@1",
        "get_order_logistics": "commerce.logistics_overview@1",
        "list_refunds": "commerce.business_status_list@1",
        "list_after_sales_requests": "commerce.business_status_list@1",
        "list_invoices": "commerce.business_status_list@1",
        "consult_invoice_policy": "commerce.advisory@1",
        "consult_refund_policy": "commerce.advisory@1",
        "consult_after_sales_policy": "commerce.advisory@1",
        "consult_warranty_policy": "commerce.advisory@1",
        "evaluate_refund_eligibility": "commerce.eligibility_decision@1",
    }

    @staticmethod
    def presentation_contracts() -> tuple[dict[str, Any], ...]:
        return presentation_contract_manifests()

    @staticmethod
    def presentation_renderers():
        return presentation_renderer_registrations()

    def blocks_from_trace(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project one canonical primary block per verified Goal scope.

        A single Goal may require several prerequisite tools, so candidates
        within the same Goal scope still follow the established precedence
        (action/advisory > logistics > order > related status).  Independent
        Goals must not overwrite one another merely because they share a turn.

        Legacy traces without Goal provenance intentionally share one turn
        scope, preserving the former single-primary behaviour for old
        checkpoints and focused one-Goal queries.
        """

        def scope_for(row: dict[str, Any]) -> tuple[str, ...]:
            values = tuple(dict.fromkeys(
                str(value)
                for value in list(row.get("goal_ids") or [])
                if str(value)
            ))
            return tuple(sorted(values)) if values else ("__turn__",)

        scoped: dict[tuple[str, ...], dict[str, Any]] = {}
        scope_order: list[tuple[str, ...]] = []

        for trace_index, row in enumerate(trace):
            if not isinstance(row, dict):
                continue
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            if not result.get("ok"):
                continue
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            name = str(row.get("name") or "")
            trace_id = str(row.get("trace_id") or row.get("call_id") or "") or None
            scope = scope_for(row)
            if scope not in scoped:
                scoped[scope] = {
                    "logistics_items": [],
                    "logistics_parameterization": None,
                    "latest_order_observations": None,
                    "latest_order_trace_id": None,
                    "status_rows": [],
                    "status_trace_id": None,
                    "status_observed": False,
                    "status_title": "业务进度",
                    "status_query_target": {},
                    "next_action_blocks": [],
                    "trace_index": trace_index,
                }
                scope_order.append(scope)
            current = scoped[scope]

            if name == "get_order_logistics":
                current["logistics_items"] = [
                    _logistics_observation(item)
                    for item in data.get("items") or ()
                    if isinstance(item, dict)
                ]
                current["logistics_parameterization"] = dict(data.get("parameterization") or {})
                current["latest_order_trace_id"] = trace_id
            elif name == "list_orders":
                current["latest_order_observations"] = [
                    dict(item) for item in data.get("orders") or () if isinstance(item, dict)
                ]
                current["latest_order_trace_id"] = trace_id
            elif name == "get_order_details":
                order = data.get("order") if isinstance(data.get("order"), dict) else {}
                current["latest_order_observations"] = [dict(order)] if order else []
                current["latest_order_trace_id"] = trace_id
            elif name in {"list_refunds", "list_after_sales_requests", "list_invoices"}:
                current["status_rows"] = [
                    dict(item) for item in data.get("items") or () if isinstance(item, dict)
                ]
                current["status_trace_id"] = trace_id
                current["status_observed"] = True
                query_target = dict(data.get("query_target") or {})
                if not query_target and current["status_rows"]:
                    linked_order_id = _text(current["status_rows"][0].get("order_id"))
                    if linked_order_id:
                        query_target = {
                            "order_id": linked_order_id,
                            "label": f"订单 {linked_order_id}",
                        }
                current["status_query_target"] = query_target
                current["status_title"] = {
                    "list_refunds": "退款记录",
                    "list_after_sales_requests": "售后工单",
                    "list_invoices": "发票记录",
                }[name]
            elif name in {
                "consult_invoice_policy",
                "consult_refund_policy",
                "consult_after_sales_policy",
                "consult_warranty_policy",
            }:
                order = data.get("order") if isinstance(data.get("order"), dict) else {}
                current["next_action_blocks"].append(project_advisory(
                    order_id=_text(order.get("order_id")),
                    product_name=_text(order.get("product_name")),
                    question=_text(data.get("question")) or _text(data.get("issue")) or "订单政策咨询",
                    policy_evidence=[
                        dict(item) for item in data.get("policy_evidence") or () if isinstance(item, dict)
                    ],
                    knowledge_available=bool(data.get("knowledge_available")),
                    trace_id=trace_id,
                ))
            elif name == "evaluate_refund_eligibility":
                preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
                snapshot = preview.get("snapshot") if isinstance(preview.get("snapshot"), dict) else {}
                order_id = _text(snapshot.get("order_id"))
                product_name = _text(snapshot.get("product_name")) or _text(data.get("target_label"))
                actions = customer_actions_for_business_codes(
                    ["APPLY_REFUND"] if data.get("eligible") else [],
                    resource_type="order",
                    resource_id=order_id,
                )
                for action in actions:
                    action["target"] = {"resource_type": "order", "order_id": order_id}
                current["next_action_blocks"].append(project_eligibility_decision(
                    order_id=order_id,
                    product_name=product_name,
                    eligible=bool(data.get("eligible")),
                    decision=_text(preview.get("decision")) or ("ALLOWED" if data.get("eligible") else "BLOCKED"),
                    summary=_text(preview.get("message")),
                    actions=actions,
                    trace_id=trace_id,
                ))

        blocks: list[dict[str, Any]] = []
        for scope in scope_order:
            current = scoped[scope]
            block: dict[str, Any] | None = None
            if current["next_action_blocks"]:
                block = dict(current["next_action_blocks"][-1])
            elif current["logistics_items"]:
                block = project_logistics_overview(
                    current["logistics_items"],
                    parameterization=current["logistics_parameterization"],
                    trace_id=current["latest_order_trace_id"],
                )
            elif current["latest_order_observations"] is not None:
                block = project_order_list(
                    current["latest_order_observations"],
                    trace_id=current["latest_order_trace_id"],
                )
            elif current["status_observed"]:
                block = project_business_status_list(
                    current["status_rows"],
                    title=current["status_title"],
                    query_target=current["status_query_target"],
                    trace_id=current["status_trace_id"],
                )
            if block is not None:
                block["_goal_ids"] = [] if scope == ("__turn__",) else list(scope)
                block["_presentation_order"] = int(current["trace_index"])
                blocks.append(block)
        return blocks
