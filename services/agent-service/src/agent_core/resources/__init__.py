"""Resource identity contracts and registrations.

Ledger-facing hydration lives in ``agent_core.resources.runtime`` deliberately
and is not imported here: the composition root must load resource contracts
without creating a dependency on transactions or persistence.
"""

from agent_core.resources.base import DeclarativeResourcePlugin, ResourcePlugin
from agent_core.resources.registry import ResourcePluginRegistry

__all__ = [
    "DeclarativeResourcePlugin",
    "ResourcePlugin",
    "ResourcePluginRegistry",
]
