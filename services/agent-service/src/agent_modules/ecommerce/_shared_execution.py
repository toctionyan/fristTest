"""Private fixed-capability execution facade.

This file intentionally contains no routing, capability enum or business
branch.  It preserves a stable import target for module-local capability
adapters while responsibility-specific code lives under ``shared/``.
"""
from __future__ import annotations

from .shared.reads import (
    execute_get_order_details, execute_get_order_logistics, execute_list_after_sales_requests,
    execute_list_invoices, execute_list_orders, execute_list_refunds,
)
from .shared.consultation import (
    execute_consult_after_sales_policy,
    execute_consult_invoice_policy,
    execute_consult_refund_policy,
    execute_consult_warranty_policy,
)
from .shared.prepare_actions import (
    execute_prepare_after_sales_request, execute_prepare_cancel_order,
    execute_prepare_invoice, execute_prepare_refund,
)
from .shared.refund_eligibility import (
    execute_evaluate_refund_eligibility, _prepare_refund_from_eligibility,
)
from .shared.runtime_tools import (
    _ask_context_clarification, _dismiss_eligibility, _dismiss_offer,
    _list_active_eligibilities, _list_active_offers, _query_transaction_lifecycle,
    _report_unsupported_request,
)

__all__ = [
    "execute_list_orders", "execute_get_order_details", "execute_get_order_logistics",
    "execute_list_refunds", "execute_list_after_sales_requests", "execute_list_invoices",
    "execute_consult_invoice_policy", "execute_consult_refund_policy",
    "execute_consult_after_sales_policy", "execute_consult_warranty_policy",
    "execute_prepare_cancel_order",
    "execute_prepare_after_sales_request", "execute_prepare_refund",
    "execute_prepare_invoice", "execute_evaluate_refund_eligibility",
    "_prepare_refund_from_eligibility", "_query_transaction_lifecycle",
    "_list_active_eligibilities", "_list_active_offers", "_dismiss_offer",
    "_dismiss_eligibility", "_ask_context_clarification", "_report_unsupported_request",
]
