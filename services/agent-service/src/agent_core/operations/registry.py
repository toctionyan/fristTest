from __future__ import annotations

from typing import Any, Iterable

from agent_core.operations.base import OperationPlugin


class OperationPluginRegistry:
    """One source of truth for customer-visible write operations."""

    def __init__(self, plugins: Iterable[OperationPlugin]) -> None:
        rows = list(plugins)
        self._plugins = {plugin.action_id: plugin for plugin in rows}
        self._by_business_code = {plugin.business_code.upper(): plugin for plugin in rows}
        if len(rows) != len(self._plugins):
            raise ValueError("duplicate action plugin registration")
        if len(rows) != len(self._by_business_code):
            raise ValueError("duplicate business action code registration")
        for plugin in rows:
            capability = getattr(plugin, "operation_capability", None)
            if capability is None:
                raise ValueError(f"operation plugin {plugin.action_id} is missing OperationCapability")
            if plugin.target_resource_type not in capability.target_resource_types:
                raise ValueError(f"operation plugin {plugin.action_id} capability target mismatch")

    def all(self) -> list[OperationPlugin]:
        return list(self._plugins.values())

    def get(self, action_id: str) -> OperationPlugin | None:
        return self._plugins.get(str(action_id or "").strip())

    def require(self, action_id: str) -> OperationPlugin:
        plugin = self.get(action_id)
        if plugin is None:
            raise KeyError(action_id)
        return plugin

    def action_ids(self) -> set[str]:
        return set(self._plugins)

    def business_codes(self) -> set[str]:
        return set(self._by_business_code)

    def all_for_resource(self, resource_type: str) -> list[OperationPlugin]:
        return [plugin for plugin in self.all() if plugin.target_resource_type == str(resource_type)]

    def public_actions_for_business_codes(self, codes: Iterable[Any], *, resource_type: str, resource_id: str) -> list[dict[str, Any]]:
        available = {str(code or "").strip().upper() for code in codes}
        target = {"resource_type": str(resource_type), "resource_id": str(resource_id)}
        return [plugin.public_metadata(target=target) for code, plugin in self._by_business_code.items() if code in available]
