from __future__ import annotations

"""Goal-aware Workflow planning and verification for the Agent Loop.

The model must declare all current-turn goals before selecting business tools.
The Runtime then maps candidate effects to those goals and blocks finalization
when any required goal has no coverage.  Goal declarations remain orchestration
evidence only: Business Service, CapabilityGate and the transaction protocol
retain their existing authority boundaries.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from agent_core.lifecycle.protocol import TERMINAL_TOOL_NAMES
from agent_core.kernel.semantic_contract import (
    GOAL_INPUT_BINDING_AUTHORITY,
    goal_dependency_ids,
)
from agent_core.kernel.plan_projection_contract import (
    derive_plan_runtime_view,
    read_plan_projection,
    resolve_plan_projection,
)
from agent_core.lifecycle.semantic_contract import semantic_contract_integrity, semantic_goals
from agent_core.lifecycle.plan_execution import (
    complete_step_attempt,
    create_plan_run,
    freeze_plan_definition,
    project_grounded_execution_plan,
    revise_plan_run,
    validate_frozen_plan_definition,
    validate_plan_run,
)
from agent_core.runtime.capability_effects import canonical_effect_identity
from agent_core.lifecycle.candidate_repair import is_candidate_repairable_result
from agent_core.context.visible_result_refs import validate_visible_result_ref
from agent_core.ledger import find_handle, scope_for_state
from agent_core.lifecycle.workflow_contracts import (
    AgentStep,
    AgentTask,
    FailureType,
    GoalCoverageStatus,
    PlanLevel,
    StepKind,
    StepStatus,
    WorkflowGoal,
    WorkflowPlan,
    WorkflowStatus,
)


def _external_effects(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in list(plan.get("effects") or []) if isinstance(row, dict)]


def _call_count_by_kind(effects: list[dict[str, Any]], kind: str) -> int:
    return sum(1 for row in effects if str(row.get("execution_kind") or "") == kind)


def _has_collection_target(calls: list[dict[str, Any]], effects: list[dict[str, Any]] | None = None) -> bool:
    """Detect a collection write target from candidate arguments or MatchProof."""
    action_effect_ids = {
        str(effect.get("effect_id") or "")
        for effect in effects or []
        if str(effect.get("execution_kind") or "") == "action_draft" and str(effect.get("effect_id") or "")
    }
    for call in calls:
        if action_effect_ids and str(call.get("_effect_id") or "") not in action_effect_ids:
            continue
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        target = args.get("target") if isinstance(args.get("target"), dict) else {}
        mode = str(target.get("mode") or "")
        if mode == "collection":
            return True
        if mode == "set_operation":
            operator = str(target.get("operator") or "")
            if operator == "ordinal":
                continue
            try:
                limit = int(target.get("limit") or 0)
            except (TypeError, ValueError):
                limit = 0
            if operator == "take" and limit == 1:
                continue
            return True
        if mode == "pipeline":
            steps = [row for row in list(target.get("steps") or []) if isinstance(row, dict)]
            last = steps[-1] if steps else {}
            if str(last.get("op") or "") == "ordinal":
                continue
            if str(last.get("op") or "") == "take":
                try:
                    if int(last.get("limit") or 0) == 1:
                        continue
                except (TypeError, ValueError):
                    pass
            return True
        handles = target.get("handles") or target.get("order_ids") or target.get("target_handles")
        if isinstance(handles, list) and len(handles) > 1:
            return True
    for effect in effects or []:
        if str(effect.get("execution_kind") or "") != "action_draft":
            continue
        hint = str(effect.get("target_cardinality_hint") or "")
        if hint == "collection":
            return True
        if hint == "single":
            continue
        proof = effect.get("match_proof") if isinstance(effect.get("match_proof"), dict) else {}
        visible = proof.get("visible_result_reference") if isinstance(proof.get("visible_result_reference"), dict) else {}
        checks = visible.get("checks") if isinstance(visible.get("checks"), list) else []
        if any(isinstance(check, dict) and str(check.get("expected_shape") or "") == "collection" for check in checks):
            return True
    return False


def _goal_type_for_effect(effect: dict[str, Any]) -> str:
    kind = str(effect.get("execution_kind") or "")
    if kind == "action_draft":
        return "action"
    if kind == "unsupported":
        return "unsupported"
    return "query"


def _verified_target_member_count(effect: dict[str, Any]) -> int | None:
    """Read the concrete target population already proved by CapabilityGate.

    A model may correctly carry a one-member visible collection into a
    single-target capability (for example, one prior order into an eligibility
    lookup).  Some conclusive capabilities return a decision object rather
    than a result list, so their payload has no ``count`` field.  Treating the
    mere ``collection`` transport shape as plural then leaves a successfully
    verified goal permanently uncovered.  The MatchProof is the authoritative
    place to recover that cardinality; no label or user text is reinterpreted.
    """
    proof = effect.get("match_proof") if isinstance(effect.get("match_proof"), dict) else {}
    visible = (
        proof.get("visible_result_reference")
        if isinstance(proof.get("visible_result_reference"), dict)
        else {}
    )
    checks = visible.get("checks") if isinstance(visible.get("checks"), list) else []
    handles: list[str] = []
    saw_validated_collection = False
    for check in checks:
        if not isinstance(check, dict) or not bool(check.get("valid")):
            continue
        validated = check.get("validated_ref") if isinstance(check.get("validated_ref"), dict) else {}
        members = validated.get("member_handles")
        if not isinstance(members, list):
            continue
        saw_validated_collection = True
        handles.extend(str(value) for value in members if str(value))
    if not saw_validated_collection:
        return None
    return len(tuple(dict.fromkeys(handles)))


def _goal_rows(*, state: dict[str, Any], user_text: str, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del user_text, effects
    rows = semantic_goals(state)
    output: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        compatibility = current.get("compatibility") if isinstance(current.get("compatibility"), dict) else {}
        current["goal_type"] = str(compatibility.get("legacy_goal_type") or "open")
        current["expected_tools"] = []
        current["semantic_source"] = "frozen_semantic_contract"
        if isinstance(current.get("input_bindings"), list):
            current["depends_on"] = goal_dependency_ids(current)
        output.append(current)
    return output


def classify_plan_level(*, goal_rows: list[dict[str, Any]], plan: dict[str, Any]) -> tuple[str, list[str]]:
    """Classify from structured goals/effects, never by language keywords."""
    effects = _external_effects(plan)
    calls = list(plan.get("tool_calls") or [])
    action_count = _call_count_by_kind(effects, "action_draft")
    required_goals = [row for row in goal_rows if bool(row.get("required", True))]
    required_goal_ids = {
        str(row.get("goal_id") or "")
        for row in required_goals
        if str(row.get("goal_id") or "")
    }
    action_goal_ids = {
        str(goal_id)
        for effect in effects
        if str(effect.get("execution_kind") or "") == "action_draft"
        for goal_id in list(effect.get("goal_ids") or [])
        if str(goal_id) in required_goal_ids
    }
    reasons: list[str] = []
    if len(action_goal_ids) > 1 or action_count > 1:
        reasons.append("multiple_action_goals")
    if action_count >= 1 and _has_collection_target(calls, effects):
        reasons.append("action_on_collection_or_set")
    if len(required_goals) >= 3 or len(effects) >= 3:
        reasons.append("three_or_more_required_work_items")
    if reasons:
        return PlanLevel.WORKFLOW.value, reasons
    if len(required_goals) > 1 or len(effects) > 1 or (action_count == 1 and len(effects) > action_count):
        return PlanLevel.LIGHTWEIGHT_PLAN.value, ["multiple_declared_goals_or_effects"]
    return PlanLevel.DIRECT.value, ["single_declared_goal"]


def _effect_to_step(
    effect: dict[str, Any],
    index: int,
    goal_rows: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, Any]:
    execution_kind = str(effect.get("execution_kind") or "unknown")
    if execution_kind == "unsupported":
        kind = StepKind.UNSUPPORTED
        verification = {
            "must_cross": ["CapabilityGate", "RuntimeOutcome"],
            "completion_owner": "unsupported_boundary",
            "business_write_allowed": False,
            "must_not_substitute_similar_capability": True,
        }
    elif execution_kind == "action_draft":
        kind = StepKind.ACTION_DRAFT
        verification = {
            "must_cross": ["CapabilityGate", "Draft", "ActionGateway"],
            "completion_owner": "transaction_runtime",
            "business_write_allowed": False,
        }
    elif execution_kind in {"observation", "grounding_read", "knowledge_read", "clarification_read", "session_correction"}:
        kind = StepKind.OBSERVATION
        verification = {
            "must_cross": ["CapabilityGate", "RuntimeOutcome"],
            "completion_owner": "tool_observation",
            "business_write_allowed": False,
        }
    else:
        kind = StepKind.UNKNOWN
        verification = {
            "must_cross": ["CapabilityGate"],
            "completion_owner": "runtime",
            "business_write_allowed": False,
        }

    tool_name = str(effect.get("tool_name") or "")
    declared_by_id = {
        str(row.get("goal_id") or ""): row
        for row in goal_rows
        if str(row.get("goal_id") or "")
    }
    requested_goal_ids = tuple(dict.fromkeys(
        str(value) for value in list(effect.get("goal_ids") or []) if str(value)
    ))
    goal_ids = requested_goal_ids if requested_goal_ids and all(value in declared_by_id for value in requested_goal_ids) else ()
    completion_types = tuple(dict.fromkeys(
        str(value) for value in list(effect.get("goal_completion_types") or []) if str(value)
    ))
    completion_effect_identities = tuple(dict.fromkeys(
        str(value) for value in list(effect.get("completion_effect_identities") or []) if str(value)
    ))
    support_effect_identities = tuple(dict.fromkeys(
        str(value) for value in list(effect.get("support_effect_identities") or []) if str(value)
    ))
    effect_cardinality = str(effect.get("target_cardinality_hint") or "unknown")
    verified_target_count = _verified_target_member_count(effect)
    surface = state.get("capability_surface") if isinstance(state.get("capability_surface"), dict) else {}
    surface_by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }

    # A safe read can be an exact registered support effect for another still
    # pending Goal without semantically depending on that Goal or completing it.
    # This metadata only permits a bounded continuation; the later completion
    # call must still pass its own target, capability and transaction gates.
    support_continuation_by_goal: dict[str, dict[str, Any]] = {}
    support_safe_execution_kinds = {
        "observation",
        "grounding_read",
        "knowledge_read",
        "clarification_read",
    }
    if execution_kind in support_safe_execution_kinds:
        for continuation_goal_id, continuation_goal in declared_by_id.items():
            identity = canonical_effect_identity(continuation_goal.get("requested_effect"))
            surface_goal = surface_by_goal.get(continuation_goal_id, {})
            completion_tools = sorted({
                str(value)
                for value in list(surface_goal.get("completion_tools") or [])
                if str(value)
            })
            support_tools = {
                str(value)
                for value in list(surface_goal.get("support_tools") or [])
                if str(value)
            }
            eligible = bool(
                identity
                and identity in support_effect_identities
                and str(surface_goal.get("status") or "") == "exact_supported"
                and tool_name in support_tools
                and completion_tools
            )
            if eligible:
                support_continuation_by_goal[continuation_goal_id] = {
                    "requested_effect_identity": identity,
                    "support_tool": tool_name,
                    "completion_tools": completion_tools,
                    "source": "exact_registered_support_effect",
                    "safe_read_only": True,
                    "completes_goal": False,
                    "target_authority_granted": False,
                    "continuation_required": True,
                }

    per_goal: dict[str, dict[str, Any]] = {}
    for goal_id in goal_ids:
        mapped_goal = declared_by_id[goal_id]
        mapped_goal_type = str(mapped_goal.get("goal_type") or "")
        identity = canonical_effect_identity(mapped_goal.get("requested_effect"))
        expected_cardinality = str(mapped_goal.get("expected_result_cardinality") or "unknown")
        cardinality_eligible = not (
            (expected_cardinality == "single" and effect_cardinality == "collection" and verified_target_count != 1)
            or (expected_cardinality == "collection" and effect_cardinality == "single")
            or expected_cardinality == "none"
        )
        formal_effect_present = bool(identity and not identity.startswith("legacy."))
        exact_completion = bool(identity and identity in completion_effect_identities)
        exact_support = bool(identity and identity in support_effect_identities)
        surface_goal = surface_by_goal.get(goal_id, {})
        unsupported_completion = bool(
            execution_kind == "unsupported"
            and str(surface_goal.get("status") or "") in {"absent_proven", "completion_capability_absent"}
            and tool_name in {str(value) for value in list(surface_goal.get("candidate_tools") or [])}
        )
        legacy_type_eligible = bool(
            not formal_effect_present and mapped_goal_type and mapped_goal_type in completion_types
        )
        completion_identity_eligible = bool(exact_completion or unsupported_completion or legacy_type_eligible)
        role = (
            "completion" if exact_completion
            else "support" if exact_support
            else "unsupported_report" if unsupported_completion
            else "legacy_completion" if legacy_type_eligible
            else "none"
        )
        per_goal[goal_id] = {
            "mapped_goal_type": mapped_goal_type or None,
            "mapped_requested_effect_identity": identity or None,
            "expected_result_cardinality": expected_cardinality,
            "effect_result_cardinality_hint": effect_cardinality,
            "verified_target_member_count": verified_target_count,
            "goal_type_completion_eligible": legacy_type_eligible,
            "formal_effect_completion_eligible": completion_identity_eligible,
            "formal_effect_support_eligible": exact_support,
            "goal_effect_role": role,
            "goal_cardinality_eligible": cardinality_eligible,
            "goal_completion_eligible": bool(completion_identity_eligible and cardinality_eligible),
        }

    roles = {goal_id: row["goal_effect_role"] for goal_id, row in per_goal.items()}
    completion_by_goal = {goal_id: bool(row["goal_completion_eligible"]) for goal_id, row in per_goal.items()}
    verification.update({
        "goal_mapping_required": True,
        "goal_mapping_complete": bool(goal_ids),
        "requested_goal_ids": list(requested_goal_ids),
        "goal_completion_types": list(completion_types),
        "completion_effect_identities": list(completion_effect_identities),
        "support_effect_identities": list(support_effect_identities),
        "per_goal": per_goal,
        "goal_effect_roles": roles,
        "goal_completion_eligible_by_goal": completion_by_goal,
        "goal_effect_role": next(iter(set(roles.values()))) if roles and len(set(roles.values())) == 1 else "mixed" if roles else "none",
        "goal_completion_eligible": bool(completion_by_goal) and all(completion_by_goal.values()),
        "support_continuation_goal_ids": sorted(support_continuation_by_goal),
        "support_continuation_by_goal": support_continuation_by_goal,
        "composite_goal_binding": len(goal_ids) > 1,
    })
    if len(goal_ids) == 1:
        only = per_goal[goal_ids[0]]
        for key in (
            "mapped_goal_type", "mapped_requested_effect_identity",
            "expected_result_cardinality", "effect_result_cardinality_hint",
            "verified_target_member_count", "goal_type_completion_eligible",
            "formal_effect_completion_eligible", "formal_effect_support_eligible",
            "goal_cardinality_eligible",
        ):
            verification[key] = deepcopy(only.get(key))
    if not requested_goal_ids:
        verification["goal_mapping_error"] = "at_least_one_goal_id_required"
    elif len(set(requested_goal_ids)) != len(requested_goal_ids):
        verification["goal_mapping_error"] = "duplicate_goal_id"
    else:
        unknown = [value for value in requested_goal_ids if value not in declared_by_id]
        if unknown:
            verification["goal_mapping_error"] = "unknown_goal_id"
            verification["unknown_goal_ids"] = unknown

    return AgentStep(
        step_id=f"step:{index}",
        effect_id=str(effect.get("effect_id") or "") or None,
        kind=kind,
        tool_name=tool_name,
        capability_id=str(effect.get("candidate_capability") or "") or None,
        goal_ids=goal_ids,
        depends_on=tuple(str(item) for item in list(effect.get("depends_on") or []) if str(item)),
        verification=verification,
    ).as_dict()


_REUSABLE_EVIDENCE_GOAL_TYPES = {"query", "consult"}
_REUSABLE_EVIDENCE_KINDS = {"artifact", "view", "result", "eligibility", "receipt"}


def _historical_evidence_satisfaction(
    *, state: dict[str, Any], goal: dict[str, Any], calls: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return a scope/freshness proof for already customer-visible evidence.

    This never satisfies action goals and never chooses an alternative handle.
    Relevance remains guarded by the model's explicit goal/evidence binding and
    the independent answer-release alignment verifier.
    """
    goal_id = str(goal.get("goal_id") or "")
    if str(goal.get("goal_type") or "") not in _REUSABLE_EVIDENCE_GOAL_TYPES:
        return None
    for call in calls:
        if str(call.get("name") or "") != "respond_to_user":
            continue
        bound = {str(value) for value in list(call.get("_goal_ids") or []) if str(value)}
        if goal_id not in bound:
            continue
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        handles = [str(value) for value in list(args.get("evidence_handles") or []) if str(value)]
        if not handles:
            continue
        rows: list[dict[str, Any]] = []
        visible_refs: list[dict[str, Any]] = []
        for handle in handles:
            entry = find_handle(
                state.get("artifact_ledger") or [],
                handle,
                scope=scope_for_state(state),
                allowed_kinds=_REUSABLE_EVIDENCE_KINDS,
                active_only=True,
            )
            visible_ref, visible_error = validate_visible_result_ref(
                state=state,
                result_ref=handle,
            )
            if entry is None or visible_error is not None or visible_ref is None:
                rows = []
                visible_refs = []
                break
            rows.append(entry)
            visible_refs.append(visible_ref)
        if len(rows) != len(handles):
            continue
        return {
            "kind": "historical_visible_evidence",
            "evidence_handles": handles,
            "scope_bound": True,
            "active_only": True,
            "customer_visible": True,
            "source_turns": [int(ref.get("source_turn") or 0) for ref in visible_refs],
            "member_provenance": [
                {
                    "evidence_handle": handle,
                    "source_collection_ref": ref.get("source_collection_ref"),
                    "presentation_origin": ref.get("presentation_origin"),
                }
                for handle, ref in zip(handles, visible_refs)
            ],
            "release_alignment_required": True,
        }
    return None


