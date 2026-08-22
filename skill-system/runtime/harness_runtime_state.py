from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HarnessRuntimeStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    BLOCKED = "BLOCKED"
    FLOW_ENDED = "FLOW_ENDED"


class HarnessRuntimeState(BaseModel):
    """Execution context for Harness workflows.

    This is intentionally not a replacement for TaskRun authority.
    TaskRun remains the lifecycle and completion authority.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    workflow_id: str
    current_step: str | None = None
    status: HarnessRuntimeStatus = HarnessRuntimeStatus.CREATED
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple)
    receipts: tuple[str, ...] = Field(default_factory=tuple)
    context: dict[str, Any] = Field(default_factory=dict)
    completion_authority: Literal["TaskRun"] = "TaskRun"
    authority_effect: Literal[False] = False


__all__ = ["HarnessRuntimeState", "HarnessRuntimeStatus"]
