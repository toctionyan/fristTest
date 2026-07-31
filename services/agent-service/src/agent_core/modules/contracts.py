"""Domain-neutral contracts for explicitly installed Agent modules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent_core.operations.assessment import OperationAssessmentDefinition
from agent_core.business.contracts import BusinessPort
from agent_core.kernel.capability_registry import CapabilityBinding
from agent_core.operations.base import OperationPlugin
from agent_core.resources.base import ResourcePlugin


class PresentationAdapter(Protocol):
    """Structural module contribution consumed by the presentation subsystem.

    The protocol lives with the module contribution contract so installed
    modules can contribute adapters without importing or constructing the
    presentation layer.
    """

    adapter_id: str
    priority: int

    def blocks_from_trace(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project verified observations into client-neutral public blocks."""


@dataclass(frozen=True)
class ModuleContribution:
    module_id: str
    version: str
    capabilities: tuple[CapabilityBinding, ...]
    resources: tuple[ResourcePlugin, ...] = ()
    operations: tuple[OperationPlugin, ...] = ()
    assessments: tuple[OperationAssessmentDefinition, ...] = ()
    presentation_adapters: tuple[PresentationAdapter, ...] = ()
    resource_types: frozenset[str] = frozenset()
    action_ids: frozenset[str] = frozenset()
    business_port_factory: Callable[[], BusinessPort] | None = None
    knowledge_documents: tuple[dict[str, Any], ...] = ()


class AgentModule(Protocol):
    module_id: str
    version: str

    def contribution(self) -> ModuleContribution: ...