def _completed_goal_ids_from_state(state: dict[str, Any]) -> set[str]:
    """Return only durable, lifecycle-authoritative completed Goal ids."""

    return {
        str(row.get("goal_id") or "")
        for row in list(state.get("goal_records") or [])
        if isinstance(row, dict)
        and str(row.get("lifecycle") or "").upper() == "COMPLETED"
        and str(row.get("goal_id") or "")
    }


def _build_goals(
    state: dict[str, Any], goal_rows: list[dict[str, Any]], steps: list[dict[str, Any]], calls: list[dict[str, Any]]
) -> tuple[WorkflowGoal, ...]:
    completed_goal_ids = _completed_goal_ids_from_state(state)
    terminal_bindings = {
        str(call.get("name") or ""): {str(value) for value in list(call.get("_goal_ids") or []) if str(value)}
        for call in calls
        if str(call.get("name") or "") in TERMINAL_TOOL_NAMES
    }
    goals: list[WorkflowGoal] = []
    for row in goal_rows:
        goal_id = str(row.get("goal_id") or "")
        expected = tuple(str(name) for name in list(row.get("expected_tools") or []) if str(name))
        covered_steps = tuple(
            str(step.get("step_id") or "")
            for step in steps
            if goal_id in {str(value) for value in list(step.get("goal_ids") or [])}
            and bool(
                ((step.get("verification") or {}).get("goal_completion_eligible_by_goal") or {}).get(goal_id)
                if isinstance((step.get("verification") or {}).get("goal_completion_eligible_by_goal"), dict)
                else (step.get("verification") or {}).get("goal_completion_eligible")
            )
        )
        satisfaction_proof = _historical_evidence_satisfaction(state=state, goal=row, calls=calls)
        clarification_bound = goal_id in terminal_bindings.get("ask_user_clarification", set())
        declared_dependencies = tuple(
            str(value) for value in list(row.get("depends_on") or []) if str(value)
        )
        missing_dependencies = tuple(
            value for value in declared_dependencies if value not in completed_goal_ids
        )
        durable_completed = goal_id in completed_goal_ids
        # A terminal response closes narrative/clarification goals. Query and
        # consult goals may also close when the exact bound evidence has a
        # current active, scoped, customer-visible proof. Actions never do.
        covered_terminal = tuple(
            name for name, bound in terminal_bindings.items()
            if goal_id in bound and (
                str(row.get("goal_type") or "") in {"narrative", "clarification"}
                or name == "ask_user_clarification"
                or (name == "respond_to_user" and satisfaction_proof is not None)
            )
        )
        goal_type = str(row.get("goal_type") or "")
        status = (
            GoalCoverageStatus.COVERED
            if durable_completed
            else GoalCoverageStatus.BLOCKED
            if clarification_bound and goal_type != "clarification"
            else GoalCoverageStatus.COVERED
            if covered_terminal
            else GoalCoverageStatus.BLOCKED
            if missing_dependencies and not covered_steps
            else GoalCoverageStatus.PENDING
        )
        if durable_completed:
            satisfaction_proof = {
                "kind": "durable_goal_lifecycle_completed",
                "goal_id": goal_id,
                "scope_bound": True,
            }
        elif clarification_bound and goal_type != "clarification":
            satisfaction_proof = {
                "kind": "clarification_pause",
                "terminal_tool": "ask_user_clarification",
                "goal_id": goal_id,
                "goal_remains_incomplete": True,
            }
        elif missing_dependencies and not covered_steps:
            satisfaction_proof = {
                "kind": "declared_goal_dependency_pause",
                "goal_id": goal_id,
                "missing_dependency_goal_ids": list(missing_dependencies),
                "goal_remains_incomplete": True,
            }
        goals.append(WorkflowGoal(
            goal_id=goal_id,
            description=str(row.get("description") or ""),
            goal_type=goal_type,
            evidence_span=str(row.get("evidence_span") or ""),
            requested_effect=deepcopy(row.get("requested_effect")) if isinstance(row.get("requested_effect"), dict) else None,
            expected_tools=expected,
            expected_result_cardinality=str(
                row.get("expected_result_cardinality") or "unknown"
            ),
            depends_on=tuple(str(value) for value in list(row.get("depends_on") or []) if str(value)),
            required=bool(row.get("required", True)),
            coverage_status=status,
            covered_by_step_ids=covered_steps,
            covered_by_terminal_tools=covered_terminal,
            satisfaction_proof=satisfaction_proof,
        ))
    return tuple(goals)


