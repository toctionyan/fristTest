"""Atomic, read-only after-sales policy consultation capability."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import policy_consult_contract
from .schema_common import TARGET_SCHEMA, function_schema
from .execution_adapter import execute_one


def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "consult_after_sales_policy", lambda engine: engine.execute_consult_after_sales_policy(state, dict(args or {})))


DEFINITION = EcommerceCapabilityDefinition(
    key="ecommerce.after_sales.policy_consultation",
    tool_name="consult_after_sales_policy",
    category="consultation",
    planner_rule="只解释售后政策与质量问题处理规则；通用政策使用 target.mode=all_orders，具体商品政策使用当前原文 entity_match；不创建售后申请，不返回退款、发票或保修政策。",
    execution_kind="knowledge_read",
    goal_completion_types=("consult", "query"),
    completion_effects=('after_sales.consult_policy:order',),
    discovery_examples=("售后政策", "售后规则", "坏了怎么处理", "坏了怎么办", "质量问题怎么处理"),
    exclusion_examples=("申请售后", "帮我申请售后", "售后进度", "退款", "发票", "保修"),
    schema=function_schema(
        "consult_after_sales_policy",
        "只查询售后政策；通用政策用 target.mode=all_orders 且不加商品筛选，具体商品政策用 entity_match；不创建申请，也不得扩展到退款、发票或保修。issue_span 可为空字符串。",
        {"target": TARGET_SCHEMA, "reference_span": {"type": "string"}, "issue_span": {"type": "string"}, "question_span": {"type": "string"}},
        ["target", "reference_span", "issue_span", "question_span"],
    ),
    executor=execute,
    contract_version='2',
    planning_contract=policy_consult_contract(policy_type="after_sales", output_type="VerifiedAfterSalesPolicyAdvice"),
    presentation_contract="commerce.advisory@1",
    public_label="订单售后政策咨询",
)
