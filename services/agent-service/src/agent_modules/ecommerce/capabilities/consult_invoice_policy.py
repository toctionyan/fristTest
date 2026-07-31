"""Atomic, read-only invoice-policy consultation capability."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .schema_common import TARGET_SCHEMA, function_schema
from .execution_adapter import execute_one


def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "consult_invoice_policy", lambda engine: engine.execute_consult_invoice_policy(state, dict(args or {})))


DEFINITION = EcommerceCapabilityDefinition(
    key="ecommerce.invoice.policy_consultation",
    tool_name="consult_invoice_policy",
    category="consultation",
    planner_rule="只解释发票/开票规则与条件；通用政策使用 target.mode=all_orders，具体商品政策使用当前原文 entity_match；不得返回退款、售后、保修或物流政策，不创建开票申请。",
    execution_kind="knowledge_read",
    goal_completion_types=("consult", "query"),
    completion_effects=('invoice.consult_policy:order',),
    discovery_examples=("能开发票吗", "可以开发票吗", "能不能开票", "开票规则", "发票政策", "开发票有什么条件"),
    exclusion_examples=("发票进度", "开票进度", "发票记录", "帮我开票", "申请发票", "我要开发票", "退款", "售后", "保修", "物流"),
    schema=function_schema(
        "consult_invoice_policy",
        "只查询发票/开票政策；通用政策用 target.mode=all_orders 且不加商品筛选，具体商品政策用 entity_match；不创建申请，也不得扩展到退款、售后、保修或物流。issue_span 可为空字符串。",
        {"target": TARGET_SCHEMA, "reference_span": {"type": "string"}, "issue_span": {"type": "string"}, "question_span": {"type": "string"}},
        ["target", "reference_span", "issue_span", "question_span"],
    ),
    executor=execute,
    presentation_contract="commerce.advisory@1",
    public_label="订单发票政策咨询",
)
