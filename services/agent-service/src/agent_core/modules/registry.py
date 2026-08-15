"""AgentModule aggregation.  Core never imports a concrete module here."""
from __future__ import annotations

from typing import Callable

from agent_core.operations.assessment_registry import OperationAssessmentRegistry
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.registry import RuntimeRegistry
from agent_core.operations.registry import OperationPluginRegistry
from agent_core.resources.registry import ResourcePluginRegistry
from .business_router import CompositeBusinessPort
from .contracts import AgentModule, ModuleContribution, PresentationAdapter, SemanticOutputDefinition


_runtime_registry_provider: Callable[[], RuntimeRegistry] | None = None
_module_registry_provider: Callable[[], "ModuleRegistry"] | None = None


def configure_registry_providers(
    *,
    runtime_registry: Callable[[], RuntimeRegistry],
    module_registry: Callable[[], "ModuleRegistry"],
) -> None:
    """Install Composition-owned factories without making Core import Composition."""
    global _runtime_registry_provider, _module_registry_provider
    _runtime_registry_provider = runtime_registry
    _module_registry_provider = module_registry


def current_runtime_registry() -> RuntimeRegistry:
    if _runtime_registry_provider is None:
        raise RuntimeError(
            "runtime registry provider is not configured; initialize the application Composition Root"
        )
    return _runtime_registry_provider()


def current_module_registry() -> "ModuleRegistry":
    if _module_registry_provider is None:
        raise RuntimeError(
            "module registry provider is not configured; initialize the application Composition Root"
        )
    return _module_registry_provider()


