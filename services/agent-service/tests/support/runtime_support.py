from __future__ import annotations

from agent_core.runtime.deps import LifecycleRuntimeDeps, lifecycle_runtime_deps
from agent_core.persistence.store_provider import get_store_provider
from agent_core.composition import get_runtime_registry
from agent_core.business import get_business_port


def runtime_deps() -> LifecycleRuntimeDeps:
    provider = get_store_provider()
    return lifecycle_runtime_deps(
        transactions=provider.transactions,
        capability_registry=get_runtime_registry().capabilities,
        business_port=get_business_port(),
        trace_logger=provider.traces,
    )
