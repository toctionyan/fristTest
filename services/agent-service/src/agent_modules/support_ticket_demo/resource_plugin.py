from __future__ import annotations

from agent_core.resources.base import DeclarativeResourcePlugin


class SupportTicketResourcePlugin(DeclarativeResourcePlugin):
    def __init__(self) -> None:
        super().__init__(resource_type="support_ticket", id_fields=("resource_id", "ticket_id"))

    def fetch_current(self, port, actor, *, resource_id: str):
        return port.read_resource(actor, resource_type=self.resource_type, resource_id=resource_id, query={"user_id": actor.user_id})

    def label_for(self, facts: dict, *, resource_id: str) -> str:
        return str(facts.get("subject") or f"支持工单 {resource_id}")
