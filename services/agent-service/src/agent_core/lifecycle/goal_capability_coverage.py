from __future__ import annotations

"""Read-only global Goal-Capability coverage for one frozen Goal set.

The solver consumes frozen Goal identities, contract-closed local paths and
Capability Contract v2 metadata.  It may prove many-to-many coverage and rank
safe structural options, but it never dispatches a Tool, issues a permit,
changes semantics, mutates PlanRun or claims business completion.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Iterable

from agent_core.goal_graph.capability_closure import build_typed_goal_capability_coverage
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.semantic_contract import goal_dependency_ids
from agent_core.lifecycle.semantic_contract import prove_goal_target_compatibility
from agent_core.runtime.capability_effects import (
    canonical_effect_identity,
    completion_effects_for_contract,
)

GOAL_CAPABILITY_COVERAGE_VERSION = "goal-capability-coverage@2"
TYPED_GOAL_CAPABILITY_SHADOW_COMPARISON_VERSION = "typed-goal-capability-shadow-comparison@1"


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _goal_rows(goals: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in goals:
        if not isinstance(raw, dict):
            continue
        goal_id = str(raw.get("goal_id") or "").strip()
        identity = canonical_effect_identity(raw.get("requested_effect"))
        if not goal_id:
            continue
        rows.append(
            {
                "goal_id": goal_id,
                "required": bool(raw.get("required", True)),
                "requested_effect": deepcopy(raw.get("requested_effect") or {}),
                "requested_effect_identity": identity or None,
                "expected_result_cardinality": str(
                    raw.get("expected_result_cardinality") or "unknown"
                ),
                "depends_on": goal_dependency_ids(raw),
            }
        )
    return rows


def _shared_prerequisite_bindings(
    goal_plans: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find reuse candidates only inside contract-closed preferred paths.

    GoalOutputRef remains the unique persisted reuse owner.  This projection
    merely shows where the same verified typed output could satisfy more than
    one dependency-ready Goal after Runtime target/scope validation.
    """

    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for goal_plan in goal_plans:
        if not isinstance(goal_plan, dict):
            continue
        goal_id = str(goal_plan.get("goal_id") or "")
        preferred = (
            goal_plan.get("preferred_path")
            if isinstance(goal_plan.get("preferred_path"), dict)
            else {}
        )
        if str(preferred.get("status") or "") != "closed":
            continue
        for step in list(preferred.get("steps") or []):
            if not isinstance(step, dict):
                continue
            consumer_tool = str(step.get("tool_name") or "")
            consumer_step_id = str(step.get("step_id") or "")
            for binding in list(step.get("input_bindings") or []):
                if not isinstance(binding, dict):
                    continue
                if str(binding.get("binding_kind") or "") != "step_output":
                    continue
                producer_tool = str(binding.get("producer_tool") or "")
                type_name = str(binding.get("type_name") or "")
                producer_step_id = str(binding.get("producer_step_id") or "")
                if not goal_id or not producer_tool or not type_name:
                    continue
                grouped.setdefault((producer_tool, type_name), []).append(
                    {
                        "goal_id": goal_id,
                        "consumer_tool": consumer_tool,
                        "consumer_step_id": consumer_step_id,
                        "producer_step_id": producer_step_id,
                    }
                )

    rows: list[dict[str, Any]] = []
    for (producer_tool, type_name), consumers in sorted(grouped.items()):
        if len({row["goal_id"] for row in consumers}) < 2:
            continue
        rows.append(
            {
                "type_name": type_name,
                "producer_tool": producer_tool,
                "consumers": consumers,
                "reuse_owner": "goal_output_refs",
                "reuse_rule": (
                    "one_active_scope_bound_verified_output_may_feed_"
                    "dependency_ready_goals"
                ),
                "requires_runtime_target_compatibility": True,
            }
        )
    return rows