def _tasks_for_goals(goals: tuple[WorkflowGoal, ...], steps: tuple[AgentStep, ...]) -> tuple[AgentTask, ...]:
    tasks: list[AgentTask] = []
    goal_to_task: dict[str, str] = {}
    for index, goal in enumerate(goals, start=1):
        task_id = f"task:{index}"
        goal_to_task[goal.goal_id] = task_id
        owned = tuple(step.step_id for step in steps if goal.goal_id in step.goal_ids)
        tasks.append(AgentTask(
            task_id=task_id,
            title=goal.description or f"处理目标 {goal.goal_id}",
            step_ids=owned,
            goal_id=goal.goal_id,
            status=WorkflowStatus.PLANNED,
        ))
    # Any effect that was not mapped to a declared goal is retained as an
    # explicit orphan task so final verification can fail closed and show why.
    orphan_steps = [step for step in steps if not step.goal_ids]
    for step in orphan_steps:
        tasks.append(AgentTask(
            task_id=f"task:orphan:{step.step_id}",
            title=f"未映射目标的候选能力：{step.tool_name}",
            step_ids=(step.step_id,),
            goal_id=None,
            status=WorkflowStatus.PLANNED,
        ))
    output: list[AgentTask] = []
    by_goal = {goal.goal_id: goal for goal in goals}
    task_by_step_id = {
        step_id: task.task_id
        for task in tasks
        for step_id in task.step_ids
    }
    task_by_effect_id = {
        str(step.effect_id or ""): task_by_step_id.get(step.step_id, "")
        for step in steps
        if str(step.effect_id or "") and task_by_step_id.get(step.step_id)
    }
    step_by_id = {step.step_id: step for step in steps}
    for task in tasks:
        deps: list[str] = []
        if task.goal_id and task.goal_id in by_goal:
            deps.extend(
                goal_to_task[dep]
                for dep in by_goal[task.goal_id].depends_on
                if dep in goal_to_task
            )
        # Execution planning may add a data dependency even when two user Goals
        # are independently stated (for example, query an order before preparing
        # a refund).  The task projection must preserve that dependency instead
        # of showing the dependent task as runnable in parallel.  This is a
        # projection only; it never invents a semantic Goal dependency.
        for step_id in task.step_ids:
            step = step_by_id.get(step_id)
            if step is None:
                continue
            deps.extend(
                dependency_task
                for effect_id in step.depends_on
                if (dependency_task := task_by_effect_id.get(str(effect_id)))
                and dependency_task != task.task_id
            )
        output.append(AgentTask(
            task_id=task.task_id,
            title=task.title,
            step_ids=task.step_ids,
            goal_id=task.goal_id,
            status=task.status,
            depends_on=tuple(dict.fromkeys(deps)),
        ))
    return tuple(output)



