"""Atomic, read-only warranty-policy consultation capability."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import policy_consult_contract
from .schema_common import TARGET_SCHEMA, function_schema
from .execution_adapter import execute_one


def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "consult_warranty_policy", lambda engine: engine.execute_consult_warranty_policy(state, dict(args or {})))


DEFINITION = EcommerceCapabilityDefinition(
    key="ecommerce.warranty.policy_consultation",
    tool_name="consult_warranty_policy",
    category="consultation",
    planner_rule="只解释保修范围与保修规则；通用政策使用 target.mode=all_orders，具体商品政策使用当前原文 entity_match；不创建售后申请，不返回退款、发票或一般售后政策。",
    execution_kind="knowledge_read",
    goal_completion_types=("consult", "query"),
    completion_effects=('warranty.consult_policy:order',),
    discovery_examples=("保修政策", "保修规则", "有保修吗", "怎么保修", "怎么维修", "保修多久"),
    exclusion_examples=("申请售后", "售后进度", "退款", "发票", "物流"),
    schema=function_schema(
        "consult_warranty_policy",
        "只查询保修政策；通用政策用 target.mode=all_orders 且不加商品筛选，具体商品政策用 entity_match；不创建申请，也不得扩展到退款、发票或物流。issue_span 可为空字符串。",
        {"target": TARGET_SCHEMA, "reference_span": {"type": "string"}, "issue_span": {"type": "string"}, "question_span": {"type": "string"}},
        ["target", "reference_span", "issue_span", "question_span"],
    ),
    executor=execute,
    contract_version='2',
    planning_contract=policy_consult_contract(policy_type="warranty", output_type="VerifiedWarrantyPolicyAdvice"),
    presentation_contract="commerce.advisory@1",
    public_label="订单保修政策咨询",
)
