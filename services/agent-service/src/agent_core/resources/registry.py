from __future__ import annotations

from typing import Iterable

from agent_core.resources.base import ResourcePlugin


class ResourcePluginRegistry:
    """Single registry for resource identity/normalization plugins."""

    def __init__(self, plugins: Iterable[ResourcePlugin], *, allow_empty: bool = False) -> None:
        rows = list(plugins)
        self._plugins = {str(plugin.resource_type): plugin for plugin in rows}
        if len(rows) != len(self._plugins):
            raise ValueError("duplicate resource plugin registration")
        if not self._plugins and not allow_empty:
            raise ValueError("at least one resource plugin is required unless an explicitly empty Kernel is being composed")

    def all(self) -> list[ResourcePlugin]:
        return list(self._plugins.values())

    def get(self, resource_type: str) -> ResourcePlugin | None:
        return self._plugins.get(str(resource_type or "").strip())

    def require(self, resource_type: str) -> ResourcePlugin:
        plugin = self.get(resource_type)
        if plugin is None:
            raise KeyError(f"unregistered resource type: {resource_type}")
        return plugin

    def resource_types(self) -> set[str]:
        return set(self._plugins)
