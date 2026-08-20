from __future__ import annotations

"""Workflow registry foundation.

Workflows compose Skills, Executors and Integrations. This module intentionally
contains no business logic; it only resolves declarative workflow definitions.
"""

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    version: str
    steps: list[dict[str, Any]]


class WorkflowRegistryError(ValueError):
    pass


class WorkflowRegistry:
    def __init__(self, registry_file: Path):
        self.registry_file = registry_file

    def load(self) -> dict[str, WorkflowDefinition]:
        if not self.registry_file.exists():
            return {}
        payload = json.loads(self.registry_file.read_text(encoding="utf-8"))
        workflows: dict[str, WorkflowDefinition] = {}
        for item in payload.get("workflows", []):
            workflow_id = str(item.get("id", "")).strip()
            if not workflow_id:
                raise WorkflowRegistryError("workflow id is required")
            workflows[workflow_id] = WorkflowDefinition(
                workflow_id=workflow_id,
                version=str(item.get("version", "1")),
                steps=list(item.get("steps", [])),
            )
        return workflows

    def resolve(self, workflow_id: str) -> WorkflowDefinition:
        workflows = self.load()
        if workflow_id not in workflows:
            raise WorkflowRegistryError(f"workflow not found: {workflow_id}")
        return workflows[workflow_id]
