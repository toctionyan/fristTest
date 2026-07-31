"""Stable Business Service entrypoint.

`main.py` intentionally stays a small composition facade.  Domain logic, HTTP
routes, request models and demo seed data live in separate modules, so adding a
new business area cannot recreate a monolithic entrypoint.
"""
from __future__ import annotations

from .api import create_app
from .api_models import (
    AddressChangeRequest, AfterSalesCreateRequest, CancelOrderRequest,
    CommandRequest, ComplaintCreateRequest, HumanHandoffCreateRequest,
    InvoiceCreateRequest, LegacyReviewRequest, LogisticsQueryRequest,
    OperationCommandRequest, OperationPreviewRequest, OrderQueryRequest,
    RefundCreateRequest,
)
from .application.service import BusinessService
from .seed import seed_demo_data

__all__ = [
    "create_app", "BusinessService", "seed_demo_data",
    "OrderQueryRequest", "LogisticsQueryRequest", "CancelOrderRequest",
    "AddressChangeRequest", "AfterSalesCreateRequest", "RefundCreateRequest",
    "InvoiceCreateRequest", "ComplaintCreateRequest", "HumanHandoffCreateRequest",
    "CommandRequest", "OperationPreviewRequest", "OperationCommandRequest",
    "LegacyReviewRequest",
]

app = create_app()
