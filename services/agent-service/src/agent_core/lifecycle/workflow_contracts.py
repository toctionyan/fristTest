from __future__ import annotations

"""Lightweight Workflow contracts for multi-intent Agent Loop execution.

These dictionaries are persisted in graph state, but they are orchestration
state only.  They never become business facts, target authority, form values or
transaction authorization.  Business facts still come from module tools and
Business Service; write operations still cross Draft/Grant/Attempt/Receipt.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


WORKFLOW_RUNTIME_VERSION = "workflow-runtime@2.0"




class GoalCoverageStatus(StrEnum):
    PENDING = "PENDING"
    COVERED = "COVERED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WorkflowGoal:
    goal_id: str
    description: str
    goal_type: str
    evidence_span: str
    requested_effect: dict[str, Any] | None
    expected_tools: tuple[str, ...]
    expected_result_cardinality: str = "unknown"
    depends_on: tuple[str, ...] = ()
    required: bool = True
    coverage_status: GoalCoverageStatus = GoalCoverageStatus.PENDING
    covered_by_step_ids: tuple[str, ...] = ()
    covered_by_terminal_tools: tuple[str, ...] = ()
    satisfaction_proof: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "description": self.description,
            "goal_type": self.goal_type,
            "evidence_span": self.evidence_span,
            "requested_effect": dict(self.requested_effect) if isinstance(self.requested_effect, dict) else None,
            "expected_tools": list(self.expected_tools),
            "expected_result_cardinality": self.expected_result_cardinality,
            "depends_on": list(self.depends_on),
            "required": self.required,
            "coverage_status": self.coverage_status.value,
            "covered_by_step_ids": list(self.covered_by_step_ids),
            "covered_by_terminal_tools": list(self.covered_by_terminal_tools),
            "satisfaction_proof": dict(self.satisfaction_proof) if isinstance(self.satisfaction_proof, dict) else None,
        }


class PlanLevel(StrEnum):
    DIRECT = "L0_DIRECT"
    LIGHTWEIGHT_PLAN = "L1_LIGHTWEIGHT_PLAN"
    WORKFLOW = "L2_WORKFLOW"


class StepKind(StrEnum):
    OBSERVATION = "observation"
    ACTION_DRAFT = "action_draft"
    TERMINAL = "terminal"
    INTERNAL = "internal"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class StepStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    NEEDS_INPUT = "NEEDS_INPUT"
    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    SKIPPED = "SKIPPED"


class WorkflowStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    NEEDS_INPUT = "NEEDS_INPUT"
    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"


class FailureType(StrEnum):
    NONE = "NONE"
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    RATE_LIMIT = "RATE_LIMIT"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    CAPABILITY_EXACT_MATCH_REQUIRED = "CAPABILITY_EXACT_MATCH_REQUIRED"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    UNSUPPORTED_CARDINALITY = "UNSUPPORTED_CARDINALITY"
    BUSINESS_RULE_REJECTED = "BUSINESS_RULE_REJECTED"
    AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    REQUIRES_HUMAN_INPUT = "REQUIRES_HUMAN_INPUT"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    UNKNOWN = "UNKNOWN"


TERMINAL_STEP_STATUSES = {
    StepStatus.SUCCEEDED.value,
    StepStatus.FAILED_FINAL.value,
    StepStatus.NEEDS_INPUT.value,
    StepStatus.AWAITING_AUTHORIZATION.value,
    StepStatus.SUBMISSION_UNKNOWN.value,
    StepStatus.SKIPPED.value,
}


@dataclass(frozen=True)
class AgentStep:
    step_id: str
    effect_id: str | None
    kind: StepKind
    tool_name: str
    capability_id: str | None = None
    goal_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    status: StepStatus = StepStatus.PLANNED
    required: bool = True
    verification: dict[str, Any] = field(default_factory=dict)
    result_summary: str | None = None
    failure_type: FailureType = FailureType.NONE
    failure_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "effect_id": self.effect_id,
            "kind": self.kind.value,
            "tool_name": self.tool_name,
            "capability_id": self.capability_id,
            "goal_ids": list(self.goal_ids),
            "depends_on": list(self.depends_on),
            "status": self.status.value,
            "required": self.required,
            "verification": dict(self.verification),
            "result_summary": self.result_summary,
            "failure_type": self.failure_type.value,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    title: str
    step_ids: tuple[str, ...]
    goal_id: str | None = None
    status: WorkflowStatus = WorkflowStatus.PLANNED
    depends_on: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "step_ids": list(self.step_ids),
            "goal_id": self.goal_id,
            "status": self.status.value,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class WorkflowPlan:
    workflow_id: str
    turn_plan_id: str
    level: PlanLevel
    status: WorkflowStatus
    goal: str
    goals: tuple[WorkflowGoal, ...]
    tasks: tuple[AgentTask, ...]
    steps: tuple[AgentStep, ...]
    created_turn: int
    updated_turn: int
    reasons: tuple[str, ...] = ()
    runtime_authority: str = "orchestration_only_not_business_fact"
    version: str = WORKFLOW_RUNTIME_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "workflow_id": self.workflow_id,
            "turn_plan_id": self.turn_plan_id,
            "level": self.level.value,
            "status": self.status.value,
            "goal": self.goal,
            "goals": [goal.as_dict() for goal in self.goals],
            "goal_coverage_complete": all((not goal.required) or goal.coverage_status != GoalCoverageStatus.PENDING for goal in self.goals),
            "tasks": [task.as_dict() for task in self.tasks],
            "steps": [step.as_dict() for step in self.steps],
            "created_turn": self.created_turn,
            "updated_turn": self.updated_turn,
            "reasons": list(self.reasons),
            "runtime_authority": self.runtime_authority,
        }


def normalize_workflow_status(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in {item.value for item in WorkflowStatus} else WorkflowStatus.PLANNED.value


def step_is_terminal(step: dict[str, Any]) -> bool:
    return str(step.get("status") or "") in TERMINAL_STEP_STATUSES
