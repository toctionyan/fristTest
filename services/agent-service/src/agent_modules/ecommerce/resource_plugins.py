from __future__ import annotations

"""Ecommerce resource identity plugins registered only by the Composition Root."""

from agent_core.resources.base import DeclarativeResourcePlugin


class OrderResourcePlugin(DeclarativeResourcePlugin):
    def __init__(self) -> None:
        super().__init__(resource_type="order", id_fields=("resource_id", "order_id"))

    def fetch_current(self, port, actor, *, resource_id: str):
        return port.read_resource(
            actor,
            resource_type=self.resource_type,
            resource_id=resource_id,
            query={"user_id": actor.user_id},
        )

    def label_for(self, facts: dict, *, resource_id: str) -> str:
        return f"{facts.get('product_name') or '订单'}（订单 {resource_id}）"