GROUNDED_EXECUTION_PLAN_VERSION = "grounded-execution-plan@2"
PLAN_VALIDATION_VERSION = "grounded-plan-validation@1"


def _plan_structure_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable planning structure, excluding runtime progress."""
    return {
        "plan_contract_version": str(plan.get("plan_contract_version") or ""),
        "turn_plan_id": str(plan.get("turn_plan_id") or ""),
        "formal_semantic_contract_id": str(plan.get("formal_semantic_contract_id") or ""),
        "formal_semantic_digest": str(plan.get("formal_semantic_digest") or ""),
        "goals": [
            {
                "goal_id": str(row.get("goal_id") or ""),
                "required": bool(row.get("required", True)),
                "depends_on": [str(value) for value in list(row.get("depends_on") or []) if str(value)],
            }
            for row in list(plan.get("goals") or [])
            if isinstance(row, dict)
        ],
        "steps": [
            {
                "step_id": str(row.get("step_id") or ""),
                "effect_id": str(row.get("effect_id") or ""),
                "tool_name": str(row.get("tool_name") or ""),
                "capability_id": str(row.get("capability_id") or ""),
                "goal_ids": [str(value) for value in list(row.get("goal_ids") or []) if str(value)],
                "depends_on": [str(value) for value in list(row.get("depends_on") or []) if str(value)],
                "required": bool(row.get("required", True)),
                "goal_effect_role": str(
                    (row.get("verification") or {}).get("goal_effect_role")
                    if isinstance(row.get("verification"), dict)
                    else ""
                ),
                "goal_effect_roles": deepcopy(
                    (row.get("verification") or {}).get("goal_effect_roles")
                    if isinstance((row.get("verification") or {}).get("goal_effect_roles"), dict)
                    else {}
                ),
                "support_continuation_goal_ids": [
                    str(value)
                    for value in list(
                        (row.get("verification") or {}).get("support_continuation_goal_ids") or []
                    )
                    if str(value)
                ],
            }
            for row in list(plan.get("steps") or [])
            if isinstance(row, dict)
        ],
    }


def _structure_digest(plan: dict[str, Any]) -> str:
    payload = json.dumps(
        _plan_structure_payload(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _dependency_cycle(effect_dependencies: dict[str, list[str]]) -> list[str]:
    """Return one deterministic effect cycle, or an empty list."""
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(effect_id: str) -> list[str]:
        if effect_id in visited:
            return []
        if effect_id in visiting:
            try:
                start = stack.index(effect_id)
            except ValueError:
                start = 0
            return [*stack[start:], effect_id]
        visiting.add(effect_id)
        stack.append(effect_id)
        for dependency_id in effect_dependencies.get(effect_id, []):
            cycle = visit(dependency_id)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(effect_id)
        visited.add(effect_id)
        return []

    for effect_id in sorted(effect_dependencies):
        cycle = visit(effect_id)
        if cycle:
            return cycle
    return []


def validate_grounded_execution_plan(
    *,
    plan: dict[str, Any],
    semantic_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate structural closure without reinterpreting user language.

    This validator compares frozen identifiers, references and exact capability
    roles only. It may reject a proposed plan, but it never creates goals,
    chooses a replacement capability or rewrites requested effects.
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    semantic = semantic_contract if isinstance(semantic_contract, dict) else {}

    if semantic:
        integrity = semantic_contract_integrity(semantic)
        if not integrity.get("ok"):
            errors.append({
                "code": str(integrity.get("code") or "SEMANTIC_CONTRACT_DIGEST_INVALID"),
                "details": integrity,
            })
        expected_id = str(semantic.get("semantic_contract_id") or "")
        expected_digest = str(semantic.get("semantic_digest") or "")
        if str(plan.get("formal_semantic_contract_id") or "") != expected_id:
            errors.append({"code": "PLAN_SEMANTIC_CONTRACT_ID_MISMATCH"})
        if str(plan.get("formal_semantic_digest") or "") != expected_digest:
            errors.append({"code": "PLAN_SEMANTIC_DIGEST_MISMATCH"})
    else:
        errors.append({"code": "PLAN_SEMANTIC_CONTRACT_REQUIRED"})

    goals = [row for row in list(plan.get("goals") or []) if isinstance(row, dict)]
    goal_ids = [str(row.get("goal_id") or "") for row in goals]
    known_goal_ids = {value for value in goal_ids if value}
    if any(not value for value in goal_ids):
        errors.append({"code": "PLAN_GOAL_ID_REQUIRED"})
    if len(goal_ids) != len(set(goal_ids)):
        errors.append({"code": "PLAN_DUPLICATE_GOAL_ID"})
    for row in goals:
        goal_id = str(row.get("goal_id") or "")
        unknown = [
            str(value)
            for value in list(row.get("depends_on") or [])
            if str(value) and str(value) not in known_goal_ids
        ]
        if unknown:
            errors.append(
                {"code": "PLAN_UNKNOWN_GOAL_DEPENDENCY", "goal_id": goal_id, "dependencies": unknown}
            )
    if semantic.get("dependency_authority") == GOAL_INPUT_BINDING_AUTHORITY:
        expected_dependencies = {
            str(row.get("goal_id") or ""): goal_dependency_ids(row)
            for row in semantic_goals(semantic)
            if str(row.get("goal_id") or "")
        }
        actual_dependencies = {
            str(row.get("goal_id") or ""): [
                str(value)
                for value in list(row.get("depends_on") or [])
                if str(value)
            ]
            for row in goals
            if str(row.get("goal_id") or "")
        }
        for goal_id, expected in expected_dependencies.items():
            if actual_dependencies.get(goal_id, []) != expected:
                errors.append({
                    "code": "PLAN_TYPED_DEPENDENCY_PROJECTION_MISMATCH",
                    "goal_id": goal_id,
                    "expected_dependencies": expected,
                    "actual_dependencies": actual_dependencies.get(goal_id, []),
                })

    steps = [row for row in list(plan.get("steps") or []) if isinstance(row, dict)]
    step_ids = [str(row.get("step_id") or "") for row in steps]
    effect_ids = [str(row.get("effect_id") or "") for row in steps]
    if any(not value for value in step_ids):
        errors.append({"code": "PLAN_STEP_ID_REQUIRED"})
    if len(step_ids) != len(set(step_ids)):
        errors.append({"code": "PLAN_DUPLICATE_STEP_ID"})
    if any(not value for value in effect_ids):
        errors.append({"code": "PLAN_EFFECT_ID_REQUIRED"})
    if len(effect_ids) != len(set(effect_ids)):
        errors.append({"code": "PLAN_DUPLICATE_EFFECT_ID"})

    known_effect_ids = {value for value in effect_ids if value}
    dependency_graph: dict[str, list[str]] = {}
    for row in steps:
        effect_id = str(row.get("effect_id") or "")
        dependencies = [str(value) for value in list(row.get("depends_on") or []) if str(value)]
        dependency_graph[effect_id] = dependencies
        unknown_dependencies = [value for value in dependencies if value not in known_effect_ids]
        if unknown_dependencies:
            errors.append(
                {
                    "code": "PLAN_UNKNOWN_EFFECT_DEPENDENCY",
                    "effect_id": effect_id,
                    "dependencies": unknown_dependencies,
                }
            )
        if effect_id and effect_id in dependencies:
            errors.append({"code": "PLAN_SELF_DEPENDENCY", "effect_id": effect_id})

        bound_goal_ids = [str(value) for value in list(row.get("goal_ids") or []) if str(value)]
        if not bound_goal_ids:
            errors.append(
                {
                    "code": "PLAN_EXACT_GOAL_BINDING_REQUIRED",
                    "effect_id": effect_id,
                    "goal_ids": bound_goal_ids,
                }
            )
            errors.append(
                {
                    "code": "PLAN_EXACT_CAPABILITY_ROLE_REQUIRED",
                    "effect_id": effect_id,
                    "goal_id": None,
                    "role": None,
                }
            )
        elif len(bound_goal_ids) != len(set(bound_goal_ids)):
            errors.append({"code": "PLAN_DUPLICATE_BOUND_GOAL", "effect_id": effect_id})
        unknown_bound = [goal_id for goal_id in bound_goal_ids if goal_id not in known_goal_ids]
        if unknown_bound:
            errors.append(
                {
                    "code": "PLAN_UNKNOWN_BOUND_GOAL",
                    "effect_id": effect_id,
                    "goal_ids": unknown_bound,
                }
            )

        verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
        roles = verification.get("goal_effect_roles") if isinstance(verification.get("goal_effect_roles"), dict) else {}
        legacy_role = str(verification.get("goal_effect_role") or "")
        allowed_roles = {"completion", "support", "unsupported_report", "legacy_completion"}
        for goal_id in bound_goal_ids:
            role = str(roles.get(goal_id) or legacy_role)
            if role not in allowed_roles:
                errors.append(
                    {
                        "code": "PLAN_EXACT_CAPABILITY_ROLE_REQUIRED",
                        "effect_id": effect_id,
                        "goal_id": goal_id,
                        "role": role or None,
                    }
                )
        continuation_goal_ids = [
            str(value)
            for value in list(verification.get("support_continuation_goal_ids") or [])
            if str(value)
        ]
        unknown_continuations = [
            goal_id for goal_id in continuation_goal_ids if goal_id not in known_goal_ids
        ]
        if unknown_continuations:
            errors.append(
                {
                    "code": "PLAN_UNKNOWN_SUPPORT_CONTINUATION_GOAL",
                    "effect_id": effect_id,
                    "goal_ids": unknown_continuations,
                }
            )

    cycle = _dependency_cycle(
        {
            effect_id: [dependency for dependency in dependencies if dependency in known_effect_ids]
            for effect_id, dependencies in dependency_graph.items()
            if effect_id
        }
    )
    if cycle:
        errors.append({"code": "PLAN_DEPENDENCY_CYCLE", "cycle": cycle})

    def _step_role_for_goal(step: dict[str, Any], goal_id: str) -> str:
        verification = step.get("verification") if isinstance(step.get("verification"), dict) else {}
        roles = verification.get("goal_effect_roles") if isinstance(verification.get("goal_effect_roles"), dict) else {}
        return str(roles.get(goal_id) or verification.get("goal_effect_role") or "")

    def _has_current_completion(goal_id: str) -> bool:
        allowed_completion_roles = {"completion", "unsupported_report"} | (
            {"legacy_completion"} if not semantic else set()
        )
        return any(
            goal_id in {str(value) for value in list(step.get("goal_ids") or []) if str(value)}
            and _step_role_for_goal(step, goal_id) in allowed_completion_roles
            for step in steps
        )

    def _has_exact_support_continuation(goal_id: str) -> bool:
        return any(
            goal_id in {
                str(value)
                for value in list(
                    ((step.get("verification") or {}).get("support_continuation_goal_ids") or [])
                    if isinstance(step.get("verification"), dict)
                    else []
                )
                if str(value)
            }
            for step in steps
        )

    pending_required_goal_ids = [
        str(row.get("goal_id") or "")
        for row in goals
        if bool(row.get("required", True))
        and str(row.get("coverage_status") or "") == GoalCoverageStatus.PENDING.value
        and str(row.get("goal_id") or "")
    ]
    support_continuation_goal_ids = [
        goal_id
        for goal_id in pending_required_goal_ids
        if not _has_current_completion(goal_id)
        and _has_exact_support_continuation(goal_id)
    ]
    uncovered_required_goals = [
        goal_id
        for goal_id in pending_required_goal_ids
        if not _has_current_completion(goal_id)
        and goal_id not in set(support_continuation_goal_ids)
    ]
    if support_continuation_goal_ids:
        warnings.append(
            {
                "code": "PLAN_REQUIRED_GOAL_DEFERRED_BY_EXACT_SUPPORT",
                "goal_ids": support_continuation_goal_ids,
                "goal_remains_incomplete": True,
                "completion_required_on_continuation": True,
                "target_authority_granted": False,
            }
        )
    if uncovered_required_goals:
        errors.append(
            {
                "code": "PLAN_REQUIRED_GOAL_HAS_NO_COMPLETION_PATH",
                "goal_ids": uncovered_required_goals,
            }
        )

    digest = _structure_digest(plan)
    status = "ACCEPTED" if not errors else "REJECTED"
    return {
        "version": PLAN_VALIDATION_VERSION,
        "status": status,
        "dispatch_allowed": status == "ACCEPTED",
        "semantic_binding_verified": bool(semantic) and not any(
            row["code"] in {
                "PLAN_SEMANTIC_CONTRACT_ID_MISMATCH",
                "PLAN_SEMANTIC_DIGEST_MISMATCH",
            }
            for row in errors
        ),
        "structure_digest": digest,
        "errors": errors,
        "warnings": warnings,
    }

def build_workflow_plan(*, state: dict[str, Any], turn_plan: dict[str, Any], user_text: str) -> dict[str, Any]:
    effects = _external_effects(turn_plan)
    calls = list(turn_plan.get("tool_calls") or [])
    goal_rows = _goal_rows(state=state, user_text=user_text, effects=effects)
    level, reasons = classify_plan_level(goal_rows=goal_rows, plan=turn_plan)
    plan_id = str(turn_plan.get("plan_id") or f"turn-plan:{uuid4().hex}")
    step_dicts = tuple(
        _effect_to_step(effect, index + 1, goal_rows, state)
        for index, effect in enumerate(effects)
    )
    goal_dependencies = {
        str(goal.get("goal_id") or ""): [str(value) for value in list(goal.get("depends_on") or []) if str(value)]
        for goal in goal_rows
        if str(goal.get("goal_id") or "")
    }
    effect_ids_by_goal: dict[str, list[str]] = {}
    for step in step_dicts:
        for goal_id in list(step.get("goal_ids") or []):
            effect_ids_by_goal.setdefault(str(goal_id), []).append(str(step.get("effect_id") or ""))
    enriched_steps: list[dict[str, Any]] = []
    for step in step_dicts:
        dependency_ids = [str(value) for value in list(step.get("depends_on") or []) if str(value)]
        for goal_id in list(step.get("goal_ids") or []):
            for dependency_goal_id in goal_dependencies.get(str(goal_id), []):
                dependency_ids.extend(effect_ids_by_goal.get(dependency_goal_id, []))
        enriched_steps.append({**step, "depends_on": list(dict.fromkeys(dependency_ids))})
    step_dicts = tuple(enriched_steps)
    step_rows = tuple(AgentStep(
        step_id=str(step["step_id"]),
        effect_id=step.get("effect_id"),
        kind=StepKind(str(step.get("kind") or StepKind.UNKNOWN.value)),
        tool_name=str(step.get("tool_name") or ""),
        capability_id=step.get("capability_id"),
        goal_ids=tuple(str(item) for item in step.get("goal_ids") or []),
        depends_on=tuple(str(item) for item in step.get("depends_on") or []),
        status=StepStatus(str(step.get("status") or StepStatus.PLANNED.value)),
        required=bool(step.get("required", True)),
        verification=dict(step.get("verification") or {}),
    ) for step in step_dicts)
    goals = _build_goals(state, goal_rows, [step.as_dict() for step in step_rows], calls)
    tasks = _tasks_for_goals(goals, step_rows)
    required_pending = any(goal.required and goal.coverage_status == GoalCoverageStatus.PENDING for goal in goals)
    clarification_pause = any(
        goal.coverage_status in {GoalCoverageStatus.COVERED, GoalCoverageStatus.BLOCKED}
        and "ask_user_clarification" in set(goal.covered_by_terminal_tools)
        for goal in goals
    )
    if clarification_pause:
        status = WorkflowStatus.NEEDS_INPUT
    elif not goals and not effects:
        status = WorkflowStatus.NOT_REQUIRED
    elif required_pending or effects:
        status = WorkflowStatus.RUNNING if level == PlanLevel.DIRECT.value else WorkflowStatus.PLANNED
    else:
        status = WorkflowStatus.SUCCEEDED
    next_plan = WorkflowPlan(
        workflow_id=f"workflow:{uuid4().hex}",
        turn_plan_id=plan_id,
        level=PlanLevel(level),
        status=status,
        goal=user_text,
        goals=goals,
        tasks=tasks,
        steps=step_rows,
        created_turn=int(state.get("turn_index") or 0),
        updated_turn=int(state.get("turn_index") or 0),
        reasons=tuple(reasons),
    ).as_dict()
    semantic_contract = state.get("frozen_semantic_contract") if isinstance(state.get("frozen_semantic_contract"), dict) else {}
    next_plan.update({
        "plan_contract_version": GROUNDED_EXECUTION_PLAN_VERSION,
        "authority": "validated_execution_plan_not_semantic_or_business_fact",
        "formal_semantic_contract_id": semantic_contract.get("semantic_contract_id"),
        "formal_semantic_digest": semantic_contract.get("semantic_digest"),
        "goal_source": "frozen_semantic_contract" if semantic_contract else "missing_frozen_semantic_contract",
    })
    previous = read_plan_projection(state)
    next_plan = _carry_forward_workflow_runtime(next_plan, previous)
    validation = validate_grounded_execution_plan(
        plan=next_plan,
        semantic_contract=semantic_contract or None,
    )
    next_plan["validation"] = validation
    next_plan["plan_digest"] = validation["structure_digest"]
    next_plan["immutable_structure"] = True
    return next_plan


def materialize_plan_runtime(
    *,
    state: dict[str, Any],
    workflow_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Freeze structural plan authority and keep progress in a separate run.

    During the migration, ``grounded_execution_plan`` remains a derived
    compatibility projection for existing consumers.  It is never the owner of
    either immutable plan structure or execution progress.
    """
    previous_definition = (
        state.get("frozen_plan_definition")
        if isinstance(state.get("frozen_plan_definition"), dict)
        else None
    )
    previous_id = (
        str(previous_definition.get("plan_definition_id") or "")
        if isinstance(previous_definition, dict)
        else ""
    )
    candidate_definition = freeze_plan_definition(
        workflow_plan,
        plan_definition_id=previous_id or None,
    )
    use_previous = False
    if previous_definition is not None:
        check = validate_frozen_plan_definition(previous_definition)
        use_previous = bool(
            check.get("ok")
            and str(previous_definition.get("definition_digest") or "")
            == str(candidate_definition.get("definition_digest") or "")
        )
    definition = deepcopy(previous_definition) if use_previous else candidate_definition
    previous_run = state.get("plan_run") if isinstance(state.get("plan_run"), dict) else None
    if use_previous:
        run = create_plan_run(
            definition,
            turn_index=int(state.get("turn_index") or 0),
            previous_run=previous_run,
        )
    elif (
        isinstance(previous_definition, dict)
        and isinstance(previous_run, dict)
        and str(previous_definition.get("turn_plan_id") or "") == str(definition.get("turn_plan_id") or "")
        and str(previous_definition.get("formal_semantic_contract_id") or "")
        == str(definition.get("formal_semantic_contract_id") or "")
    ):
        try:
            run = revise_plan_run(
                previous_definition=previous_definition,
                previous_run=previous_run,
                definition=definition,
                turn_index=int(state.get("turn_index") or 0),
            )
        except ValueError:
            run = create_plan_run(definition, turn_index=int(state.get("turn_index") or 0))
    else:
        run = create_plan_run(definition, turn_index=int(state.get("turn_index") or 0))
    projection = project_plan_runtime(definition=definition, plan_run=run)
    return definition, run, projection


