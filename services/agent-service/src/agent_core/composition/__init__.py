"""Application composition boundary.

This is the only agent-core package permitted to import concrete AgentModule
implementations. It assembles installed module contributions into the runtime
registry; lifecycle, transaction and presentation code consume that registry.
"""
from .registry import get_module_registry, get_runtime_registry, reset_runtime_registry_cache

__all__ = ["get_module_registry", "get_runtime_registry", "reset_runtime_registry_cache"]
