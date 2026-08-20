from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from capability_registry import CapabilityPreflight, preflight_capabilities
from workflow_registry import WorkflowSpec, require_workflow

WORKFLOW_ACTIVATION_SCHEMA = "workflow-activation@1"


@dataclass(frozen=True)
class WorkflowActivation:
    workflow: WorkflowSpec
    capability_preflight: CapabilityPreflight

    @property
    def ready(self) -> bool:
        return self.capability_preflight.ready

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_ACTIVATION_SCHEMA,
            "status": "PASS" if self.ready else "BLOCKED_CONFIGURATION",
            "workflow": self.workflow.as_dict(),
            "required_skills": list(self.workflow.skills),
            "capability_preflight": self.capability_preflight.as_dict(),
            "policy": {
                "workflow_is_provider_neutral": True,
                "provider_binding_occurs_at_activation": True,
                "taskrun_authority_changed": False,
                "quality_authority_changed": False,
                "completion_authority_changed": False,
                "write_authority_changed": False,
            },
        }


def activate_workflow(
    workspace: Path,
    *,
    workflow_id: str,
    available_provider_ids: Iterable[str] = (),
    provider_preferences: Mapping[str, str] | None = None,
) -> WorkflowActivation:
    workflow = require_workflow(workspace, workflow_id)
    preflight = preflight_capabilities(
        workspace,
        required=workflow.required_capabilities,
        optional=workflow.optional_capabilities,
        available_provider_ids=available_provider_ids,
        provider_preferences=provider_preferences,
    )
    return WorkflowActivation(workflow=workflow, capability_preflight=preflight)


__all__ = [
    "WORKFLOW_ACTIVATION_SCHEMA",
    "WorkflowActivation",
    "activate_workflow",
]
