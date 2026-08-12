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
    """Structural module contribution consumed by the presentation subsystem."""

    adapter_id: str
    priority: int

    def blocks_from_trace(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project verified observations into client-neutral public blocks."""


@dataclass(frozen=True)
class SemanticOutputDefinition:
    """Capability-independent user-meaning type contributed by a domain module.

    ``legacy_effect_aliases`` are migration metadata consumed only after the
    semantic contract is frozen. They are deliberately excluded from the
    public writer vocabulary so installed capability availability cannot leak
    back into language understanding.
    """

    output_id: str
    subject_type: str
    effect_kinds: tuple[str, ...]
    description: str
    legacy_effect_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        output_id = str(self.output_id or "").strip().casefold()
        subject_type = str(self.subject_type or "").strip().casefold()
        effect_kinds = tuple(dict.fromkeys(str(value or "").strip().casefold() for value in self.effect_kinds if str(value or "").strip()))
        description = str(self.description or "").strip()
        aliases = tuple(dict.fromkeys(str(value or "").strip().casefold() for value in self.legacy_effect_aliases if str(value or "").strip()))
        if not output_id or output_id == "open":
            raise ValueError("semantic output_id must be non-empty and cannot use reserved 'open'")
        if not subject_type:
            raise ValueError(f"semantic output {output_id} requires subject_type")
        if not effect_kinds:
            raise ValueError(f"semantic output {output_id} requires effect_kinds")
        if not description:
            raise ValueError(f"semantic output {output_id} requires description")
        object.__setattr__(self, "output_id", output_id)
        object.__setattr__(self, "subject_type", subject_type)
        object.__setattr__(self, "effect_kinds", effect_kinds)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "legacy_effect_aliases", aliases)

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "output_id": self.output_id,
            "subject_type": self.subject_type,
            "effect_kinds": list(self.effect_kinds),
            "description": self.description,
        }


@dataclass(frozen=True)
class ModuleContribution:
    module_id: str
    version: str
    capabilities: tuple[CapabilityBinding, ...]
    resources: tuple[ResourcePlugin, ...] = ()
    operations: tuple[OperationPlugin, ...] = ()
    assessments: tuple[OperationAssessmentDefinition, ...] = ()
    presentation_adapters: tuple[PresentationAdapter, ...] = ()
    semantic_outputs: tuple[SemanticOutputDefinition, ...] = ()
    resource_types: frozenset[str] = frozenset()
    action_ids: frozenset[str] = frozenset()
    business_port_factory: Callable[[], BusinessPort] | None = None
    knowledge_documents: tuple[dict[str, Any], ...] = ()


class AgentModule(Protocol):
    module_id: str
    version: str

    def contribution(self) -> ModuleContribution: ...
