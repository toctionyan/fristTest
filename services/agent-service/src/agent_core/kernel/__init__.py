"""Domain-neutral Runtime Kernel public contracts.

Concrete plugin assembly lives in :mod:`agent_core.composition`. Architecture
validation is loaded lazily so importing a generic kernel contract cannot pull
concrete resources back into the dependency graph.
"""

from typing import Any

from agent_core.kernel.contracts import (
    ARCHITECTURE_INVARIANTS,
    BUSINESS_COMMAND_CONTRACT,
    RUNTIME_ARCHITECTURE_VERSION,
)
from agent_core.kernel.registry import RuntimeRegistry


def validate_runtime_architecture(registry: RuntimeRegistry) -> dict[str, Any]:
    from agent_core.kernel.integrity import validate_runtime_architecture as _validate

    return _validate(registry)


__all__ = [
    "ARCHITECTURE_INVARIANTS",
    "BUSINESS_COMMAND_CONTRACT",
    "RUNTIME_ARCHITECTURE_VERSION",
    "RuntimeRegistry",
    "validate_runtime_architecture",
]
