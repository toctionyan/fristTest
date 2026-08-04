"""Atomic, read-only refund-policy consultation capability."""
from __future__ import annotations
from typing import Any

from .spec import EcommerceCapabilityDefinition
from .planning_contracts import policy_consult_contract
from .schema_common import TARGET_SCHEMA, function_schema
from .execution_adapter import execute_one


def execute(state: dict[str, Any], args: dict[str, Any], *, execution_permit=None, effect_id: str = "", transactions=None) -> dict[str, Any]:
    return execute_one(state, "consult_refund_policy", lambda engine: engine.execute_consult_refund_policy(state, dict(args or {})))


DEFINITION = EcommerceCapabilityDefinition(
    key="ecommerce.refund.policy_consultation",
    tool_name="consult_refund_policy",
    category="consultation",
    planner_rule="只解释一般退款/退货政策；通用政策使用 target.mode=all_orders，具体商品政策使用当前原文 entity_match；具体订单当前能否退款必须使用退款资格核验，不创建退款申请。",
    execution_kind="knowledge_read",
    goal_completion_types=("consult", "query"),
    completion_effects=('refund.consult_policy:order',),
    discovery_examples=("退款政策", "退款规则", "退货规则", "怎么退", "如何退"),
    exclusion_examples=("可以退货退款吗", "可以退款吗", "能退款吗", "能退吗", "退款资格", "帮我退款", "申请退款", "发票", "售后", "保修"),
    schema=function_schema(
        "consult_refund_policy",
        "只查询一般退款/退货政策；通用政策用 target.mode=all_orders 且不加商品筛选，具体商品政策用 entity_match；不得代替具体订单资格核验，也不创建申请。issue_span 可为空字符串。",
        {"target": TARGET_SCHEMA, "reference_span": {"type": "string"}, "issue_span": {"type": "string"}, "question_span": {"type": "string"}},
        ["target", "reference_span", "issue_span", "question_span"],
    ),
    executor=execute,
    contract_version='2',
    planning_contract=policy_consult_contract(policy_type="refund", output_type="VerifiedRefundPolicyAdvice"),
    presentation_contract="commerce.advisory@1",
    public_label="订单退款政策咨询",
)
