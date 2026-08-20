"""Composition bootstrap layer.

The bootstrap layer assembles already validated runtime pieces.
It does not become authority for lifecycle, quality, writes, or completion.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompositionBinding:
    composition_id: str
    workflow_id: str
    executor_provider: str
    integration_provider: str


class CompositionBootstrap:
    def __init__(self, registry: dict[str, Any]):
        self._registry = registry

    def resolve(self, composition_id: str) -> CompositionBinding:
        item = next(
            c for c in self._registry["compositions"]
            if c["composition_id"] == composition_id
        )
        return CompositionBinding(
            composition_id=item["composition_id"],
            workflow_id=item["workflow_id"],
            executor_provider=item["providers"]["executor"],
            integration_provider=item["providers"]["integration"],
        )

    def build_runtime_input(self, composition_id: str) -> dict[str, str]:
        binding = self.resolve(composition_id)
        return {
            "workflow_id": binding.workflow_id,
            "composition_id": binding.composition_id,
            "executor_provider": binding.executor_provider,
            "integration_provider": binding.integration_provider,
        }
