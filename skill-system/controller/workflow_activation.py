from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from capability_registry import (
    CapabilityPreflight,
    load_capability_contracts,
    preflight_capabilities,
)
from workflow_registry import WorkflowSpec, require_workflow

WORKFLOW_ACTIVATION_SCHEMA = "workflow-activation@1"


class WorkflowActivationError(ValueError):
    """Raised when a Workflow cannot be activated without violating authority boundaries."""


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
                "capability_resolution_grants_write_authority": False,
                "taskrun_authority_changed": False,
                "quality_authority_changed": False,
                "completion_authority_changed": False,
                "write_authority_changed": False,
            },
        }


def _assert_mutation_boundary(workspace: Path, workflow: WorkflowSpec) -> None:
    contracts = load_capability_contracts(workspace)
    mutating = sorted(
        capability_id
        for capability_id in (*workflow.required_capabilities, *workflow.optional_capabilities)
        if contracts.get(capability_id) is not None and contracts[capability_id].mutates
    )
    if mutating and not workflow.write_governed:
        raise WorkflowActivationError(
            f"workflow {workflow.workflow_id!r} requests mutating capabilities without write_governed=true: {mutating}"
        )


def activate_workflow(
    workspace: Path,
    *,
    workflow_id: str,
    available_provider_ids: Iterable[str] = (),
    provider_preferences: Mapping[str, str] | None = None,
) -> WorkflowActivation:
    workflow = require_workflow(workspace, workflow_id)
    return activate_workflow_spec(
        workspace,
        workflow=workflow,
        available_provider_ids=available_provider_ids,
        provider_preferences=provider_preferences,
    )


def activate_workflow_spec(
    workspace: Path,
    *,
    workflow: WorkflowSpec,
    available_provider_ids: Iterable[str] = (),
    provider_preferences: Mapping[str, str] | None = None,
) -> WorkflowActivation:
    """Activate one already-canonical WorkflowSpec without another registry write.

    Installed Starter adapters compile through ``parse_workflow_spec`` first and
    pass that exact object here. Capability contracts, Provider resolution,
    mutation checks, and activation projection remain identical to registry-ID
    activation.
    """

    if not isinstance(workflow, WorkflowSpec):
        raise WorkflowActivationError("workflow must be a canonical WorkflowSpec")
    _assert_mutation_boundary(workspace, workflow)
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
    "WorkflowActivationError",
    "activate_workflow",
    "activate_workflow_spec",
]
