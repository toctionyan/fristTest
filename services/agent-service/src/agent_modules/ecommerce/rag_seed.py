from __future__ import annotations

"""Builtin ecommerce knowledge supplied by the ecommerce module.

The Kernel receives these as plain installed-module documents; it never embeds
product, refund or logistics policy data in its own source tree.
"""

from typing import Any


def ecommerce_builtin_knowledge_documents() -> tuple[dict[str, Any], ...]:
    return (
        {
            "doc_id": "policy_after_sales_001",
            "title": "售后政策",
            "content": "普通商品签收 15 天内，如果存在质量问题，可以申请售后。签收超过 15 天后，一般进入厂家保修流程。人为损坏、进水、私自拆修通常不属于免费售后范围。",
            "source": "内置售后政策",
            "metadata": {"policy_domain": "after_sales"},
        },
        {
            "doc_id": "policy_refund_001",
            "title": "退款政策",
            "content": "未发货订单通常可以直接申请退款。已发货订单需要拒收或退回商品后，才能进入退款审核。已签收订单如果无质量问题，需符合七天无理由退货条件。",
            "source": "内置退款政策",
            "metadata": {"policy_domain": "refund"},
        },
        {
            "doc_id": "policy_warranty_001",
            "title": "保修政策",
            "content": "蓝牙耳机、机械键盘、无线鼠标等数码配件通常享受厂家保修。进水、摔坏、私自拆修属于人为损坏，不属于免费保修范围。",
            "source": "内置保修政策",
            "metadata": {"policy_domain": "warranty"},
        },
        {
            "doc_id": "policy_logistics_001",
            "title": "物流说明",
            "content": "订单发货后，物流信息通常在 24 小时内更新。运输中订单可查询预计送达时间。待发货订单说明商家仍在备货。",
            "source": "内置物流说明",
            "metadata": {"policy_domain": "logistics"},
        },
        {
            "doc_id": "policy_invoice_001",
            "title": "开票政策",
            "content": "已支付且未全额退款的订单可以申请电子发票。开票是独立业务，不会创建退款或售后申请；仅查询开票资格也不会创建或提交开票申请。正式申请前需要填写发票抬头。",
            "source": "内置开票政策",
            "metadata": {"policy_domain": "invoice"},
        },
    )
