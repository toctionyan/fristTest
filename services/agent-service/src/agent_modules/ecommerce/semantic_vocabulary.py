"""Capability-independent ecommerce semantic output vocabulary.

The writer may see these domain meanings before semantic freeze. This file
contains no tool name, capability key, availability flag, planner rule,
discovery example or exclusion example. Legacy aliases are internal migration
metadata used only by the deterministic post-freeze compatibility compiler.
"""
from __future__ import annotations

from agent_core.modules.contracts import SemanticOutputDefinition


def _output(
    output_id: str,
    subject_type: str,
    effect_kinds: tuple[str, ...],
    description: str,
    *legacy_aliases: str,
) -> SemanticOutputDefinition:
    return SemanticOutputDefinition(
        output_id=output_id,
        subject_type=subject_type,
        effect_kinds=effect_kinds,
        description=description,
        legacy_effect_aliases=tuple(legacy_aliases),
    )


SEMANTIC_OUTPUTS = (
    _output("order.collection", "order", ("read",), "订单集合及其可见成员。", "order.list:order"),
    _output("order.details", "order", ("read",), "订单的已验证业务详情。", "order.query_details:order"),
    _output(
        "shipment.current_status",
        "shipment",
        ("read",),
        "物流生命周期的当前状态或阶段标签，例如待发货、运输中、已签收；不表示当前位置节点、轨迹或运输进展。",
        "order.query_logistics:order",
    ),
    _output("shipment.eta", "shipment", ("read",), "物流预计送达时间。", "order.query_logistics:order"),
    _output(
        "shipment.tracking",
        "shipment",
        ("read",),
        "物流当前位置节点、已发生轨迹或运输进展；用于回答货件当前到达何处或运输推进到哪里，不等同于仅有生命周期状态标签。",
        "order.query_logistics:order",
    ),
    _output("refund.status", "refund", ("read",), "退款申请的当前处理状态。", "refund.query_status:refund"),
    _output("after_sales.status", "after_sales_request", ("read",), "售后申请的当前处理状态。", "after_sales.query_status:after_sales_request"),
    _output("invoice.status", "invoice", ("read",), "发票申请或开具状态。", "invoice.query_status:invoice"),
    _output("invoice.policy", "order", ("consult",), "与订单开票有关的政策说明。", "invoice.consult_policy:order"),
    _output("refund.policy", "order", ("consult",), "与订单退款有关的政策说明。", "refund.consult_policy:order"),
    _output("after_sales.policy", "order", ("consult",), "与订单售后有关的政策说明。", "after_sales.consult_policy:order"),
    _output("warranty.policy", "order", ("consult",), "与订单或商品保修有关的政策说明。", "warranty.consult_policy:order"),
    _output("refund.eligibility", "order", ("read",), "订单当前是否具备退款办理资格及其已验证结论。", "refund.assess_eligibility:order"),
    _output("order.cancellation", "order", ("cancel",), "取消指定订单所产生的外部业务效果。", "order.cancel:order"),
    _output("after_sales.request", "order", ("create",), "创建售后申请所产生的外部业务效果。", "after_sales.create:order"),
    _output("refund.request", "order", ("create",), "创建退款申请所产生的外部业务效果。", "refund.create:order"),
    _output("invoice.request", "order", ("create",), "创建开票申请所产生的外部业务效果。", "invoice.create:order"),
    _output("transaction.status", "transaction", ("read",), "办理事务的当前生命周期状态。", "transaction.query_status:transaction"),
    _output("refund.eligibility.collection", "refund_eligibility", ("read",), "当前仍有效的退款资格结论集合。", "refund.list_eligibilities:refund_eligibility"),
    _output("transaction.draft.collection", "transaction_draft", ("read",), "当前仍有效的办理草稿集合。", "transaction.list_drafts:transaction_draft"),
    _output("transaction.draft.dismissal", "transaction_draft", ("cancel", "dismiss"), "撤销或关闭一个仍有效的办理草稿。", "transaction.cancel_draft:transaction_draft"),
    _output("refund.eligibility.dismissal", "refund_eligibility", ("dismiss",), "关闭一个仍有效的退款资格结论。", "refund.dismiss_eligibility:refund_eligibility"),
    # Intentionally has zero installed capability coverage. Keeping this valid
    # semantic meaning in the same vocabulary proves that vocabulary presence
    # does not reveal or imply executability.
    _output("courier.contact.phone", "courier", ("read",), "配送人员的联系电话。"),
)

__all__ = ["SEMANTIC_OUTPUTS"]
