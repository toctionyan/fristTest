from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HarnessTaskStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"


class HarnessRuntimeState(BaseModel):
    """Execution context for Harness workflows.

    This is intentionally not a replacement for TaskRun authority.
    TaskRun remains the lifecycle and completion authority.
    """

    task_id: str
    workflow_id: str
    current_step: str | None = None
    status: HarnessTaskStatus = HarnessTaskStatus.CREATED
    evidence_refs: list[str] = Field(default_factory=list)
    receipts: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


__all__ = ["HarnessRuntimeState", "HarnessTaskStatus"]
