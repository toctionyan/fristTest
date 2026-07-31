"""Facade composed from small Business Service application slices."""
from __future__ import annotations

from .applications import ApplicationMixin
from .core_service import ServiceCore
from .order_operations import OrderOperationMixin
from .order_queries import OrderQueryMixin
from .resource_commands import ResourceCommandMixin


class BusinessService(
    ServiceCore,
    OrderQueryMixin,
    OrderOperationMixin,
    ApplicationMixin,
    ResourceCommandMixin,
):
    """Single service facade with domain slices, preserving the public API."""

    pass
