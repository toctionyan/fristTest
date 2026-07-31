from __future__ import annotations

"""Order-specific operation declarations.

This is the only Agent-side module that knows the current order action set.
Adding a non-order resource must create its own module instead of editing the
kernel, target resolver or business gateway.
"""

from typing import Any

from agent_core.operations.base import DeclarativeOperationPlugin


def _issue_type_input() -> dict[str, Any]:
    return {
        "name": "reason_code", "label": "问题类型", "control": "choice_or_text", "input_kind": "select",
        "required": True, "step": 1, "step_title": "选择问题类型",
        "options": [
            {"value": "QUALITY_ISSUE", "label": "质量问题"},
            {"value": "SPEC_MISMATCH", "label": "规格或描述不符"},
            {"value": "WRONG_ITEM", "label": "发错商品"},
            {"value": "OTHER", "label": "其他"},
        ],
    }


def _reason_input(label: str) -> dict[str, Any]:
    return {
        "name": "reason", "label": label, "control": "textarea", "input_kind": "textarea",
        "required": True, "step": 2, "step_title": "描述问题", "placeholder": "请描述具体情况。",
    }


def _after_sales_payload(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    service_type = str(result.get("service_type") or "").strip()
    reason = str(result.get("reason") or "").strip()
    if service_type and reason and not reason.startswith("["):
        result["reason"] = f"[{service_type}] {reason}"
    return result


def default_order_operations() -> tuple[DeclarativeOperationPlugin, ...]:
    return (
        DeclarativeOperationPlugin(
            action_id="cancel_order", business_code="CANCEL_ORDER", business_operation="CANCEL_ORDER",
            label="取消订单", risk_level="low_mutation", target_resource_type="order",
            input_schema=[{
                "name": "reason", "label": "取消原因", "control": "choice_or_text", "input_kind": "select",
                "required": True, "step": 1, "step_title": "选择取消原因",
                "options": [
                    {"value": "not_needed", "label": "不想要了"},
                    {"value": "duplicate", "label": "重复下单"},
                    {"value": "bad_reputation", "label": "口碑不好"},
                    {"value": "other", "label": "其他"},
                ],
            }],
            intent_template="我要取消订单 {resource_id}", required_payload_fields=("reason",), refresh_target_on_success=True,
        ),
        DeclarativeOperationPlugin(
            action_id="create_after_sales_request", business_code="APPLY_AFTER_SALES", business_operation="APPLY_AFTER_SALES",
            label="申请售后", risk_level="medium_mutation", target_resource_type="order",
            input_schema=[_issue_type_input(), _reason_input("问题描述")], intent_template="我要申请售后 {resource_id}",
            required_payload_fields=("reason", "reason_code"), result_resource_type="after_sales", result_id_field="ticket_id",
            payload_transformer=_after_sales_payload,
        ),
        DeclarativeOperationPlugin(
            action_id="create_refund", business_code="APPLY_REFUND", business_operation="APPLY_REFUND",
            label="申请退款", risk_level="financial_mutation", target_resource_type="order",
            input_schema=[_issue_type_input(), _reason_input("退款原因")], intent_template="我要申请退款 {resource_id}",
            required_payload_fields=("reason",), result_resource_type="refund", result_id_field="refund_id",
        ),
        DeclarativeOperationPlugin(
            action_id="create_invoice", business_code="APPLY_INVOICE", business_operation="APPLY_INVOICE",
            label="申请发票", risk_level="medium_mutation", target_resource_type="order",
            input_schema=[{
                "name": "invoice_title", "label": "发票抬头", "control": "text", "input_kind": "text",
                "required": True, "step": 1, "step_title": "填写开票信息",
            }],
            intent_template="我要申请发票 {resource_id}", required_payload_fields=("invoice_title",),
            result_resource_type="invoice", result_id_field="invoice_id",
        ),
    )
