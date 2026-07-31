"""Self-contained sample business port proving a second module can own its data boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent_core.business.contracts import ActorContext


_TICKETS: tuple[dict[str, Any], ...] = (
    {
        "ticket_id": "T-1001",
        "user_id": "u001",
        "subject": "无法登录工作台",
        "status": "处理中",
        "summary": "已分配给一线支持团队。",
        "version": 1,
    },
    {
        "ticket_id": "T-1002",
        "user_id": "u001",
        "subject": "账单下载问题",
        "status": "待补充信息",
        "summary": "请在工单中补充账单月份。",
        "version": 1,
    },
)


class SupportTicketDemoBusinessPort:
    """Minimal module-owned port; it intentionally has no write operation."""

    def health(self) -> dict[str, Any]:
        return {"success": True, "module": "support_ticket_demo", "backend": "in_memory_demo"}

    @staticmethod
    def _owned(actor: ActorContext) -> list[dict[str, Any]]:
        return [deepcopy(row) for row in _TICKETS if str(row.get("user_id") or "") == str(actor.user_id or "")]

    def read_resource(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        resource_id: str,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if resource_type != "support_ticket":
            return {"success": False, "error": "unsupported_resource_type"}
        for row in self._owned(actor):
            if str(row.get("ticket_id")) == str(resource_id):
                return {"success": True, "data": row}
        return {"success": False, "error": "not_found"}

    def query_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        query_spec: dict[str, Any],
    ) -> dict[str, Any]:
        if resource_type != "support_ticket":
            return {"success": False, "error": "unsupported_resource_type"}
        rows = self._owned(actor)
        return {
            "success": True,
            "data": rows,
            "summary": {"source_population_count": len(rows), "matched_population_count": len(rows)},
        }

    def query_related_resources(
        self,
        actor: ActorContext,
        *,
        resource_type: str,
        relation: dict[str, Any],
        query_spec: dict[str, Any],
    ) -> dict[str, Any]:
        return {"success": False, "error": "unsupported_relation"}

    def preview_operation(
        self,
        actor: ActorContext,
        *,
        resource_type: str | None,
        resource_id: str | None,
        operation: str,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"success": False, "error": "module_has_no_write_operations"}

    def execute_command(
        self,
        actor: ActorContext,
        *,
        command: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return {"success": False, "error": "module_has_no_write_operations"}


_port = SupportTicketDemoBusinessPort()


def get_support_ticket_demo_business_port() -> SupportTicketDemoBusinessPort:
    return _port