def _proof_rows_for_shared_binding(
    *,
    goal_rows: list[dict[str, Any]],
    goal_ids: list[str],
    tool_name: str,
    capability_registry: CapabilityRegistry,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    contract = capability_registry.contract_for_tool(tool_name)
    if (
        contract is None
        or contract.contract_version != "2"
        or contract.planning_contract is None
    ):
        return {}, ["capability_contract_v2_required"]
    planning = contract.planning_contract
    if planning.completion.mode != "tool_output":
        return {}, ["shared_completion_requires_tool_output_proof"]
    output_name = str(planning.completion.output_name or "")
    output = next(
        (item for item in planning.produces if item.name == output_name),
        None,
    )
    if output is None or not output.completion_proof:
        return {}, ["shared_completion_primary_output_invalid"]

    by_goal = {row["goal_id"]: row for row in goal_rows}
    proofs: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for goal_id in goal_ids:
        identity = str((by_goal.get(goal_id) or {}).get("requested_effect_identity") or "")
        if not identity:
            errors.append(f"per_goal_completion_effect_missing:{goal_id}")
            continue
        proofs[goal_id] = {
            "requested_effect_identity": identity,
            "output_name": output.name,
            "output_type": output.type_name,
            "output_authority": output.authority,
        }
    return proofs, errors


def _preferred_path_by_goal(goal_plans: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in goal_plans:
        goal_id = str(row.get("goal_id") or "")
        path = row.get("preferred_path") if isinstance(row.get("preferred_path"), dict) else None
        if goal_id and path and str(path.get("status") or "") == "closed":
            result[goal_id] = deepcopy(path)
    return result


def _coverage_candidates(
    *,
    required_goal_ids: list[str],
    preferred_paths: dict[str, dict[str, Any]],
    shared_bindings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    required = set(required_goal_ids)
    candidates: list[dict[str, Any]] = []

    independent_missing = sorted(required - set(preferred_paths))
    independent_steps = sum(
        len(list((preferred_paths.get(goal_id) or {}).get("steps") or []))
        for goal_id in required_goal_ids
        if goal_id in preferred_paths
    )
    candidates.append(
        {
            "coverage_id": "coverage:independent-preferred-paths",
            "strategy": "per_goal_preferred_paths",
            "covered_goal_ids": sorted(required - set(independent_missing)),
            "uncovered_goal_ids": independent_missing,
            "shared_binding_ids": [],
            "estimated_dispatch_count": independent_steps,
            "side_effect_policy": "preserve_each_capability_contract",
            "proof_policy": "per_goal_contract_closed_path",
            "coverage_status": "COMPLETE" if not independent_missing else "INCOMPLETE",
        }
    )

    for binding in shared_bindings:
        shared_goals = set(str(value) for value in list(binding.get("goal_ids") or []) if str(value))
        remaining = required - shared_goals
        missing = sorted(remaining - set(preferred_paths))
        remaining_steps = sum(
            len(list((preferred_paths.get(goal_id) or {}).get("steps") or []))
            for goal_id in sorted(remaining)
            if goal_id in preferred_paths
        )
        candidates.append(
            {
                "coverage_id": f"candidate:{binding['coverage_id']}",
                "strategy": "shared_completion_plus_remaining_preferred_paths",
                "covered_goal_ids": sorted((shared_goals & required) | (remaining - set(missing))),
                "uncovered_goal_ids": missing,
                "shared_binding_ids": [str(binding.get("coverage_id") or "")],
                "estimated_dispatch_count": 1 + remaining_steps,
                "side_effect_policy": "shared_dispatch_read_only_only",
                "proof_policy": "shared_primary_completion_output_attested_for_each_goal",
                "coverage_status": "COMPLETE" if not missing else "INCOMPLETE",
            }
        )

    for row in candidates:
        row["candidate_digest"] = _digest(row)
    return candidates


def _global_shared_coverage_candidate(
    *,
    required_goal_ids: list[str],
    preferred_paths: dict[str, dict[str, Any]],
    shared_bindings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the lowest-dispatch safe cover across the current frozen Goal set.

    This is a bounded set-cover calculation over at most the current turn's
    Goal IDs.  It does not create a second plan authority: the result is only a
    read-only coverage option, and individual preferred paths remain owned by
    the existing pre-tool planner.
    """

    ordered_goals = list(dict.fromkeys(str(value) for value in required_goal_ids if str(value)))
    if len(ordered_goals) < 2:
        return None
    goal_bit = {goal_id: 1 << index for index, goal_id in enumerate(ordered_goals)}
    full_mask = (1 << len(ordered_goals)) - 1

    options: list[dict[str, Any]] = []
    for goal_id in ordered_goals:
        path = preferred_paths.get(goal_id)
        if not isinstance(path, dict):
            continue
        steps = [row for row in list(path.get("steps") or []) if isinstance(row, dict)]
        options.append(
            {
                "option_id": f"preferred-path:{goal_id}:{str(path.get('path_id') or '')}",
                "mask": goal_bit[goal_id],
                "dispatch_count": max(1, len(steps)),
                "shared_binding_id": None,
                "independent_goal_id": goal_id,
            }
        )

    for binding in shared_bindings:
        if not isinstance(binding, dict):
            continue
        covered = sorted(
            {
                str(value)
                for value in list(binding.get("goal_ids") or [])
                if str(value) in goal_bit
            }
        )
        if len(covered) < 2:
            continue
        mask = 0
        for goal_id in covered:
            mask |= goal_bit[goal_id]
        options.append(
            {
                "option_id": f"shared:{str(binding.get('coverage_id') or '')}",
                "mask": mask,
                "dispatch_count": 1,
                "shared_binding_id": str(binding.get("coverage_id") or ""),
                "independent_goal_id": None,
            }
        )

    # mask -> (dispatch_count, option_ids, shared_ids, independent_goal_ids)
    best: dict[int, tuple[int, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
        0: (0, (), (), ())
    }
    for option in sorted(options, key=lambda row: str(row.get("option_id") or "")):
        snapshot = list(best.items())
        option_mask = int(option.get("mask") or 0)
        option_id = str(option.get("option_id") or "")
        option_cost = int(option.get("dispatch_count") or 0)
        for mask, (cost, option_ids, shared_ids, independent_goal_ids) in snapshot:
            next_mask = mask | option_mask
            if next_mask == mask:
                continue
            next_shared = shared_ids
            shared_id = str(option.get("shared_binding_id") or "")
            if shared_id:
                next_shared = (*shared_ids, shared_id)
            next_independent = independent_goal_ids
            independent_goal_id = str(option.get("independent_goal_id") or "")
            if independent_goal_id:
                next_independent = (*independent_goal_ids, independent_goal_id)
            candidate = (
                cost + option_cost,
                (*option_ids, option_id),
                next_shared,
                next_independent,
            )
            current = best.get(next_mask)
            if current is None or (candidate[0], len(candidate[1]), candidate[1]) < (
                current[0],
                len(current[1]),
                current[1],
            ):
                best[next_mask] = candidate

    selected = best.get(full_mask)
    if selected is None:
        return None
    dispatch_count, option_ids, shared_ids, independent_goal_ids = selected
    unique_shared = tuple(dict.fromkeys(shared_ids))
    # A single shared binding plus independent paths is already represented by
    # the ordinary per-binding candidates.  Emit this extra candidate only when
    # global solving actually combines multiple shared bindings.
    if len(unique_shared) < 2:
        return None
    payload = {
        "coverage_id": f"coverage:global-optimized:{_digest(option_ids)[:20]}",
        "strategy": "global_safe_shared_set_cover",
        "covered_goal_ids": sorted(ordered_goals),
        "uncovered_goal_ids": [],
        "shared_binding_ids": list(unique_shared),
        "independent_goal_ids": sorted(set(independent_goal_ids)),
        "selected_option_ids": list(option_ids),
        "estimated_dispatch_count": dispatch_count,
        "side_effect_policy": "shared_dispatch_read_only_only",
        "proof_policy": "each_shared_binding_attests_one_primary_completion_output_per_goal_effect",
        "coverage_status": "COMPLETE",
    }
    payload["candidate_digest"] = _digest(payload)
    return payload


def _select_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    complete = [row for row in candidates if str(row.get("coverage_status") or "") == "COMPLETE"]
    if not complete:
        return None
    # Safety/proof rules are encoded before candidates enter this list.  Only
    # after complete safe coverage do we prefer fewer dispatches.
    ranked = sorted(
        complete,
        key=lambda row: (
            int(row.get("estimated_dispatch_count") or 0),
            str(row.get("coverage_id") or ""),
        ),
    )
    return deepcopy(ranked[0])



def _typed_shadow_comparison(
    *,
    legacy_goal_rows: list[dict[str, Any]],
    typed_coverage: dict[str, Any],
) -> dict[str, Any]:
    """Compare typed closure against legacy exact-effect coverage without changing selection."""

    typed_by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(typed_coverage.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    divergences: list[dict[str, Any]] = []

    if str(typed_coverage.get("coverage_status") or "") == "STRUCTURAL_INVALID":
        divergences.append(
            {
                "code": "TYPED_GOAL_GRAPH_STRUCTURAL_INVALID",
                "goal_id": None,
                "typed_dataflow_status": typed_coverage.get("dataflow_status"),
            }
        )

    for legacy in legacy_goal_rows:
        goal_id = str(legacy.get("goal_id") or "")
        legacy_tools = sorted(
            {
                str(value)
                for value in list(legacy.get("completion_tools") or [])
                if str(value)
            }
        )
        typed_row = typed_by_goal.get(goal_id, {})
        typed_tools = sorted(
            {
                str(value)
                for value in list(typed_row.get("closed_capability_tools") or [])
                if str(value)
            }
        )
        if legacy_tools and not typed_tools:
            divergences.append(
                {
                    "code": "LEGACY_COVERED_TYPED_UNCLOSED",
                    "goal_id": goal_id,
                    "legacy_completion_tools": legacy_tools,
                    "typed_closed_capability_tools": [],
                    "typed_status": str(typed_row.get("status") or "MISSING"),
                }
            )
        typed_only = sorted(set(typed_tools) - set(legacy_tools))
        if typed_only:
            divergences.append(
                {
                    "code": "TYPED_CLOSED_LEGACY_UNCOVERED",
                    "goal_id": goal_id,
                    "legacy_completion_tools": legacy_tools,
                    "typed_only_tools": typed_only,
                    "typed_status": str(typed_row.get("status") or "UNKNOWN"),
                }
            )

    legacy_dependencies = {
        str(row.get("goal_id") or ""): sorted(
            {
                str(value)
                for value in list(row.get("depends_on") or [])
                if str(value)
            }
        )
        for row in legacy_goal_rows
        if str(row.get("goal_id") or "")
    }
    typed_dependencies = {
        str(goal_id): sorted(
            {
                str(value)
                for value in list(values or [])
                if str(value)
            }
        )
        for goal_id, values in dict(
            typed_coverage.get("derived_dependencies") or {}
        ).items()
        if str(goal_id)
    }
    for goal_id in sorted(set(legacy_dependencies) | set(typed_dependencies)):
        legacy_depends_on = legacy_dependencies.get(goal_id, [])
        typed_depends_on = typed_dependencies.get(goal_id, [])
        if legacy_depends_on != typed_depends_on:
            divergences.append(
                {
                    "code": "LEGACY_DEPENDENCY_DIFFERS_FROM_VERIFIED_DATAFLOW",
                    "goal_id": goal_id,
                    "legacy_depends_on": legacy_depends_on,
                    "typed_derived_dependencies": typed_depends_on,
                }
            )

    if (
        str(typed_coverage.get("coverage_status") or "") == "DATAFLOW_OPEN"
        and not any(
            row.get("code") == "LEGACY_DEPENDENCY_DIFFERS_FROM_VERIFIED_DATAFLOW"
            for row in divergences
        )
    ):
        divergences.append(
            {
                "code": "TYPED_DATAFLOW_OPEN",
                "goal_id": None,
                "typed_dataflow_status": typed_coverage.get("dataflow_status"),
                "typed_dataflow_errors": list(
                    typed_coverage.get("dataflow_errors") or []
                ),
            }
        )

    payload = {
        "version": TYPED_GOAL_CAPABILITY_SHADOW_COMPARISON_VERSION,
        "authority": "audit_only_legacy_selection_unchanged",
        "status": "MATCHED" if not divergences else "DIVERGED",
        "graph_id": typed_coverage.get("graph_id"),
        "graph_digest": typed_coverage.get("graph_digest"),
        "typed_coverage_digest": typed_coverage.get("coverage_digest"),
        "capability_registry_version": typed_coverage.get(
            "capability_registry_version"
        ),
        "divergences": divergences,
        "typed_shadow_may_widen_legacy_coverage": False,
        "legacy_selection_authority_unchanged": True,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_plan_run": False,
    }
    payload["comparison_digest"] = _digest(payload)
    return payload

def build_goal_capability_coverage(
    *,
    goals: Iterable[dict[str, Any]],
    goal_plans: Iterable[dict[str, Any]],
    capability_registry: CapabilityRegistry,
    typed_goal_graph: dict[str, Any] | None = None,
    frozen_contract: dict[str, Any] | None = None,
    available_input_evidence: tuple[dict[str, Any], ...] = (),
    evaluation_time: float | None = None,
) -> dict[str, Any]:
    """Build global exact coverage without optimizing away Goal identity."""

    source_goals = [deepcopy(row) for row in goals if isinstance(row, dict)]
    goal_rows = _goal_rows(source_goals)
    source_goal_by_id = {str(row.get("goal_id") or ""): row for row in source_goals}
    goal_candidates: dict[str, list[str]] = {row["goal_id"]: [] for row in goal_rows}
    tool_to_goals: dict[str, list[str]] = {}

    for tool_name in sorted(capability_registry.tool_names()):
        contract = capability_registry.contract_for_tool(tool_name)
        if contract is None or contract.execution_kind in {"unsupported", "clarification_read"}:
            continue
        completion = set(completion_effects_for_contract(contract))
        matched = [
            row["goal_id"]
            for row in goal_rows
            if row["requested_effect_identity"]
            and row["requested_effect_identity"] in completion
        ]
        if not matched:
            continue
        tool_to_goals[tool_name] = matched
        for goal_id in matched:
            goal_candidates[goal_id].append(tool_name)

    per_goal: list[dict[str, Any]] = []
    for row in goal_rows:
        goal_id = row["goal_id"]
        candidates = sorted(set(goal_candidates.get(goal_id) or []))
        per_goal.append(
            {
                **deepcopy(row),
                "completion_tools": candidates,
                "coverage_status": "COVERED_BY_EXACT_CAPABILITY" if candidates else "UNCOVERED",
            }
        )

    shared_bindings: list[dict[str, Any]] = []
    shared_rejections: list[dict[str, Any]] = []
    for tool_name, goal_ids in sorted(tool_to_goals.items()):
        if len(goal_ids) <= 1:
            continue
        contract = capability_registry.contract_for_tool(tool_name)
        if contract is None:
            continue
        reasons: list[str] = []
        if contract.writes_business_data or contract.execution_kind == "action_draft":
            reasons.append("shared_write_requires_explicit_atomic_business_strategy")
        proof_rows, proof_errors = _proof_rows_for_shared_binding(
            goal_rows=goal_rows,
            goal_ids=sorted(goal_ids),
            tool_name=tool_name,
            capability_registry=capability_registry,
        )
        reasons.extend(proof_errors)
        target_compatibility = prove_goal_target_compatibility(
            source_goal_by_id[goal_id]
            for goal_id in sorted(goal_ids)
            if goal_id in source_goal_by_id
        )
        if target_compatibility.get("status") == "DIFFERENT":
            reasons.append("shared_target_mismatch")
        elif target_compatibility.get("status") != "SAME":
            reasons.append("shared_target_unproven")
        if reasons:
            shared_rejections.append(
                {
                    "tool_name": tool_name,
                    "goal_ids": sorted(goal_ids),
                    "reasons": sorted(set(reasons)),
                    "target_compatibility": target_compatibility,
                }
            )
            continue
        shared_bindings.append(
            {
                "tool_name": tool_name,
                "goal_ids": sorted(goal_ids),
                "coverage_id": f"coverage:{tool_name}:{'-'.join(sorted(goal_ids))}",
                "coverage_proofs": proof_rows,
                "target_compatibility": target_compatibility,
                "binding_rule": (
                    "one_call_requires_exact_match_proof_for_every_goal_"
                    "compatible_target_and_per_goal_completion_evidence"
                ),
                "writes_business_data": False,
                "requires_runtime_target_compatibility": True,
            }
        )

    goal_plan_rows = [deepcopy(row) for row in goal_plans if isinstance(row, dict)]
    preferred_paths = _preferred_path_by_goal(goal_plan_rows)
    uncovered = [
        row["goal_id"]
        for row in goal_rows
        if row["required"] and not goal_candidates.get(row["goal_id"])
    ]
    required_goal_ids = [row["goal_id"] for row in goal_rows if row["required"]]
    coverage_candidates = _coverage_candidates(
        required_goal_ids=required_goal_ids,
        preferred_paths=preferred_paths,
        shared_bindings=shared_bindings,
    )
    global_candidate = _global_shared_coverage_candidate(
        required_goal_ids=required_goal_ids,
        preferred_paths=preferred_paths,
        shared_bindings=shared_bindings,
    )
    if global_candidate is not None:
        coverage_candidates.append(global_candidate)
    selected = _select_candidate(coverage_candidates)

    payload: dict[str, Any] = {
        "version": GOAL_CAPABILITY_COVERAGE_VERSION,
        "authority": "read_only_exact_coverage_not_execution_authority",
        "matching": "structured_identity_exact_only",
        "capability_registry_version": capability_registry.version,
        "goal_set_digest": _digest(goal_rows),
        "coverage_status": "COMPLETE" if not uncovered else "INCOMPLETE",
        "required_goal_ids": required_goal_ids,
        "uncovered_goal_ids": uncovered,
        "goals": per_goal,
        "shared_capability_bindings": shared_bindings,
        "shared_capability_rejections": shared_rejections,
        "shared_prerequisite_bindings": _shared_prerequisite_bindings(goal_plan_rows),
        "preferred_paths_by_goal": preferred_paths,
        "coverage_candidates": coverage_candidates,
        "selected_coverage": selected,
        "selection_basis": (
            "complete_safe_proven_coverage_before_dispatch_count;"
            "shared_writes_are_not_eligible_without_atomic_strategy_contract"
        ),
        "must_not_dispatch": True,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_plan_run": False,
    }
    if isinstance(typed_goal_graph, dict):
        typed_shadow = build_typed_goal_capability_coverage(
            graph=typed_goal_graph,
            capability_registry=capability_registry,
            frozen_contract=frozen_contract,
            available_input_evidence=tuple(available_input_evidence or ()),
            evaluation_time=evaluation_time,
            legacy_shadow_compatibility=True,
        )
        payload["typed_goal_capability_shadow"] = typed_shadow
        payload["typed_shadow_comparison"] = _typed_shadow_comparison(
            legacy_goal_rows=per_goal,
            typed_coverage=typed_shadow,
        )
    payload["coverage_digest"] = _digest(payload)
    return payload


__all__ = [
    "GOAL_CAPABILITY_COVERAGE_VERSION",
    "TYPED_GOAL_CAPABILITY_SHADOW_COMPARISON_VERSION",
    "build_goal_capability_coverage",
]
