"""Workflow registry.

Initial registry implementation. It will later compile definitions into the
LangGraph runtime. It deliberately keeps orchestration separate from Skills
and existing TaskRun authority.
"""

from .models import WorkflowDefinition


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}

    def register(self, workflow: WorkflowDefinition) -> None:
        self._workflows[workflow.id] = workflow

    def get(self, workflow_id: str) -> WorkflowDefinition:
        return self._workflows[workflow_id]

    def list_ids(self) -> list[str]:
        return sorted(self._workflows)
