"""Generic router over module-owned BusinessPort implementations."""
from __future__ import annotations

from typing import Any

from agent_core.business.contracts import ActorContext, BusinessPort


class CompositeBusinessPort:
    def __init__(self, *, resource_ports: dict[str, BusinessPort], action_ports: dict[str, BusinessPort]) -> None:
        self._resource_ports = dict(resource_ports)
        self._action_ports = dict(action_ports)

    def health(self) -> dict[str, Any]:
        """Report each module independently; one unavailable backend must not hide installed modules."""
        modules: dict[str, Any] = {}
        seen: set[int] = set()
        for resource_type, port in self._resource_ports.items():
            if id(port) in seen:
                continue
            seen.add(id(port))
            try:
                modules[resource_type] = port.health()
            except Exception as exc:
                modules[resource_type] = {"success": False, "error": exc.__class__.__name__}
        return {"success": all(bool(row.get("success")) for row in modules.values()) if modules else True, "modules": modules}

    def _resource_port(self, resource_type: str) -> BusinessPort:
        port = self._resource_ports.get(str(resource_type or ""))
        if port is None:
            raise ValueError(f"No enabled module owns resource_type={resource_type!r}")
        return port

    def _command_port(self, command: dict[str, Any]) -> BusinessPort:
        action_id = str(command.get("action_id") or command.get("operation") or "")
        port = self._action_ports.get(action_id)
        if port is None:
            resource_type = str(command.get("resource_type") or command.get("target_resource_type") or "")
            port = self._resource_ports.get(resource_type)
        if port is None:
            raise ValueError("No enabled module owns this command")
        return port

    def read_resource(self, actor: ActorContext, *, resource_type: str, resource_id: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._resource_port(resource_type).read_resource(actor, resource_type=resource_type, resource_id=resource_id, query=query)

    def query_resources(self, actor: ActorContext, *, resource_type: str, query_spec: dict[str, Any]) -> dict[str, Any]:
        return self._resource_port(resource_type).query_resources(actor, resource_type=resource_type, query_spec=query_spec)

    def query_related_resources(self, actor: ActorContext, *, resource_type: str, relation: dict[str, Any], query_spec: dict[str, Any]) -> dict[str, Any]:
        return self._resource_port(resource_type).query_related_resources(actor, resource_type=resource_type, relation=relation, query_spec=query_spec)

    def preview_operation(self, actor: ActorContext, *, resource_type: str | None, resource_id: str | None, operation: str, input_values: dict[str, Any] | None = None) -> dict[str, Any]:
        port = self._action_ports.get(operation) or self._resource_port(str(resource_type or ""))
        return port.preview_operation(actor, resource_type=resource_type, resource_id=resource_id, operation=operation, input_values=input_values)

    def execute_command(self, actor: ActorContext, *, command: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        return self._command_port(command).execute_command(actor, command=command, idempotency_key=idempotency_key)
