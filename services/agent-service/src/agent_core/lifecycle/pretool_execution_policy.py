from __future__ import annotations

"""Compile the pre-tool capability frontier used by the model call.

This module is deliberately narrower than the formal workflow runtime:

* FrozenSemanticContract owns user goals.
* Capability Contract v2 owns capability topology.
* This policy owns only which registered business capabilities may be exposed
  on the *next* model call.
* MatchProof, ExecutionPermit, target resolution, transaction authority and
  Business Service remain downstream authorities.

The policy never invents arguments, business facts, goal semantics or permits.
It converts the existing pre-tool contract plan into a bounded provider tool
surface and records explicit migration fallbacks for capabilities that have not
reached Contract v2 yet.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from agent_core.goal_graph.dependency_authority import build_dependency_authority_attestation
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.semantic_contract import semantic_goals
from agent_core.lifecycle.semantic_contract import prove_goal_target_compatibility
from agent_core.lifecycle.plan_execution import (
    validate_frozen_plan_definition,
    validate_plan_run,
)
from agent_core.lifecycle.pretool_planner import build_pretool_shadow_plan
from agent_core.lifecycle.goal_outputs import reusable_goal_outputs_for_goal

PRETOOL_EXECUTION_POLICY_VERSION = "pretool-execution-policy@1"
TYPED_DEPENDENCY_AUTHORITY_SHADOW_VERSION = "typed-dependency-authority-shadow@1"
_PROGRESS_STEP_STATES = {"SUCCEEDED"}
_COMPLETED_GOAL_LIFECYCLES = {"COMPLETED"}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _validated_global_coverage(
    plan: dict[str, Any],
    *,
    capability_registry: CapabilityRegistry,
) -> tuple[dict[str, Any], list[str]]:
    coverage = (
        plan.get("global_goal_capability_coverage")
        if isinstance(plan.get("global_goal_capability_coverage"), dict)
        else {}
    )
    if not coverage:
        return {}, []
    errors: list[str] = []
    stored = str(coverage.get("coverage_digest") or "")
    if not stored:
        errors.append("GLOBAL_GOAL_CAPABILITY_COVERAGE_DIGEST_REQUIRED")
    else:
        payload = deepcopy(coverage)
        payload.pop("coverage_digest", None)
        if stored != _digest(payload):
            errors.append("GLOBAL_GOAL_CAPABILITY_COVERAGE_DIGEST_INVALID")
    registry_version = str(coverage.get("capability_registry_version") or "")
    if registry_version and registry_version != str(capability_registry.version or ""):
        errors.append("GLOBAL_GOAL_CAPABILITY_COVERAGE_REGISTRY_MISMATCH")
    return ({} if errors else coverage), errors


def _completed_goal_ids(state: dict[str, Any]) -> set[str]:
    """Read dependency completion only from the durable Goal lifecycle authority.

    PlanRun ``terminal_goal_states`` is a dialogue/execution projection.  It is
    intentionally not used to unlock another goal because ``validate_plan_run``
    proves structural compatibility, not the semantic integrity of arbitrary
    terminal-state mutations.
    """

    return {
        str(row.get("goal_id") or "")
        for row in list(state.get("goal_records") or [])
        if isinstance(row, dict)
        and str(row.get("lifecycle") or "").upper() in _COMPLETED_GOAL_LIFECYCLES
        and str(row.get("goal_id") or "")
    }


def _completed_tools_by_goal(state: dict[str, Any]) -> tuple[dict[str, set[str]], list[str]]:
    """Read only integrity-checked PlanRun evidence from the preceding loop."""

    definition = state.get("frozen_plan_definition")
    run = state.get("plan_run")
    if not isinstance(definition, dict) or not isinstance(run, dict):
        return {}, []

    definition_check = validate_frozen_plan_definition(definition)
    if not definition_check.get("ok"):
        return {}, [str(definition_check.get("code") or "FROZEN_PLAN_DEFINITION_INVALID")]
    run_check = validate_plan_run(definition=definition, plan_run=run)
    if not run_check.get("ok"):
        return {}, [str(run_check.get("code") or "PLAN_RUN_INVALID")]

    completed: dict[str, set[str]] = {}
    step_states = run.get("step_states") if isinstance(run.get("step_states"), dict) else {}
    for step in list(definition.get("steps") or []):
        if not isinstance(step, dict):
            continue
        effect_id = str(step.get("effect_id") or "")
        tool_name = str(step.get("tool_name") or "")
        step_state = step_states.get(effect_id) if isinstance(step_states.get(effect_id), dict) else {}
        if not effect_id or not tool_name or str(step_state.get("status") or "") not in _PROGRESS_STEP_STATES:
            continue
        for goal_id in [str(value) for value in list(step.get("goal_ids") or []) if str(value)]:
            completed.setdefault(goal_id, set()).add(tool_name)
    return completed, []


def _path_frontier(
    path: dict[str, Any],
    *,
    completed_tools: set[str],
) -> tuple[list[str], int, bool]:
    steps = [row for row in list(path.get("steps") or []) if isinstance(row, dict)]
    tool_by_step_id = {
        str(row.get("step_id") or ""): str(row.get("tool_name") or "")
        for row in steps
        if str(row.get("step_id") or "") and str(row.get("tool_name") or "")
    }
    progress = sum(
        1 for row in steps if str(row.get("tool_name") or "") in completed_tools
    )
    for step in steps:
        tool_name = str(step.get("tool_name") or "")
        if not tool_name or tool_name in completed_tools:
            continue
        dependency_tools = {
            tool_by_step_id.get(str(step_id), "")
            for step_id in list(step.get("depends_on_step_ids") or [])
        }
        dependency_tools.discard("")
        if dependency_tools <= completed_tools:
            return [tool_name], progress, False
        return [], progress, False
    return [], progress, True


def _unsupported_tools_for_goal(
    *,
    registry: CapabilityRegistry,
    surface_decision: dict[str, Any],
) -> list[str]:
    result: list[str] = []
    for tool_name in list(surface_decision.get("candidate_tools") or []):
        name = str(tool_name or "")
        contract = registry.contract_for_tool(name)
        if contract is not None and str(contract.execution_kind or "") == "unsupported":
            result.append(name)
    return sorted(set(result))


def _legacy_exact_tools(surface_decision: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(value)
            for value in list(surface_decision.get("candidate_tools") or [])
            if str(value)
        }
    )


def _typed_dependency_authority_shadow(
    *,
    plan: dict[str, Any],
    global_coverage: dict[str, Any],
    completed_goal_ids: set[str],
) -> dict[str, Any] | None:
    """Audit legacy dependency blocking against verified Goal Graph dataflow.

    This is migration evidence only. The current provider frontier continues to
    use ``depends_on_goal_ids`` until a later explicit authority cutover.
    """

    typed = (
        global_coverage.get("typed_goal_capability_shadow")
        if isinstance(global_coverage.get("typed_goal_capability_shadow"), dict)
        else None
    )
    if typed is None:
        return None

    evidence_errors: list[str] = []
    stored_typed_digest = str(typed.get("coverage_digest") or "")
    if not stored_typed_digest:
        evidence_errors.append("TYPED_DEPENDENCY_SHADOW_COVERAGE_DIGEST_REQUIRED")
    else:
        typed_payload = deepcopy(typed)
        typed_payload.pop("coverage_digest", None)
        if stored_typed_digest != _digest(typed_payload):
            evidence_errors.append("TYPED_DEPENDENCY_SHADOW_COVERAGE_DIGEST_INVALID")

    legacy_dependencies = {
        str(row.get("goal_id") or ""): sorted(
            {
                str(value)
                for value in list(row.get("depends_on_goal_ids") or [])
                if str(value)
            }
        )
        for row in list(plan.get("goal_plans") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    typed_dependencies = {
        str(goal_id): sorted(
            {
                str(value)
                for value in list(values or [])
                if str(value)
            }
        )
        for goal_id, values in dict(typed.get("derived_dependencies") or {}).items()
        if str(goal_id)
    }
    dataflow_status = str(typed.get("dataflow_status") or "")
    dataflow_closed = dataflow_status == "GOAL_GRAPH_DATAFLOW_CLOSED"

    comparisons: list[dict[str, Any]] = []
    divergence_codes: set[str] = set()
    for goal_id in sorted(set(legacy_dependencies) | set(typed_dependencies)):
        legacy = set(legacy_dependencies.get(goal_id, []))
        verified = set(typed_dependencies.get(goal_id, []))
        legacy_only = sorted(legacy - verified)
        typed_only = sorted(verified - legacy)
        codes: list[str] = []
        if legacy_only:
            codes.append("LEGACY_DEPENDENCY_NOT_VERIFIED_BY_DATAFLOW")
        if typed_only:
            codes.append("VERIFIED_DATAFLOW_DEPENDENCY_MISSING_FROM_LEGACY")
        divergence_codes.update(codes)
        legacy_missing = sorted(legacy - completed_goal_ids)
        typed_missing = sorted(verified - completed_goal_ids)
        comparisons.append(
            {
                "goal_id": goal_id,
                "legacy_dependency_goal_ids": sorted(legacy),
                "typed_derived_dependency_goal_ids": sorted(verified),
                "legacy_only_dependency_goal_ids": legacy_only,
                "typed_only_dependency_goal_ids": typed_only,
                "legacy_missing_dependency_goal_ids": legacy_missing,
                "typed_missing_dependency_goal_ids": typed_missing if dataflow_closed else [],
                "current_legacy_would_block": bool(legacy_missing),
                "typed_would_block": bool(typed_missing) if dataflow_closed else None,
                "codes": codes or ["DEPENDENCY_SET_MATCHED"],
            }
        )

    if evidence_errors:
        status = "EVIDENCE_INVALID"
    elif not dataflow_closed:
        status = "NOT_READY_DATAFLOW_OPEN"
    elif divergence_codes:
        status = "DIVERGED"
    else:
        status = "MATCHED"

    payload = {
        "version": TYPED_DEPENDENCY_AUTHORITY_SHADOW_VERSION,
        "authority": "audit_only_current_dependency_enforcement_unchanged",
        "status": status,
        "current_dependency_authority": "legacy_declared_goal_dependencies",
        "candidate_dependency_authority": "verified_dataflow_edges_only",
        "typed_coverage_status": typed.get("coverage_status"),
        "typed_dataflow_status": dataflow_status or None,
        "typed_coverage_digest": stored_typed_digest or None,
        "typed_graph_id": typed.get("graph_id"),
        "typed_graph_digest": typed.get("graph_digest"),
        "evidence_errors": evidence_errors,
        "comparisons": comparisons,
        "divergence_codes": sorted(divergence_codes),
        "cutover_eligible": bool(
            not evidence_errors and dataflow_closed and not divergence_codes
        ),
        "cutover_performed": False,
        "changes_current_dependency_blocking": False,
        "changes_allowed_capability_tools": False,
        "blocks_execution": False,
        "creates_permit": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
    }
    payload["shadow_digest"] = _digest(payload)
    return payload


def build_pretool_execution_policy(
    *,
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    shadow_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the provider capability frontier for the next model call.

    Contract-v2 goals are enforced by topology and prior PlanRun progress.
    Non-v2 goals retain the already exact effect surface as an explicit,
    auditable migration fallback; they are never silently presented as fully
    governed.
    """

    plan = deepcopy(shadow_plan) if isinstance(shadow_plan, dict) else build_pretool_shadow_plan(
        state=state,
        capability_registry=capability_registry,
    )
    surface = plan.get("capability_surface") if isinstance(plan.get("capability_surface"), dict) else {}
    surface_by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    completed_tools_by_goal, evidence_errors = _completed_tools_by_goal(state)
    completed_goal_ids = _completed_goal_ids(state)

    goal_policies: list[dict[str, Any]] = []
    allowed_tools: set[str] = set()
    migration_gap_goal_ids: list[str] = []
    dependency_blocked_goal_ids: list[str] = []

    for goal_plan in list(plan.get("goal_plans") or []):
        if not isinstance(goal_plan, dict):
            continue
        goal_id = str(goal_plan.get("goal_id") or "")
        decision = surface_by_goal.get(goal_id, {})
        dependencies = {
            str(value)
            for value in list(goal_plan.get("depends_on_goal_ids") or [])
            if str(value)
        }
        completed_tools = set(completed_tools_by_goal.get(goal_id, set()))
        reusable_by_tool, reusable_outputs, output_evidence_errors = reusable_goal_outputs_for_goal(
            state=state,
            capability_registry=capability_registry,
            goal_plan=goal_plan,
            dependency_goal_ids=dependencies,
        )
        completed_tools.update(reusable_by_tool)
        reusable_output_ids = sorted({
            str(row.get("goal_output_ref_id") or "")
            for row in reusable_outputs
            if str(row.get("goal_output_ref_id") or "")
        })
        surface_status = str(decision.get("status") or goal_plan.get("surface_status") or "")

        if goal_id in completed_goal_ids:
            row = {
                "goal_id": goal_id,
                "status": "GOAL_ALREADY_COMPLETED",
                "enforcement": "contract_frontier",
                "depends_on_goal_ids": sorted(dependencies),
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": [],
                "active_path_ids": [],
                "reason": "goal_lifecycle_completed",
            }
            goal_policies.append(row)
            continue

        missing_dependencies = sorted(dependencies - completed_goal_ids)
        if missing_dependencies:
            dependency_blocked_goal_ids.append(goal_id)
            goal_policies.append({
                "goal_id": goal_id,
                "status": "BLOCKED_BY_GOAL_DEPENDENCY",
                "enforcement": "contract_frontier",
                "depends_on_goal_ids": sorted(dependencies),
                "missing_dependency_goal_ids": missing_dependencies,
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": [],
                "active_path_ids": [],
                "reason": "declared_goal_dependency_not_completed",
            })
            continue

        if surface_status in {"absent_proven", "completion_capability_absent"}:
            unsupported = _unsupported_tools_for_goal(
                registry=capability_registry,
                surface_decision=decision,
            )
            allowed_tools.update(unsupported)
            goal_policies.append({
                "goal_id": goal_id,
                "status": "UNSUPPORTED_EXACTLY_PROVEN",
                "enforcement": "exact_absence_report",
                "depends_on_goal_ids": sorted(dependencies),
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": unsupported,
                "active_path_ids": [],
                "reason": "no_exact_completion_capability",
            })
            continue

        closed_paths = [
            row
            for row in list(goal_plan.get("candidate_paths") or [])
            if isinstance(row, dict) and str(row.get("status") or "") == "closed"
        ]
        if not closed_paths:
            fallback = _legacy_exact_tools(decision)
            migration_gap_goal_ids.append(goal_id)
            allowed_tools.update(fallback)
            goal_policies.append({
                "goal_id": goal_id,
                "status": "CONTRACT_V2_MIGRATION_GAP",
                "enforcement": "legacy_exact_surface_fallback",
                "depends_on_goal_ids": sorted(dependencies),
                "completed_tools": sorted(completed_tools),
                "reused_goal_output_ref_ids": reusable_output_ids,
                "goal_output_evidence_errors": output_evidence_errors,
                "allowed_tools": fallback,
                "active_path_ids": [],
                "reason": "no_contract_closed_path",
            })
            continue

        evaluated: list[dict[str, Any]] = []
        for path in closed_paths:
            frontier, progress, complete = _path_frontier(
                path,
                completed_tools=completed_tools,
            )
            evaluated.append({
                "path_id": str(path.get("path_id") or ""),
                "completion_tool": str(path.get("completion_tool") or ""),
                "progress": progress,
                "frontier": frontier,
                "path_complete": complete,
            })
        max_progress = max((int(row.get("progress") or 0) for row in evaluated), default=0)
        active_paths = [row for row in evaluated if int(row.get("progress") or 0) == max_progress]
        frontier = sorted({tool for row in active_paths for tool in list(row.get("frontier") or [])})
        all_active_complete = bool(active_paths) and all(bool(row.get("path_complete")) for row in active_paths)

        # Action-draft completion may require an authoritative target even when
        # the Contract-v2 target binding can theoretically come from an external
        # resolver.  Exact registered read-support tools are therefore exposed
        # as an optional bounded frontier before the draft.  They do not satisfy
        # the Goal, do not grant target authority, and disappear for this Goal
        # after their PlanRun step succeeds.
        active_completion_tools = {
            str(row.get("completion_tool") or "")
            for row in active_paths
            if str(row.get("completion_tool") or "")
        }
        # Optional read-support is only exposed when the exact effect has
        # one contract-closed completion path and that path is currently direct.
        # If the registry already declares alternate closed paths (for example
        # eligibility -> draft), their own topology remains the sole frontier;
        # support_effects must not widen it.  This preserves the contract-v2
        # "highest-progress paths only" invariant while still permitting a
        # single direct action path to resolve a verified target through an
        # exact registered safe read.
        action_completion_pending = bool(
            not all_active_complete
            and len(closed_paths) == 1
            and len(active_paths) == 1
            and set(frontier) == active_completion_tools
            and any(
                (contract := capability_registry.contract_for_tool(name)) is not None
                and str(contract.execution_kind or "") == "action_draft"
                for name in active_completion_tools
            )
        )
        support_frontier: list[str] = []
        if action_completion_pending:
            for name in list(decision.get("support_tools") or []):
                tool_name = str(name or "")
                support_contract = capability_registry.contract_for_tool(tool_name)
                if (
                    tool_name
                    and tool_name not in completed_tools
                    and support_contract is not None
                    and str(support_contract.execution_kind or "")
                    in {"observation", "grounding_read", "knowledge_read", "clarification_read"}
                ):
                    support_frontier.append(tool_name)
        support_frontier = sorted(set(support_frontier))
        frontier = sorted(set(frontier) | set(support_frontier))
        allowed_tools.update(frontier)
        goal_policies.append({
            "goal_id": goal_id,
            "status": "PATH_COMPLETE_AWAITING_TERMINAL" if all_active_complete else "FRONTIER_READY",
            "enforcement": "contract_frontier",
            "depends_on_goal_ids": sorted(dependencies),
            "completed_tools": sorted(completed_tools),
            "reused_goal_output_ref_ids": reusable_output_ids,
            "goal_output_evidence_errors": output_evidence_errors,
            "allowed_tools": frontier,
            "support_frontier_tools": support_frontier,
            "support_frontier_is_completion": False,
            "active_path_ids": [str(row.get("path_id") or "") for row in active_paths],
            "candidate_path_count": len(closed_paths),
            "max_path_progress": max_progress,
            "reason": (
                "highest_progress_contract_paths_plus_exact_action_support"
                if support_frontier
                else "highest_progress_contract_paths_only"
            ),
        })

    global_coverage, coverage_evidence_errors = _validated_global_coverage(
        plan, capability_registry=capability_registry
    )
    declared_shared = {
        str(row.get("tool_name") or ""): row
        for row in list(global_coverage.get("shared_capability_bindings") or [])
        if isinstance(row, dict) and str(row.get("tool_name") or "")
    }
    frontier_by_tool: dict[str, set[str]] = {}
    for row in goal_policies:
        if str(row.get("status") or "") not in {"FRONTIER_READY", "PATH_COMPLETE_AWAITING_TERMINAL"}:
            continue
        goal_id = str(row.get("goal_id") or "")
        for tool_name in list(row.get("allowed_tools") or []):
            frontier_by_tool.setdefault(str(tool_name), set()).add(goal_id)
    formal_goal_by_id = {
        str(row.get("goal_id") or ""): row
        for row in semantic_goals(state)
        if str(row.get("goal_id") or "")
    }
    shared_frontier_bindings: list[dict[str, Any]] = []
    for tool_name, goal_ids in sorted(frontier_by_tool.items()):
        if len(goal_ids) <= 1:
            continue
        target_compatibility = prove_goal_target_compatibility(
            formal_goal_by_id[goal_id]
            for goal_id in sorted(goal_ids)
            if goal_id in formal_goal_by_id
        )
        if target_compatibility.get("status") != "SAME":
            continue
        declared = declared_shared.get(tool_name) if isinstance(declared_shared.get(tool_name), dict) else {}
        declared_goal_ids = {
            str(value) for value in list(declared.get("goal_ids") or []) if str(value)
        }
        proof_by_goal = (
            declared.get("coverage_proofs")
            if isinstance(declared.get("coverage_proofs"), dict)
            else {}
        )
        if not goal_ids.issubset(declared_goal_ids):
            continue
        selected_proofs = {
            goal_id: deepcopy(proof_by_goal[goal_id])
            for goal_id in sorted(goal_ids)
            if isinstance(proof_by_goal.get(goal_id), dict)
        }
        if set(selected_proofs) != goal_ids:
            continue
        shared_frontier_bindings.append({
            "tool_name": tool_name,
            "goal_ids": sorted(goal_ids),
            "coverage_id": str(declared.get("coverage_id") or ""),
            "coverage_proofs": selected_proofs,
            "target_compatibility": target_compatibility,
            "binding_rule": "single_call_requires_exact_match_proof_for_every_goal_and_compatible_target",
        })

    dependency_authority_shadow = _typed_dependency_authority_shadow(
        plan=plan,
        global_coverage=global_coverage,
        completed_goal_ids=completed_goal_ids,
    )

    dependency_authority_attestation = (
        build_dependency_authority_attestation(
            dependency_shadow=dependency_authority_shadow,
            semantic_contract_id=str(plan.get("formal_semantic_contract_id") or ""),
            semantic_digest=str(plan.get("formal_semantic_digest") or ""),
            capability_registry_version=capability_registry.version,
            completed_goal_ids=completed_goal_ids,
        )
        if dependency_authority_shadow is not None
        else None
    )

    if evidence_errors:
        # Treat invalid prior progress as zero progress.  The goal policies above
        # were already compiled from contract topology with an empty progress
        # set, so keeping their frontier fails closed instead of widening back to
        # the complete exact capability surface.
        mode = "EVIDENCE_INVALID_ZERO_PROGRESS"
    elif coverage_evidence_errors:
        mode = "COVERAGE_INVALID_NO_SHARED_BINDING"
    elif migration_gap_goal_ids:
        mode = "MIXED_ENFORCEMENT"
    else:
        mode = "ENFORCED"

    payload: dict[str, Any] = {
        "version": PRETOOL_EXECUTION_POLICY_VERSION,
        "authority": "provider_tool_surface_only_not_execution_permit",
        "mode": mode,
        "formal_semantic_contract_id": plan.get("formal_semantic_contract_id"),
        "formal_semantic_digest": plan.get("formal_semantic_digest"),
        "source_shadow_plan_digest": plan.get("plan_digest"),
        "capability_registry_version": capability_registry.version,
        "allowed_capability_tools": sorted(allowed_tools),
        "goal_policies": goal_policies,
        "completed_goal_ids": sorted(completed_goal_ids),
        "migration_gap_goal_ids": sorted(set(migration_gap_goal_ids)),
        "dependency_blocked_goal_ids": sorted(set(dependency_blocked_goal_ids)),
        "runtime_evidence_errors": evidence_errors,
        "coverage_evidence_errors": coverage_evidence_errors,
        "global_coverage_digest": global_coverage.get("coverage_digest"),
        "selected_global_coverage_id": (
            (global_coverage.get("selected_coverage") or {}).get("coverage_id")
            if isinstance(global_coverage.get("selected_coverage"), dict)
            else None
        ),
        "shared_frontier_bindings": shared_frontier_bindings,
        "creates_permit": False,
        "dispatches_tools": False,
        "mutates_semantics": False,
        "mutates_business_state": False,
        "downstream_authorities": [
            "goal_effect_match_proof",
            "capability_gate",
            "execution_permit",
            "target_resolver",
            "transaction_authority",
            "business_service",
        ],
    }
    if dependency_authority_shadow is not None:
        payload["typed_dependency_authority_shadow"] = dependency_authority_shadow
    if dependency_authority_attestation is not None:
        payload["typed_dependency_authority_attestation"] = dependency_authority_attestation
    payload["policy_digest"] = _digest(payload)
    return payload


