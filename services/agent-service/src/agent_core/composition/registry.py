from __future__ import annotations

"""Explicit module-first Composition Root.

This is the only ``agent_core`` location allowed to import concrete domain
modules.  The Kernel itself receives only registered contributions.
"""

import os
from functools import lru_cache
from typing import Callable

from agent_core.business import configure_business_port
from agent_core.modules import (
    AgentModule,
    ModuleRegistry,
    configure_registry_providers,
)
from agent_core.presentation.registry import PresentationRegistry, configure_default_presentation_registry
from agent_core.rag.seed_catalog import (
    clear_builtin_knowledge_documents,
    configure_builtin_knowledge_documents,
)
from agent_modules.ecommerce import EcommerceModule
from agent_modules.support_ticket_demo import SupportTicketDemoModule


ModuleFactory = Callable[[], AgentModule]

_MODULE_FACTORIES: dict[str, ModuleFactory] = {
    "ecommerce": EcommerceModule,
    "support_ticket_demo": SupportTicketDemoModule,
}


def enabled_module_ids() -> tuple[str, ...]:
    """Return the explicitly enabled modules without implicit defaults beyond config."""
    raw = os.getenv("AGENT_ENABLED_MODULES", "ecommerce")
    return tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))


@lru_cache(maxsize=1)
def get_module_registry() -> ModuleRegistry:
    module_ids = enabled_module_ids()
    unknown = sorted(set(module_ids) - set(_MODULE_FACTORIES))
    if unknown:
        raise RuntimeError(f"Unknown AGENT_ENABLED_MODULES entries: {unknown}")

    registry = ModuleRegistry(tuple(_MODULE_FACTORIES[module_id]() for module_id in module_ids))
    configure_business_port(registry.build_business_port)
    configure_default_presentation_registry(lambda: PresentationRegistry(registry.presentation_adapters()))
    configure_builtin_knowledge_documents(registry.builtin_knowledge_documents())
    return registry


@lru_cache(maxsize=1)
def get_runtime_registry():
    """Build the formal Runtime Registry from explicitly installed modules."""
    return get_module_registry().build_runtime_registry()


configure_registry_providers(
    runtime_registry=get_runtime_registry,
    module_registry=get_module_registry,
)


def reset_runtime_registry_cache() -> None:
    """Reset Composition Root caches for tests and controlled configuration reloads."""
    get_runtime_registry.cache_clear()
    get_module_registry.cache_clear()
    clear_builtin_knowledge_documents()
