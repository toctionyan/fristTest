"""Workflow contracts.

The models describe orchestration only. They intentionally do not own task
completion or business truth; those remain in existing TaskRun/Governance
layers.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

StepType = Literal["skill", "executor", "integration", "human_gate"]


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    type: StepType
    target: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    version: str
    steps: tuple[WorkflowStep, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
