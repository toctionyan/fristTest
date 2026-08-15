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
    _output(
        "order.collection",
        "order",
        ("read",),
        "发现、筛选或列出订单本身及其可见成员；当用户按商品、状态、金额或其他属性寻找订单时，即使最终只匹配一笔，也仍属于订单集合语义。",
        "order.list:order",
    ),
    _output(
        "order.details",
        "order",
        ("read",),
        "读取一个在本次查询前已经通过订单号、唯一历史结果或其他已验证引用唯一绑定的订单业务详情；不用于按商品、状态或其他属性寻找订单本身。",
        "order.query_details:order",
    ),
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
    _output(
        "refund.status",
        "refund",
        ("read",),
        "读取已经存在的退款申请记录、退款历史以及这些退款申请的当前处理状态；退款申请记录或退款历史属于退款申请本身的业务记录，不等同于此前产生的退款资格核验结论记录。",
        "refund.query_status:refund",
    ),
    _output("after_sales.status", "after_sales_request", ("read",), "售后申请的当前处理状态。", "after_sales.query_status:after_sales_request"),
    _output("invoice.status", "invoice", ("read",), "发票申请或开具状态。", "invoice.query_status:invoice"),
    _output("invoice.policy", "order", ("consult",), "与订单开票有关的政策说明。", "invoice.consult_policy:order"),
    _output("refund.policy", "order", ("consult",), "与订单退款有关的政策说明。", "refund.consult_policy:order"),
    _output("after_sales.policy", "order", ("consult",), "与订单售后有关的政策说明。", "after_sales.consult_policy:order"),
    _output("warranty.policy", "order", ("consult",), "与订单或商品保修有关的政策说明。", "warranty.consult_policy:order"),
    _output(
        "refund.eligibility",
        "order",
        ("read",),
        "核验订单当前能否退款、是否具备退款办理资格并返回已验证结论；这是对当前订单资格进行判断的语义，不等同于检索此前已经存在的资格记录集合。",
        "refund.assess_eligibility:order",
    ),
    _output("order.cancellation", "order", ("cancel",), "取消指定订单所产生的外部业务效果。", "order.cancel:order"),
    _output("after_sales.request", "order", ("create",), "创建售后申请所产生的外部业务效果。", "after_sales.create:order"),
    _output("refund.request", "order", ("create",), "创建退款申请所产生的外部业务效果。", "refund.create:order"),
    _output("invoice.request", "order", ("create",), "创建开票申请所产生的外部业务效果。", "invoice.create:order"),
    _output("transaction.status", "transaction", ("read",), "办理事务的当前生命周期状态。", "transaction.query_status:transaction"),
    _output(
        "refund.eligibility.collection",
        "refund_eligibility",
        ("read",),
        "只检索此前已经产生且当前仍有效的退款资格核验结论记录集合；这里的记录是资格判断结论本身，不是退款申请记录或退款历史，也不表示现在对一笔或多笔订单重新判断能否退款。",
        "refund.list_eligibilities:refund_eligibility",
    ),
    _output("transaction.draft.collection", "transaction_draft", ("read",), "当前仍有效的办理草稿集合。", "transaction.list_drafts:transaction_draft"),
    _output("transaction.draft.dismissal", "transaction_draft", ("cancel", "dismiss"), "撤销或关闭一个仍有效的办理草稿。", "transaction.cancel_draft:transaction_draft"),
    _output("refund.eligibility.dismissal", "refund_eligibility", ("dismiss",), "关闭一个仍有效的退款资格结论。", "refund.dismiss_eligibility:refund_eligibility"),
    # Intentionally has zero installed capability coverage. Keeping this valid
    # semantic meaning in the same vocabulary proves that vocabulary presence
    # does not reveal or imply executability.
    _output("courier.contact.phone", "courier", ("read",), "配送人员的联系电话。"),
)

__all__ = ["SEMANTIC_OUTPUTS"]