def project_plan_runtime(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
) -> dict[str, Any]:
    """Return the single Kernel-owned definition/run projection."""
    return project_grounded_execution_plan(
        definition=definition,
        plan_run=plan_run,
    )


def validate_plan_runtime_dispatch(
    *,
    definition: dict[str, Any] | None,
    plan_run: dict[str, Any] | None,
    effect_id: str,
    semantic_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed if plan structure or run binding changed before dispatch."""
    definition_check = validate_frozen_plan_definition(definition)
    if not definition_check.get("ok"):
        return {
            "ok": False,
            "code": str(definition_check.get("code") or "FROZEN_PLAN_DEFINITION_INVALID"),
            "message": "冻结计划定义不完整或已变化，本次工具调用未执行。",
            "data": {"integrity": definition_check},
        }
    assert isinstance(definition, dict)
    run_check = validate_plan_run(definition=definition, plan_run=plan_run)
    if not run_check.get("ok"):
        return {
            "ok": False,
            "code": str(run_check.get("code") or "PLAN_RUN_INVALID"),
            "message": "执行轨迹与冻结计划不一致，本次工具调用未执行。",
            "data": {"integrity": run_check},
        }
    assert isinstance(plan_run, dict)
    if isinstance(semantic_contract, dict):
        semantic_check = semantic_contract_integrity(semantic_contract)
        if not semantic_check.get("ok"):
            return {
                "ok": False,
                "code": str(semantic_check.get("code") or "SEMANTIC_CONTRACT_DIGEST_INVALID"),
                "message": "正式语义合同已变化，本次工具调用未执行。",
                "data": {"integrity": semantic_check},
            }
        if (
            str(definition.get("formal_semantic_contract_id") or "")
            != str(semantic_contract.get("semantic_contract_id") or "")
            or str(definition.get("formal_semantic_digest") or "")
            != str(semantic_contract.get("semantic_digest") or "")
        ):
            return {
                "ok": False,
                "code": "PLAN_DEFINITION_SEMANTIC_BINDING_MISMATCH",
                "message": "冻结计划与正式语义合同不一致，本次工具调用未执行。",
            }
    projection = project_plan_runtime(definition=definition, plan_run=plan_run)
    return validate_step_dispatch(
        workflow_plan=projection,
        effect_id=effect_id,
        semantic_contract=semantic_contract,
    )


def validate_step_dispatch(
    *,
    workflow_plan: dict[str, Any] | None,
    effect_id: str,
    semantic_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed before dispatch when goal ownership or dependencies are invalid."""
    if not isinstance(workflow_plan, dict):
        return {"ok": False, "code": "WORKFLOW_PLAN_REQUIRED", "message": "缺少可验证的工作流计划，本次工具调用未执行。"}
    steps = [row for row in list(workflow_plan.get("steps") or []) if isinstance(row, dict)]
    step = next((row for row in steps if str(row.get("effect_id") or "") == effect_id), None)
    if step is None:
        return {"ok": False, "code": "WORKFLOW_STEP_REQUIRED", "message": "候选效果没有对应的工作流步骤，本次工具调用未执行。"}
    goal_ids = [str(value) for value in list(step.get("goal_ids") or []) if str(value)]
    if not goal_ids or len(goal_ids) != len(set(goal_ids)):
        verification = step.get("verification") if isinstance(step.get("verification"), dict) else {}
        requested = [str(value) for value in list(verification.get("requested_goal_ids") or []) if str(value)]
        code = "WORKFLOW_GOAL_BINDING_INVALID" if requested else "WORKFLOW_GOAL_BINDING_REQUIRED"
        return {
            "ok": False,
            "code": code,
            "message": "业务工具调用必须绑定至少一个且不重复的已声明 Goal；多 Goal 绑定仍需逐 Goal 精确能力证明。",
            "data": {"effect_id": effect_id, "requested_goal_ids": requested},
        }
    if str(workflow_plan.get("plan_contract_version") or "").startswith("grounded-execution-plan@"):
        validation = validate_grounded_execution_plan(
            plan=workflow_plan,
            semantic_contract=semantic_contract,
        )
        recorded_digest = str(workflow_plan.get("plan_digest") or "")
        if validation["status"] != "ACCEPTED":
            return {
                "ok": False,
                "code": "GROUNDED_PLAN_VALIDATION_FAILED",
                "message": "执行计划没有通过确定性结构校验，本次工具调用未执行。",
                "data": {"validation": validation},
            }
        if not recorded_digest or recorded_digest != validation["structure_digest"]:
            return {
                "ok": False,
                "code": "GROUNDED_PLAN_DIGEST_MISMATCH",
                "message": "执行计划结构已发生未授权变化，本次工具调用未执行。",
                "data": {
                    "recorded_plan_digest": recorded_digest or None,
                    "actual_plan_digest": validation["structure_digest"],
                },
            }
    by_effect = {str(row.get("effect_id") or ""): row for row in steps if str(row.get("effect_id") or "")}
    unmet: list[dict[str, str]] = []
    for dependency_id in list(step.get("depends_on") or []):
        dependency = by_effect.get(str(dependency_id))
        status = str((dependency or {}).get("status") or "missing")
        if status not in {StepStatus.SUCCEEDED.value, StepStatus.SKIPPED.value}:
            unmet.append({"effect_id": str(dependency_id), "status": status})
    if unmet:
        return {
            "ok": False,
            "code": "WORKFLOW_DEPENDENCY_UNSATISFIED",
            "message": "前置步骤尚未成功完成，本次工具调用未执行。",
            "data": {"effect_id": effect_id, "unmet_dependencies": unmet},
        }
    return {"ok": True, "code": "WORKFLOW_STEP_DISPATCHABLE", "message": "工作流步骤可执行。"}


def _carry_forward_workflow_runtime(next_plan: dict[str, Any], previous: Any) -> dict[str, Any]:
    """Preserve runtime-owned Step outcomes across model/tool iterations."""
    if not isinstance(previous, dict):
        return next_plan
    if str(previous.get("turn_plan_id") or "") != str(next_plan.get("turn_plan_id") or ""):
        return next_plan
    prior_steps = {
        str(step.get("effect_id") or ""): step
        for step in list(previous.get("steps") or [])
        if isinstance(step, dict) and str(step.get("effect_id") or "")
    }
    carried_steps: list[dict[str, Any]] = []
    for step in list(next_plan.get("steps") or []):
        row = dict(step) if isinstance(step, dict) else {}
        prior = prior_steps.get(str(row.get("effect_id") or ""))
        if prior is not None:
            for key in ("status", "result_summary", "failure_type", "failure_reason"):
                row[key] = prior.get(key)
            row["verification"] = {**dict(row.get("verification") or {}), **dict(prior.get("verification") or {})}
        carried_steps.append(row)
    next_plan["workflow_id"] = str(previous.get("workflow_id") or next_plan.get("workflow_id") or "")
    next_plan["created_turn"] = int(previous.get("created_turn") or next_plan.get("created_turn") or 0)
    runtime_view = derive_plan_runtime_view(
        goals=list(next_plan.get("goals") or []),
        tasks=list(next_plan.get("tasks") or []),
        steps=carried_steps,
    )
    next_plan["steps"] = runtime_view["steps"]
    next_plan["goals"] = runtime_view["goals"]
    next_plan["goal_coverage_complete"] = runtime_view["goal_coverage_complete"]
    next_plan["status"] = runtime_view["status"]
    next_plan["tasks"] = runtime_view["tasks"]
    return next_plan



def _failure_type_from_result(result: dict[str, Any]) -> str:
    code = str(result.get("code") or "")
    outcome = result.get("runtime_outcome") if isinstance(result.get("runtime_outcome"), dict) else {}
    next_interaction = str(outcome.get("next_interaction") or "")
    if code in {"TRANSACTION_CONTEXT_UNAVAILABLE", "TRANSACTION_REPOSITORY_UNAVAILABLE"}:
        return FailureType.ENVIRONMENT_UNAVAILABLE.value
    if code in {"UNKNOWN_OR_UNSUPPORTED_TOOL", "UNSUPPORTED_CAPABILITY", "CAPABILITY_UNAVAILABLE"}:
        return FailureType.CAPABILITY_UNAVAILABLE.value
    if code in {
        "CAPABILITY_EXACT_MATCH_REQUIRED",
        "CAPABILITY_PARAMETERIZATION_INCOMPLETE",
        "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET",
    }:
        return FailureType.CAPABILITY_EXACT_MATCH_REQUIRED.value
    if code in {"CONTEXT_TARGET_NOT_UNIQUE", "CONTEXT_TARGET_NOT_VERIFIED_FOR_WRITE", "NEED_TRANSACTION_SELECTION"} or next_interaction == "need_selection":
        return FailureType.TARGET_AMBIGUOUS.value
    if code == "UNSUPPORTED_TARGET_CARDINALITY":
        return FailureType.UNSUPPORTED_CARDINALITY.value
    if next_interaction in {"open_form", "open_authority"}:
        return FailureType.REQUIRES_HUMAN_INPUT.value
    if code in {"SUBMISSION_UNKNOWN", "RECONCILIATION_REQUIRED"}:
        # This is neither a business failure nor an infrastructure retry. The
        # same idempotency key must be reconciled before another submission.
        return FailureType.SUBMISSION_UNKNOWN.value
    return FailureType.UNKNOWN.value


def _step_status_from_result(result: dict[str, Any]) -> str:
    outcome = result.get("runtime_outcome") if isinstance(result.get("runtime_outcome"), dict) else {}
    next_interaction = str(outcome.get("next_interaction") or "")
    effects = str(outcome.get("effects") or "")
    code = str(result.get("code") or "")
    if code == "SUBMISSION_UNKNOWN" or effects == "unknown":
        return StepStatus.SUBMISSION_UNKNOWN.value
    if next_interaction == "open_form" or effects == "input_required":
        return StepStatus.NEEDS_INPUT.value
    if next_interaction == "open_authority" or effects in {"authority_required", "draft_created"}:
        return StepStatus.AWAITING_AUTHORIZATION.value
    if bool(result.get("ok")):
        return StepStatus.SUCCEEDED.value
    if is_candidate_repairable_result(result):
        return StepStatus.FAILED_RETRYABLE.value
    failure_type = _failure_type_from_result(result)
    if failure_type in {FailureType.ENVIRONMENT_UNAVAILABLE.value, FailureType.TRANSIENT_NETWORK.value, FailureType.RATE_LIMIT.value}:
        return StepStatus.FAILED_RETRYABLE.value
    return StepStatus.FAILED_FINAL.value


def _verified_result_member_count(result: dict[str, Any]) -> int | None:
    """Read a capability-owned result population without inferring semantics."""
    if not bool(result.get("ok")):
        return None
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    count = data.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    for key in ("orders", "items", "rows", "members"):
        values = data.get(key)
        if isinstance(values, list):
            return len(values)
    if isinstance(data.get("order"), dict):
        return 1
    return None



def _derive_step_result_update_from_step(
    *,
    step: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Derive runtime-owned step fields without mutating a plan projection."""
    status = _step_status_from_result(result)
    failure_type = (
        FailureType.NONE.value
        if status in {
            StepStatus.SUCCEEDED.value,
            StepStatus.AWAITING_AUTHORIZATION.value,
            StepStatus.NEEDS_INPUT.value,
        }
        else _failure_type_from_result(result)
    )
    verification = dict(step.get("verification") or {})
    verification["last_result_code"] = str(result.get("code") or "") or None
    verification["runtime_outcome_type"] = (
        str((result.get("runtime_outcome") or {}).get("outcome_type") or "")
        if isinstance(result.get("runtime_outcome"), dict)
        else None
    )
    verification["verified_by_runtime"] = True
    verified_count = _verified_result_member_count(result)
    verification["verified_result_member_count"] = verified_count
    per_goal = verification.get("per_goal") if isinstance(verification.get("per_goal"), dict) else {}
    if per_goal:
        updated_per_goal: dict[str, dict[str, Any]] = {}
        completion_by_goal: dict[str, bool] = {}
        cardinality_by_goal: dict[str, bool] = {}
        for goal_id, raw in per_goal.items():
            row = dict(raw) if isinstance(raw, dict) else {}
            verified_target_count = row.get("verified_target_member_count")
            expected_cardinality = str(row.get("expected_result_cardinality") or "unknown")
            effect_cardinality = str(row.get("effect_result_cardinality_hint") or "unknown")
            cardinality_eligible = bool(
                expected_cardinality not in {"none"}
                and not (expected_cardinality == "collection" and effect_cardinality == "single")
                and not (
                    expected_cardinality == "single"
                    and effect_cardinality == "collection"
                    and verified_count != 1
                    and verified_target_count != 1
                )
            )
            completion_identity = bool(
                row.get("formal_effect_completion_eligible",
                    row.get("goal_type_completion_eligible", row.get("goal_completion_eligible")))
            )
            row["verified_result_member_count"] = verified_count
            row["goal_cardinality_eligible"] = cardinality_eligible
            row["goal_completion_eligible"] = bool(completion_identity and cardinality_eligible)
            updated_per_goal[str(goal_id)] = row
            cardinality_by_goal[str(goal_id)] = cardinality_eligible
            completion_by_goal[str(goal_id)] = bool(row["goal_completion_eligible"])
        verification["per_goal"] = updated_per_goal
        verification["goal_cardinality_eligible_by_goal"] = cardinality_by_goal
        verification["goal_completion_eligible_by_goal"] = completion_by_goal
        verification["goal_cardinality_eligible"] = bool(cardinality_by_goal) and all(cardinality_by_goal.values())
        verification["goal_completion_eligible"] = bool(completion_by_goal) and all(completion_by_goal.values())
    else:
        verified_target_count = verification.get("verified_target_member_count")
        expected_cardinality = str(verification.get("expected_result_cardinality") or "unknown")
        effect_cardinality = str(verification.get("effect_result_cardinality_hint") or "unknown")
        cardinality_eligible = bool(
            expected_cardinality not in {"none"}
            and not (expected_cardinality == "collection" and effect_cardinality == "single")
            and not (
                expected_cardinality == "single"
                and effect_cardinality == "collection"
                and verified_count != 1
                and verified_target_count != 1
            )
        )
        verification["goal_cardinality_eligible"] = cardinality_eligible
        verification["goal_completion_eligible"] = bool(
            verification.get(
                "formal_effect_completion_eligible",
                verification.get(
                    "goal_type_completion_eligible",
                    verification.get("goal_completion_eligible"),
                ),
            )
            and cardinality_eligible
        )
    return {
        "status": status,
        "result_summary": str(result.get("message") or "")[:500] or None,
        "failure_type": failure_type,
        "failure_reason": (
            None
            if failure_type == FailureType.NONE.value
            else str(result.get("message") or result.get("code") or "")
        ),
        "verification": verification,
    }


def derive_plan_run_step_update(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
    effect_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Derive the atomic PlanRun update from its two authorities and a result.

    ``grounded_execution_plan`` is deliberately absent from the inputs.  The
    returned related-step patches persist candidate-repair supersession in the
    same PlanRun write rather than losing it when the view is reprojected.
    """
    check = validate_plan_run(definition=definition, plan_run=plan_run)
    if not check.get("ok"):
        raise ValueError(str(check.get("code") or "PLAN_RUN_INVALID"))
    structural = next(
        (
            row
            for row in list(definition.get("steps") or [])
            if isinstance(row, dict)
            and str(row.get("effect_id") or "") == str(effect_id or "")
        ),
        None,
    )
    if structural is None:
        raise ValueError("PLAN_STEP_NOT_FOUND")
    current_state = dict((plan_run.get("step_states") or {}).get(effect_id) or {})
    step = {
        **deepcopy(structural),
        "status": str(current_state.get("status") or "PLANNED"),
        "verification": {
            **dict(structural.get("verification") or {}),
            **dict(current_state.get("verification") or {}),
        },
    }
    update = _derive_step_result_update_from_step(step=step, result=result)

    related_step_updates: dict[str, dict[str, Any]] = {}
    if str(update.get("status") or "") == StepStatus.SUCCEEDED.value:
        updated_goal_ids = {
            str(value) for value in list(structural.get("goal_ids") or []) if str(value)
        }
        updated_completion_types = {
            str(value)
            for value in list(
                (structural.get("verification") or {}).get("goal_completion_types") or []
            )
            if str(value)
        }
        step_states = (
            plan_run.get("step_states")
            if isinstance(plan_run.get("step_states"), dict)
            else {}
        )
        for prior in list(definition.get("steps") or []):
            if not isinstance(prior, dict):
                continue
            prior_effect_id = str(prior.get("effect_id") or "")
            if not prior_effect_id or prior_effect_id == effect_id:
                continue
            prior_state = dict(step_states.get(prior_effect_id) or {})
            prior_verification = {
                **dict(prior.get("verification") or {}),
                **dict(prior_state.get("verification") or {}),
            }
            prior_goals = {
                str(value) for value in list(prior.get("goal_ids") or []) if str(value)
            }
            prior_completion_types = {
                str(value)
                for value in list(prior_verification.get("goal_completion_types") or [])
                if str(value)
            }
            repairable_prior = is_candidate_repairable_result({
                "ok": False,
                "code": prior_verification.get("last_result_code"),
            })
            if (
                str(prior_state.get("status") or "") == StepStatus.FAILED_RETRYABLE.value
                and prior_goals == updated_goal_ids
                and prior_completion_types == updated_completion_types
                and repairable_prior
            ):
                related_step_updates[prior_effect_id] = {
                    "status": StepStatus.SKIPPED.value,
                    "failure_type": FailureType.NONE.value,
                    "failure_reason": None,
                    "verification": {
                        "superseded_by_effect_id": effect_id,
                        "candidate_repaired": True,
                    },
                }
    update["related_step_updates"] = related_step_updates
    return update


def complete_plan_run_step_result(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
    attempt_id: str,
    effect_id: str,
    result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Complete one attempt through the PlanRun-owned write boundary."""
    update = derive_plan_run_step_update(
        definition=definition,
        plan_run=plan_run,
        effect_id=effect_id,
        result=result,
    )
    return complete_step_attempt(
        definition=definition,
        plan_run=plan_run,
        attempt_id=attempt_id,
        result=result,
        step_status=str(update.get("status") or StepStatus.FAILED_FINAL.value),
        failure_type=str(update.get("failure_type") or FailureType.UNKNOWN.value),
        result_summary=update.get("result_summary"),
        verification=(
            dict(update.get("verification") or {})
            if isinstance(update.get("verification"), dict)
            else {}
        ),
        related_step_updates=(
            dict(update.get("related_step_updates") or {})
            if isinstance(update.get("related_step_updates"), dict)
            else {}
        ),
    )



def verify_workflow_for_final_answer(state: dict[str, Any]) -> dict[str, Any]:
    """Return a fail-closed verification for terminalization.

    Final model prose is accepted only when all required workflow steps are in a
    terminal state, or when the workflow has intentionally paused for input,
    authorization, final failure or submission reconciliation.
    """
    resolution = resolve_plan_projection(state)
    if not resolution.get("ok"):
        return {
            "ok": False,
            "reason": "plan_projection_invalid",
            "code": str(resolution.get("code") or "PLAN_PROJECTION_INVALID"),
            "source": str(resolution.get("source") or ""),
        }
    plan = resolution.get("plan") if isinstance(resolution.get("plan"), dict) else None
    if not plan:
        return {"ok": True, "reason": "no_workflow_plan"}
    plan_validation = plan.get("validation") if isinstance(plan.get("validation"), dict) else {}
    if (
        str(resolution.get("source") or "") == "same_turn_validated_plan"
        and str(plan_validation.get("status") or "") != "ACCEPTED"
    ):
        return {
            "ok": False,
            "reason": "plan_validation_rejected",
            "code": str(resolution.get("code") or "EPHEMERAL_PLAN_REPAIR_VIEW"),
            "errors": list(plan_validation.get("errors") or []),
        }
    status = str(plan.get("status") or "")
    level = str(plan.get("level") or "")
    uncovered_goals = [
        goal for goal in list(plan.get("goals") or [])
        if isinstance(goal, dict)
        and bool(goal.get("required", True))
        and str(goal.get("coverage_status") or "") == GoalCoverageStatus.PENDING.value
    ]
    orphan_steps = [
        step for step in list(plan.get("steps") or [])
        if isinstance(step, dict) and not list(step.get("goal_ids") or [])
    ]
    clarification_pause = status == WorkflowStatus.NEEDS_INPUT.value and any(
        isinstance(goal, dict)
        and str(goal.get("coverage_status") or "") in {
            GoalCoverageStatus.COVERED.value,
            GoalCoverageStatus.BLOCKED.value,
        }
        and "ask_user_clarification" in {
            str(name) for name in list(goal.get("covered_by_terminal_tools") or [])
        }
        for goal in list(plan.get("goals") or [])
    )
    if clarification_pause and not orphan_steps:
        suspended = [
            goal for goal in list(plan.get("goals") or [])
            if isinstance(goal, dict)
            and bool(goal.get("required", True))
            and str(goal.get("coverage_status") or "") in {
                GoalCoverageStatus.PENDING.value,
                GoalCoverageStatus.BLOCKED.value,
            }
        ]
        return {
            "ok": True,
            "reason": "clarification_pause",
            "level": level,
            "suspended_goal_ids": [str(goal.get("goal_id") or "") for goal in suspended],
        }
    if uncovered_goals or orphan_steps:
        return {
            "ok": False,
            "reason": "goal_coverage_incomplete",
            "level": level,
            "uncovered_goal_ids": [str(goal.get("goal_id") or "") for goal in uncovered_goals],
            "unmapped_step_ids": [str(step.get("step_id") or "") for step in orphan_steps],
        }
    if status in {
        WorkflowStatus.NOT_REQUIRED.value,
        WorkflowStatus.SUCCEEDED.value,
        WorkflowStatus.NEEDS_INPUT.value,
        WorkflowStatus.AWAITING_AUTHORIZATION.value,
        WorkflowStatus.FAILED_FINAL.value,
        WorkflowStatus.SUBMISSION_UNKNOWN.value,
    }:
        return {"ok": True, "reason": status, "level": level}
    pending = [
        step for step in list(plan.get("steps") or [])
        if isinstance(step, dict) and bool(step.get("required", True)) and str(step.get("status") or "") not in {
            StepStatus.SUCCEEDED.value,
            StepStatus.NEEDS_INPUT.value,
            StepStatus.AWAITING_AUTHORIZATION.value,
            StepStatus.FAILED_FINAL.value,
            StepStatus.SUBMISSION_UNKNOWN.value,
            StepStatus.SKIPPED.value,
        }
    ]
    return {
        "ok": not pending,
        "reason": "required_steps_not_terminal" if pending else "all_steps_terminal",
        "level": level,
        "pending_step_ids": [str(step.get("step_id") or "") for step in pending],
    }
