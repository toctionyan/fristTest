from __future__ import annotations

"""Typed, scope-verified target-set data used before any action preview.

The object is deliberately runtime-only.  The runtime does not persist a batch
transaction model; it merely resolves a verified target set so capability
validation can reject unsupported cardinality before preview or Draft creation.
"""

from dataclasses import dataclass
from typing import TypeAlias

from agent_core.resources.registry import ResourcePluginRegistry


# Resolution-basis vocabulary belongs to resource plugins.  The Kernel carries
# the registered value as audit metadata without enumerating domain modes.
ResolutionBasis: TypeAlias = str


@dataclass(frozen=True)
class ResolvedTargetSet:
    resource_type: str
    handles: tuple[str, ...]
    source: str
    scope_verified: bool
    evidence_handles: tuple[str, ...]
    resolution_basis: ResolutionBasis
    resolved_at_turn: int
    canonical_order: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.handles)

    def as_dict(self) -> dict[str, object]:
        return {
            "resource_type": self.resource_type,
            "handles": list(self.handles),
            "source": self.source,
            "scope_verified": self.scope_verified,
            "evidence_handles": list(self.evidence_handles),
            "resolution_basis": self.resolution_basis,
            "resolved_at_turn": self.resolved_at_turn,
            "canonical_order": list(self.canonical_order),
        }


def resolved_target_set(
    *,
    resource_type: str,
    handles: list[str] | tuple[str, ...],
    source: str,
    scope_verified: bool,
    evidence_handles: list[str] | tuple[str, ...],
    resolution_basis: ResolutionBasis,
    resolved_at_turn: int,
) -> ResolvedTargetSet:
    # dict preserves the resolver's stable collection order while de-duplicating.
    canonical = tuple(dict.fromkeys(str(value).strip() for value in handles if str(value).strip()))
    evidence = tuple(dict.fromkeys(str(value).strip() for value in evidence_handles if str(value).strip()))
    return ResolvedTargetSet(
        resource_type=str(resource_type),
        handles=canonical,
        source=str(source),
        scope_verified=bool(scope_verified),
        evidence_handles=evidence,
        resolution_basis=resolution_basis,
        resolved_at_turn=max(0, int(resolved_at_turn or 0)),
        canonical_order=canonical,
    )


class TargetResolver:
    """Constructs only verified runtime target sets.

    Collection parsing may live in a tool-specific resolver, but every action
    entry must pass its verified members through this boundary before
    OperationCapability can inspect cardinality.  It is intentionally
    ephemeral: no TransactionCase or batch state is persisted in the runtime.
    """

    def __init__(self, resource_registry: ResourcePluginRegistry) -> None:
        # Registry construction belongs to Composition.  Resource target
        # validation receives the installed registry explicitly and never
        # reaches back into modules or another global service locator.
        self._resource_registry = resource_registry

    def from_verified_members(
        self,
        *,
        resource_type: str,
        handles: list[str] | tuple[str, ...],
        source: str,
        evidence_handles: list[str] | tuple[str, ...],
        resolution_basis: ResolutionBasis,
        resolved_at_turn: int,
    ) -> ResolvedTargetSet:
        resource = self._resource_registry.require(str(resource_type))
        target_set = resolved_target_set(
            resource_type=resource.resource_type,
            handles=handles,
            source=source,
            scope_verified=True,
            evidence_handles=evidence_handles,
            resolution_basis=resolution_basis,
            resolved_at_turn=resolved_at_turn,
        )
        if not target_set.handles:
            raise ValueError("resolved target set must contain at least one verified handle")
        return target_set