class ModuleRegistry:
    def __init__(self, modules: tuple[AgentModule, ...] | list[AgentModule]) -> None:
        self._modules = tuple(modules)
        self._contributions = tuple(module.contribution() for module in self._modules)
        self._validate_module_identity()
        self._validate_semantic_outputs()

    def _validate_module_identity(self) -> None:
        seen: set[str] = set()
        for contribution in self._contributions:
            if not contribution.module_id or not contribution.version:
                raise ValueError("AgentModule requires non-empty module_id and version")
            if contribution.module_id in seen:
                raise ValueError(f"duplicate AgentModule module_id: {contribution.module_id}")
            seen.add(contribution.module_id)

    def _validate_semantic_outputs(self) -> None:
        seen: set[str] = set()
        aliases: dict[str, list[str]] = {}
        for contribution in self._contributions:
            for definition in contribution.semantic_outputs:
                if definition.output_id in seen:
                    raise ValueError(f"duplicate semantic output_id: {definition.output_id}")
                seen.add(definition.output_id)
                for alias in definition.legacy_effect_aliases:
                    aliases.setdefault(alias, []).append(definition.output_id)
        oversized = {alias: values for alias, values in aliases.items() if len(values) > 8}
        if oversized:
            raise ValueError(
                f"legacy semantic alias expands to more than 8 outputs: {sorted(oversized)}"
            )

    def _validate_semantic_outputs(self) -> None:
        seen: set[str] = set()
        aliases: dict[str, list[str]] = {}
        for contribution in self._contributions:
            for definition in contribution.semantic_outputs:
                if definition.output_id in seen:
                    raise ValueError(f"duplicate semantic output_id: {definition.output_id}")
                seen.add(definition.output_id)
                for alias in definition.legacy_effect_aliases:
                    aliases.setdefault(alias, []).append(definition.output_id)
        oversized = {alias: values for alias, values in aliases.items() if len(values) > 8}
        if oversized:
            raise ValueError(f"legacy semantic alias expands to more than 8 outputs: {sorted(oversized)}")

    def module_ids(self) -> frozenset[str]:
        return frozenset(row.module_id for row in self._contributions)

    def semantic_output_definitions(self) -> tuple[SemanticOutputDefinition, ...]:
        return tuple(
            definition
            for contribution in self._contributions
            for definition in contribution.semantic_outputs
        )

    def semantic_output_index(self) -> dict[str, dict[str, object]]:
        return {
            definition.output_id: definition.public_snapshot()
            for definition in self.semantic_output_definitions()
        }

    def semantic_output_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.semantic_output_index()))

    def semantic_vocabulary_snapshot(self) -> dict[str, object]:
        """Public pre-freeze vocabulary. Never expose capability availability."""
        return {
            "version": "semantic-output-vocabulary@2",
            "authority": "domain_semantics_only_capability_independent",
            "availability_exposed": False,
            "tool_names_exposed": False,
            "outputs": [
                definition.public_snapshot()
                for definition in sorted(
                    self.semantic_output_definitions(), key=lambda row: row.output_id
                )
            ],
        }

    def legacy_semantic_output_aliases(self) -> dict[str, tuple[str, ...]]:
        """Internal post-freeze migration compiler from legacy effect identity."""
        aliases: dict[str, list[str]] = {}
        for definition in self.semantic_output_definitions():
            for alias in definition.legacy_effect_aliases:
                aliases.setdefault(alias, []).append(definition.output_id)
        return {
            alias: tuple(sorted(dict.fromkeys(output_ids)))
            for alias, output_ids in aliases.items()
        }

    def semantic_output_definitions(self) -> tuple[SemanticOutputDefinition, ...]:
        return tuple(
            definition
            for contribution in self._contributions
            for definition in contribution.semantic_outputs
        )

    def semantic_output_index(self) -> dict[str, dict[str, object]]:
        return {
            definition.output_id: definition.public_snapshot()
            for definition in self.semantic_output_definitions()
        }

    def semantic_output_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.semantic_output_index()))

    def semantic_vocabulary_snapshot(self) -> dict[str, object]:
        """Public pre-freeze vocabulary. Never expose capability availability."""
        return {
            "version": "semantic-output-vocabulary@2",
            "authority": "domain_semantics_only_capability_independent",
            "availability_exposed": False,
            "tool_names_exposed": False,
            "outputs": [
                definition.public_snapshot()
                for definition in sorted(
                    self.semantic_output_definitions(), key=lambda row: row.output_id
                )
            ],
        }

    def legacy_semantic_output_aliases(self) -> dict[str, tuple[str, ...]]:
        """Internal post-freeze migration compiler from legacy effect identity."""
        aliases: dict[str, list[str]] = {}
        for definition in self.semantic_output_definitions():
            for alias in definition.legacy_effect_aliases:
                aliases.setdefault(alias, []).append(definition.output_id)
        return {
            alias: tuple(sorted(dict.fromkeys(output_ids)))
            for alias, output_ids in aliases.items()
        }

    def build_runtime_registry(self) -> RuntimeRegistry:
        capabilities = tuple(binding for row in self._contributions for binding in row.capabilities)
        resources = tuple(plugin for row in self._contributions for plugin in row.resources)
        operations = tuple(plugin for row in self._contributions for plugin in row.operations)
        assessments = tuple(item for row in self._contributions for item in row.assessments)
        registry = RuntimeRegistry(
            resources=ResourcePluginRegistry(resources, allow_empty=not resources),
            operations=OperationPluginRegistry(operations),
            assessments=OperationAssessmentRegistry(assessments),
            capabilities=CapabilityRegistry(
                capabilities, version="module-registry@3.8", allow_empty=not capabilities
            ),
        )
        registry.validate_integrity()
        return registry

    def presentation_adapters(self) -> tuple[PresentationAdapter, ...]:
        """Return installed adapters without constructing the presentation subsystem."""
        return tuple(adapter for row in self._contributions for adapter in row.presentation_adapters)

    def builtin_knowledge_documents(self) -> tuple[dict[str, object], ...]:
        documents: list[dict[str, object]] = []
        seen: set[str] = set()
        for contribution in self._contributions:
            for raw in contribution.knowledge_documents:
                row = dict(raw or {})
                doc_id = str(row.get("doc_id") or "").strip()
                if not doc_id:
                    raise ValueError(f"module {contribution.module_id} knowledge document requires doc_id")
                if doc_id in seen:
                    raise ValueError(f"duplicate installed knowledge doc_id: {doc_id}")
                seen.add(doc_id)
                documents.append(row)
        return tuple(documents)

    def build_business_port(self) -> CompositeBusinessPort:
        resource_ports: dict[str, object] = {}
        action_ports: dict[str, object] = {}
        for row in self._contributions:
            if row.business_port_factory is None:
                continue
            port = row.business_port_factory()
            for resource_type in row.resource_types:
                if resource_type in resource_ports:
                    raise ValueError(f"duplicate resource owner: {resource_type}")
                resource_ports[resource_type] = port
            for action_id in row.action_ids:
                if action_id in action_ports:
                    raise ValueError(f"duplicate action owner: {action_id}")
                action_ports[action_id] = port
        return CompositeBusinessPort(resource_ports=resource_ports, action_ports=action_ports)
