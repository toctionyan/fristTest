from __future__ import annotations

"""Domain-neutral Runtime registry contract.

This module deliberately contains no concrete resource, operation or business
implementation. Concrete registration belongs in ``agent_core.composition``.
"""

from dataclasses import dataclass
from agent_core.operations.registry import OperationPluginRegistry
from agent_core.operations.assessment_registry import OperationAssessmentRegistry
from agent_core.resources.registry import ResourcePluginRegistry
from agent_core.kernel.capability_registry import CapabilityRegistry


@dataclass(frozen=True)
class RuntimeRegistry:
    resources: ResourcePluginRegistry
    operations: OperationPluginRegistry
    assessments: OperationAssessmentRegistry
    capabilities: CapabilityRegistry

    def validate_integrity(self) -> None:
        self.capabilities.validate_integrity()
        for operation in self.operations.all():
            if self.resources.get(operation.target_resource_type) is None:
                raise ValueError(
                    f"operation {operation.action_id} targets unregistered resource {operation.target_resource_type}"
                )
            capability = operation.operation_capability
            if operation.target_resource_type not in capability.target_resource_types:
                raise ValueError(f"operation {operation.action_id} has inconsistent capability target")
        for assessment in self.assessments.all():
            if self.resources.get(assessment.target_resource_type) is None:
                raise ValueError(f"assessment {assessment.assessment_id} targets unregistered resource {assessment.target_resource_type}")
            promoted = self.operations.get(assessment.promoted_action_id)
            if promoted is None:
                raise ValueError(f"assessment {assessment.assessment_id} promotes unregistered action {assessment.promoted_action_id}")
            if promoted.target_resource_type != assessment.target_resource_type:
                raise ValueError(f"assessment {assessment.assessment_id} target differs from promoted action")

    def preparable_action_ids(self) -> frozenset[str]:
        return frozenset(self.operations.action_ids())

    def resource_types(self) -> frozenset[str]:
        return frozenset(self.resources.resource_types())

    def assessment_ids(self) -> frozenset[str]:
        return frozenset(self.assessments.ids())