def execution_policy_prompt_projection(policy: dict[str, Any] | None) -> dict[str, Any]:
    row = policy if isinstance(policy, dict) else {}
    return {
        "version": row.get("version"),
        "mode": row.get("mode"),
        "allowed_capability_tools": list(row.get("allowed_capability_tools") or []),
        "shared_frontier_bindings": list(row.get("shared_frontier_bindings") or []),
        "goals": [
            {
                "goal_id": item.get("goal_id"),
                "status": item.get("status"),
                "allowed_tools": list(item.get("allowed_tools") or []),
                "missing_dependency_goal_ids": list(item.get("missing_dependency_goal_ids") or []),
            }
            for item in list(row.get("goal_policies") or [])
            if isinstance(item, dict)
        ],
        "rule": (
            "只能调用 allowed_capability_tools 中的业务能力；该边界只限制候选能力，"
            "参数、对象、权限、Permit、事务和业务事实仍由后续 Runtime 验证。shared_frontier_bindings 只表示一个 Tool 可候选绑定多个 Goal；每个 Goal 仍需独立 MatchProof 和完成证明。"
        ),
    }


__all__ = [
    "PRETOOL_EXECUTION_POLICY_VERSION",
    "TYPED_DEPENDENCY_AUTHORITY_SHADOW_VERSION",
    "build_pretool_execution_policy",
    "execution_policy_prompt_projection",
]
