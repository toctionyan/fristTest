from __future__ import annotations

"""Pre-tool, contract-driven shadow planning.

The shadow planner consumes only the frozen semantic contract, the exact
business-effect capability surface and module-owned Capability Contract v2
snapshots.  It never sees or creates model Tool Calls, ExecutionPermits,
transaction authority or business facts.  Its output is diagnostic evidence
for the V20.14 migration and cannot dispatch or block execution.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

from agent_core.goal_graph.compiler import compile_frozen_semantic_contract
from agent_core.goal_graph.verifier import dataflow_closure
from agent_core.kernel.semantic_contract import GOAL_INPUT_BINDING_AUTHORITY
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.lifecycle.goal_capability_coverage import build_goal_capability_coverage
from agent_core.lifecycle.semantic_contract import (
    assert_semantic_contract_integrity,
    semantic_goals,
)
from agent_core.runtime.capability_effects import discover_exact_effect_surface

PRETOOL_SHADOW_PLAN_VERSION = "pretool-grounded-shadow-plan@1"
PRETOOL_SHADOW_COMPARISON_VERSION = "pretool-shadow-comparison@1"


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _planning_contract(registry: CapabilityRegistry, tool_name: str):
    contract = registry.contract_for_tool(tool_name)
    if contract is None or contract.contract_version != "2" or contract.planning_contract is None:
        return None
    return contract


def _producer_tools_for_type(
    *,
    registry: CapabilityRegistry,
    allowed_tools: Iterable[str],
    type_name: str,
) -> list[str]:
    producers: list[str] = []
    for tool_name in sorted(set(str(value or "") for value in allowed_tools if str(value or ""))):
        contract = _planning_contract(registry, tool_name)
        if contract is None:
            continue
        if any(output.type_name == type_name for output in contract.planning_contract.produces):
            producers.append(tool_name)
    return producers


def _build_candidate_path(
    *,
    goal_id: str,
    completion_tool: str,
    allowed_tools: set[str],
    registry: CapabilityRegistry,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    step_by_tool: dict[str, str] = {}
    unresolved: list[dict[str, Any]] = []
    visiting: list[str] = []

    def add_tool(tool_name: str) -> str | None:
        if tool_name in step_by_tool:
            return step_by_tool[tool_name]
        if tool_name in visiting:
            unresolved.append(
                {
                    "tool_name": tool_name,
                    "reason": "capability_dependency_cycle",
                    "cycle": [*visiting, tool_name],
                }
            )
            return None
        contract = _planning_contract(registry, tool_name)
        if contract is None:
            unresolved.append(
                {
                    "tool_name": tool_name,
                    "reason": "capability_contract_v2_required",
                }
            )
            return None

        visiting.append(tool_name)
        planning = contract.planning_contract
        input_bindings: list[dict[str, Any]] = []
        depends_on_step_ids: list[str] = []
        for required_input in planning.requires:
            sources = tuple(required_input.source_types)
            # A capability-output-only input must be produced by another
            # contract in the exact local surface.  The planner never invents
            # a domain step or guesses by Tool name.
            capability_output_only = sources == ("capability_output",)
            if capability_output_only:
                producers = [
                    name
                    for name in _producer_tools_for_type(
                        registry=registry,
                        allowed_tools=allowed_tools,
                        type_name=required_input.type_name,
                    )
                    if name != tool_name
                ]
                if not producers:
                    unresolved.append(
                        {
                            "tool_name": tool_name,
                            "input_name": required_input.name,
                            "type_name": required_input.type_name,
                            "reason": "required_capability_output_has_no_local_producer",
                        }
                    )
                    input_bindings.append(
                        {
                            **required_input.as_dict(),
                            "binding_kind": "unresolved",
                            "producer_tool_candidates": [],
                        }
                    )
                    continue
                producer_tool = producers[0]
                producer_step_id = add_tool(producer_tool)
                if producer_step_id:
                    depends_on_step_ids.append(producer_step_id)
                input_bindings.append(
                    {
                        **required_input.as_dict(),
                        "binding_kind": "step_output",
                        "producer_tool": producer_tool,
                        "producer_step_id": producer_step_id,
                    }
                )
            else:
                input_bindings.append(
                    {
                        **required_input.as_dict(),
                        "binding_kind": "declared_external_source",
                        "producer_tool": None,
                        "producer_step_id": None,
                    }
                )

        visiting.pop()
        step_id = f"shadow-step:{goal_id}:{len(steps) + 1}:{tool_name}"
        step = {
            "step_id": step_id,
            "goal_id": goal_id,
            "tool_name": tool_name,
            "capability_key": contract.key,
            "contract_version": contract.contract_version,
            "execution_kind": contract.execution_kind,
            "input_bindings": input_bindings,
            "depends_on_step_ids": list(dict.fromkeys(depends_on_step_ids)),
            "produces": [value.as_dict() for value in planning.produces],
            "preconditions": [value.as_dict() for value in planning.preconditions],
            "authorization": planning.authorization.as_dict(),
            "completion": planning.completion.as_dict(),
            "idempotency": planning.idempotency.as_dict(),
            "resource_conflict": planning.resource_conflict.as_dict(),
            "execution_authority": "none_shadow_only",
        }
        steps.append(step)
        step_by_tool[tool_name] = step_id
        return step_id

    terminal_step_id = add_tool(completion_tool)
    status = "closed" if terminal_step_id and not unresolved else "unresolved"
    external_obligations = [
        {
            "step_id": step["step_id"],
            "tool_name": step["tool_name"],
            "input_name": binding["name"],
            "type_name": binding["type_name"],
            "source_types": list(binding.get("source_types") or []),
            "authority": binding.get("authority"),
            "required": bool(binding.get("required", True)),
        }
        for step in steps
        for binding in list(step.get("input_bindings") or [])
        if binding.get("binding_kind") == "declared_external_source"
    ]
    payload = {
        "goal_id": goal_id,
        "completion_tool": completion_tool,
        "status": status,
        "closure_level": "contract_topology_only",
        "runtime_input_resolution_required": external_obligations,
        "execution_readiness": "not_evaluated_shadow",
        "steps": steps,
        "terminal_step_id": terminal_step_id,
        "unresolved_inputs": unresolved,
        "must_not_dispatch": True,
    }
    payload["path_id"] = f"shadow-path:{_digest(payload)[:20]}"
    return payload


def build_pretool_shadow_plan(
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
) -> dict[str, Any]:
    """Build a deterministic local plan before any model business Tool Call.

    The function intentionally ignores ``current_turn_plan.tool_calls`` and all
    execution permits.  It is safe to call before model invocation and returns
    read-only diagnostic data.
    """

    contract = state.get("frozen_semantic_contract")
    assert_semantic_contract_integrity(contract if isinstance(contract, dict) else None)
    goals = semantic_goals(state)
    typed_goal_graph = compile_frozen_semantic_contract(
        contract,
        scope={
            "tenant_id": state.get("current_tenant_id"),
            "user_id": state.get("current_user_id"),
            "thread_id": state.get("current_thread_id"),
        },
    )
    typed_contract = contract.get("dependency_authority") == GOAL_INPUT_BINDING_AUTHORITY
    typed_closure = dataflow_closure(typed_goal_graph, frozen_contract=contract)
    if typed_contract and not typed_closure.get("ok"):
        raise ValueError(
            "TYPED_GOAL_DEPENDENCY_CLOSURE_INVALID:"
            + ",".join(str(value) for value in list(typed_closure.get("errors") or []))
        )
    typed_dependencies = {
        str(goal_id): [str(value) for value in list(values or []) if str(value)]
        for goal_id, values in dict(typed_closure.get("derived_dependencies") or {}).items()
        if str(goal_id)
    }
    surface = discover_exact_effect_surface(capability_registry, goals)
    decisions = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict)
    }

    goal_plans: list[dict[str, Any]] = []
    dependency_edges: list[dict[str, str]] = []
    all_candidate_tools: list[str] = []
    for goal in goals:
        goal_id = str(goal.get("goal_id") or "")
        decision = decisions.get(goal_id, {})
        completion_tools = [
            str(value)
            for value in list(decision.get("completion_tools") or [])
            if str(value)
        ]
        support_tools = [
            str(value)
            for value in list(decision.get("support_tools") or [])
            if str(value)
        ]
        allowed_tools = set([*completion_tools, *support_tools])
        all_candidate_tools.extend(sorted(allowed_tools))
        paths = [
            _build_candidate_path(
                goal_id=goal_id,
                completion_tool=tool_name,
                allowed_tools=allowed_tools,
                registry=capability_registry,
            )
            for tool_name in completion_tools
        ]
        paths.sort(
            key=lambda row: (
                0 if row.get("status") == "closed" else 1,
                len(row.get("steps") or []),
                str(row.get("completion_tool") or ""),
            )
        )
        preferred = deepcopy(paths[0]) if paths and paths[0].get("status") == "closed" else None
        status = str(decision.get("status") or "absent_proven")
        if completion_tools and not any(row.get("status") == "closed" for row in paths):
            status = "contract_unresolved"
        depends_on_goal_ids = (
            list(typed_dependencies.get(goal_id, []))
            if typed_contract
            else [str(value) for value in list(goal.get("depends_on") or []) if str(value)]
        )
        for dependency in depends_on_goal_ids:
            dependency_edges.append({"from_goal_id": dependency, "to_goal_id": goal_id})
        goal_plans.append(
            {
                "goal_id": goal_id,
                "requested_effect": deepcopy(goal.get("requested_effect") or {}),
                "requested_effect_identity": decision.get("requested_effect_identity"),
                "depends_on_goal_ids": depends_on_goal_ids,
                "surface_status": decision.get("status"),
                "status": status,
                "completion_tools": completion_tools,
                "support_tools": support_tools,
                "candidate_paths": paths,
                "preferred_path": preferred,
                "preferred_path_selection_basis": "contract_closed_then_shortest_then_tool_name_not_execution_policy",
                "allowed_shadow_tools": sorted(
                    {
                        str(step.get("tool_name") or "")
                        for path in paths
                        for step in list(path.get("steps") or [])
                        if str(step.get("tool_name") or "")
                    }
                ),
            }
        )

    if any(row.get("status") in {"absent_proven", "completion_capability_absent", "contract_unresolved"} for row in goal_plans):
        status = "UNRESOLVED_SHADOW"
    elif all(row.get("preferred_path") for row in goal_plans):
        status = "READY_SHADOW"
    else:
        status = "MIGRATION_GAP_SHADOW"

    global_coverage = build_goal_capability_coverage(
        goals=goals,
        goal_plans=goal_plans,
        capability_registry=capability_registry,
        typed_goal_graph=typed_goal_graph,
        frozen_contract=contract,
    )
    snapshot = capability_registry.planning_contract_snapshot(sorted(set(all_candidate_tools)))
    payload: dict[str, Any] = {
        "version": PRETOOL_SHADOW_PLAN_VERSION,
        "authority": "shadow_only_not_execution_authority",
        "status": status,
        "formal_semantic_contract_id": contract.get("semantic_contract_id"),
        "formal_semantic_digest": contract.get("semantic_digest"),
        "capability_registry_version": capability_registry.version,
        "capability_snapshot_digest": _digest(snapshot),
        "capability_surface": deepcopy(surface),
        "goal_plans": goal_plans,
        "goal_dependency_edges": dependency_edges,
        "typed_goal_graph": typed_goal_graph,
        "global_goal_capability_coverage": global_coverage,
        "generated_before_model_tool_call": True,
        "observed_model_tool_calls": [],
        "must_not_dispatch": True,
        "creates_permit": False,
        "mutates_semantics": False,
        "execution_readiness": "not_evaluated_shadow",
    }
    payload["plan_digest"] = _digest(payload)
    return payload


def compare_shadow_plan_to_model_calls(
    shadow_plan: dict[str, Any] | None,
    model_calls: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    plan = shadow_plan if isinstance(shadow_plan, dict) else {}
    allowed_by_goal = {
        str(row.get("goal_id") or ""): set(str(value) for value in list(row.get("allowed_shadow_tools") or []) if str(value))
        for row in list(plan.get("goal_plans") or [])
        if isinstance(row, dict)
    }
    completion_by_goal = {
        str(row.get("goal_id") or ""): set(str(value) for value in list(row.get("completion_tools") or []) if str(value))
        for row in list(plan.get("goal_plans") or [])
        if isinstance(row, dict)
    }
    observed: list[dict[str, Any]] = []
    unexpected: list[dict[str, str]] = []
    observed_by_goal: dict[str, set[str]] = {goal_id: set() for goal_id in allowed_by_goal}
    for call in model_calls:
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        goal_ids = [str(value) for value in list(args.get("goal_ids") or []) if str(value)]
        observed.append({"tool_name": tool_name, "goal_ids": goal_ids})
        if not goal_ids:
            unexpected.append({"goal_id": "", "tool_name": tool_name})
            continue
        for goal_id in goal_ids:
            observed_by_goal.setdefault(goal_id, set()).add(tool_name)
            if tool_name not in allowed_by_goal.get(goal_id, set()):
                unexpected.append({"goal_id": goal_id, "tool_name": tool_name})

    first_observed_index: dict[str, int] = {}
    for index, row in enumerate(observed):
        for goal_id in list(row.get("goal_ids") or []):
            first_observed_index.setdefault(str(goal_id), index)
    dependency_order_violations: list[dict[str, Any]] = []
    for edge in list(plan.get("goal_dependency_edges") or []):
        if not isinstance(edge, dict):
            continue
        dependency = str(edge.get("from_goal_id") or "")
        dependent = str(edge.get("to_goal_id") or "")
        if dependent in first_observed_index and (
            dependency not in first_observed_index
            or first_observed_index[dependent] < first_observed_index[dependency]
        ):
            dependency_order_violations.append(
                {
                    "from_goal_id": dependency,
                    "to_goal_id": dependent,
                    "reason": "dependent_goal_observed_before_declared_dependency",
                }
            )

    if unexpected or dependency_order_violations:
        status = "DIVERGED"
    elif not observed:
        status = "NO_MODEL_TOOL_CALLS"
    else:
        completed = all(
            bool(observed_by_goal.get(goal_id, set()) & completion_tools)
            for goal_id, completion_tools in completion_by_goal.items()
            if completion_tools
        )
        status = "MATCHED" if completed else "PARTIAL_MATCH"

    return {
        "version": PRETOOL_SHADOW_COMPARISON_VERSION,
        "shadow_plan_id": str(plan.get("plan_digest") or ""),
        "status": status,
        "observed_model_tool_calls": observed,
        "unexpected_bindings": unexpected,
        "dependency_order_violations": dependency_order_violations,
        "observed_tools_by_goal": {
            goal_id: sorted(values) for goal_id, values in observed_by_goal.items()
        },
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "authority": "diagnostic_comparison_only",
    }


__all__ = [
    "PRETOOL_SHADOW_COMPARISON_VERSION",
    "PRETOOL_SHADOW_PLAN_VERSION",
    "build_pretool_shadow_plan",
    "compare_shadow_plan_to_model_calls",
]
