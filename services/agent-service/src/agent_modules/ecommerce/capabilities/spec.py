"""Single-source capability definition contract for the ecommerce module."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from agent_core.kernel.capability import (
    CapabilityPlanningContract,
    CapabilityTargetArgumentProjection,
    ToolCapabilityContract,
)

Executor = Callable[..., dict[str, Any]]

# The ecommerce module owns the mapping from an opaque compiled binding into
# its target DSL.  Core Runtime consumes this declaration without learning
# vertical field names or values.
ECOMMERCE_TARGET_ARGUMENT_PROJECTION = CapabilityTargetArgumentProjection(
    argument_name="target",
    constant_fields=(("mode", "artifact"),),
    binding_fields=(("left_handle", "member_handle"),),
)


@dataclass(frozen=True)
class EcommerceCapabilityDefinition:
    key: str
    tool_name: str
    category: str
    planner_rule: str
    execution_kind: str
    schema: dict[str, Any]
    executor: Executor
    presentation_contract: str
    public_label: str | None = None
    writes_business_data: bool = False
    evidence_sources: tuple[str, ...] = ("business_service", "verified_ledger")
    goal_completion_types: tuple[str, ...] = ()
    goal_support_types: tuple[str, ...] = ()
    completion_effects: tuple[str, ...] = ()
    support_effects: tuple[str, ...] = ()
    discovery_examples: tuple[str, ...] = ()
    exclusion_examples: tuple[str, ...] = ()
    contract_version: str = "1"
    planning_contract: CapabilityPlanningContract | None = None

    @property
    def contract(self) -> ToolCapabilityContract:
        planning_contract = self.planning_contract
        if planning_contract is not None:
            target = planning_contract.target
            if (
                target.cardinality != "none"
                and "target_resolver" in set(target.binding_sources)
                and target.argument_projection is None
            ):
                planning_contract = replace(
                    planning_contract,
                    target=replace(
                        target,
                        argument_projection=ECOMMERCE_TARGET_ARGUMENT_PROJECTION,
                    ),
                )
        return ToolCapabilityContract(
            key=self.key,
            tool_name=self.tool_name,
            category=self.category,
            writes_business_data=self.writes_business_data,
            evidence_sources=self.evidence_sources,
            planner_rule=self.planner_rule,
            unavailable_response="当前没有与该请求精确匹配的已启用能力。",
            execution_kind=self.execution_kind,
            goal_completion_types=self.goal_completion_types,
            goal_support_types=self.goal_support_types,
            completion_effects=self.completion_effects,
            support_effects=self.support_effects,
            discovery_examples=self.discovery_examples,
            exclusion_examples=self.exclusion_examples,
            contract_version=self.contract_version,
            planning_contract=planning_contract,
        )
