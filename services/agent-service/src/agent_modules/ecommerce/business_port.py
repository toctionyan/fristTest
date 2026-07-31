"""Ecommerce implementation of the generic Core BusinessPort."""
from __future__ import annotations

from functools import lru_cache
import os
from typing import Any

from agent_core.business.contracts import ActorContext, BusinessPort
from agent_core.business.transport import business_actor_context
from agent_modules.ecommerce.business_client import BusinessClient, get_business_client


class EcommerceHttpBusinessPort:
    """Translate generic resource/command requests to ecommerce HTTP routes.

    The translation is Overlay-only. Runtime callers provide a declared
    ``resource_type`` and formal query/command envelope; this class never
    performs a nearest resource/action match.
    """

    def __init__(self, client: BusinessClient | None = None):
        self._client = client

    @property
    def client(self) -> BusinessClient:
        return self._client or get_business_client()

    def _call(self, actor: ActorContext, fn):
        with business_actor_context(
            user_id=actor.user_id,
            role=actor.role,
            tenant_id=actor.tenant_id,
            account_id=actor.subject or actor.user_id,
            permissions=actor.permissions,
            user_token=actor.user_token,
        ):
            return fn(self.client)

    def health(self) -> dict[str, Any]:
        return self.client.health()

    def read_resource(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        resource_id: str,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resource = str(resource_type or "").strip()
        target_id = str(resource_id or "").strip()
        if not resource or not target_id:
            raise ValueError("resource_type and resource_id are required")
        params = dict(query or {})

        def invoke(client: BusinessClient):
            if resource == "order":
                return client.get_order(target_id, user_id=str(params.get("user_id") or "") or None)
            if resource == "logistics":
                return client.get_logistics(target_id)
            if resource == "product":
                return client.get_product(target_id)
            raise ValueError(f"unsupported ecommerce resource read: {resource}")

        return self._call(actor, invoke)

    def query_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        query_spec: dict[str, Any],
    ) -> dict[str, Any]:
        resource = str(resource_type or "").strip()
        spec = dict(query_spec or {})

        def invoke(client: BusinessClient):
            if resource == "order":
                if set(spec).issubset({"user_id"}):
                    return client.list_orders(user_id=str(spec.get("user_id") or "") or None)
                return client.query_orders(spec)
            if resource == "logistics":
                return client.query_logistics(spec)
            if resource == "refund":
                return client.list_refunds(
                    refund_id=str(spec.get("resource_id") or spec.get("refund_id") or "") or None,
                    order_id=str(spec.get("order_id") or "") or None,
                    user_id=str(spec.get("user_id") or "") or None,
                    status=str(spec.get("status") or "") or None,
                )
            if resource == "after_sales":
                return client.list_after_sales(
                    ticket_id=str(spec.get("resource_id") or spec.get("ticket_id") or "") or None,
                    order_id=str(spec.get("order_id") or "") or None,
                    user_id=str(spec.get("user_id") or "") or None,
                    status=str(spec.get("status") or "") or None,
                )
            if resource == "invoice":
                return client.list_invoices(
                    invoice_id=str(spec.get("resource_id") or spec.get("invoice_id") or "") or None,
                    order_id=str(spec.get("order_id") or "") or None,
                    user_id=str(spec.get("user_id") or "") or None,
                    status=str(spec.get("status") or "") or None,
                )
            if resource == "product":
                return client.list_products(keyword=str(spec.get("keyword") or "") or None)
            if resource == "coupon":
                return client.list_coupons(user_id=str(spec.get("user_id") or "") or None)
            raise ValueError(f"unsupported ecommerce resource query: {resource}")

        return self._call(actor, invoke)

    def query_related_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        relation: dict[str, Any],
        query_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Query records constrained by a verified resource relation.

        Core passes only a resource-shaped relation; ecommerce maps its order
        relationship to the appropriate domain API at this overlay boundary.
        """
        spec = {**dict(query_spec or {}), **dict(relation or {})}
        return self.query_resources(actor, resource_type=resource_type, query_spec=spec)

    def preview_operation(
        self,
        actor: ActorContext,
        *,
        resource_type: str | None,
        resource_id: str | None,
        operation: str,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            actor,
            lambda client: client.preview_operation(
                resource_type=resource_type,
                resource_id=resource_id,
                operation=operation,
                input_values=input_values,
            ),
        )

    def execute_command(
        self,
        actor: ActorContext,
        *,
        command: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._call(
            actor,
            lambda client: client.execute_operation_command(
                command=dict(command or {}),
                idempotency_key=idempotency_key,
            ),
        )


@lru_cache(maxsize=1)
def get_ecommerce_business_port() -> BusinessPort:
    adapter = os.getenv("AGENT_BUSINESS_ADAPTER", "ecommerce_http").strip().lower()
    if adapter in {"ecommerce_http", "demo_http", "http", "business_service"}:
        return EcommerceHttpBusinessPort()
    raise RuntimeError(f"Unsupported AGENT_BUSINESS_ADAPTER={adapter!r}")


def reset_ecommerce_business_port_cache() -> None:
    get_ecommerce_business_port.cache_clear()
