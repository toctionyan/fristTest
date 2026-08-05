from __future__ import annotations

"""Neutral read contract for the derived plan compatibility projection.

The immutable ``frozen_plan_definition`` and mutable ``plan_run`` are the only
Plan authorities.  ``grounded_execution_plan`` is a cache/view for consumers.
This module keeps its projection and read rules in Kernel so Lifecycle, Runtime
and Observability do not create reverse package dependencies merely to inspect
that view.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

FROZEN_PLAN_DEFINITION_VERSION = "frozen-plan-definition@1"
PLAN_RUN_VERSION = "plan-run@1"
PLAN_PROJECTION_CACHE_VERSION = "plan-projection-cache@1"
PLAN_PROJECTION_AUTHORITY = "compatibility_projection_from_frozen_definition_and_plan_run"

_RUNTIME_GOAL_COVERAGE_PENDING = "PENDING"


def canonical_contract_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _definition_payload(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in definition.items()
        if key not in {"definition_digest", "immutable"}
    }


def validate_plan_authority_pair(
    *,
    definition: dict[str, Any] | None,
    plan_run: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(definition, dict):
        return {"ok": False, "code": "FROZEN_PLAN_DEFINITION_REQUIRED"}
    if str(definition.get("version") or "") != FROZEN_PLAN_DEFINITION_VERSION:
        return {"ok": False, "code": "FROZEN_PLAN_DEFINITION_VERSION_INVALID"}
    if not bool(definition.get("immutable")):
        return {"ok": False, "code": "FROZEN_PLAN_DEFINITION_NOT_IMMUTABLE"}
    recorded_definition_digest = str(definition.get("definition_digest") or "")
    actual_definition_digest = canonical_contract_digest(_definition_payload(definition))
    if not recorded_definition_digest or recorded_definition_digest != actual_definition_digest:
        return {
            "ok": False,
            "code": "FROZEN_PLAN_DEFINITION_DIGEST_INVALID",
            "recorded_digest": recorded_definition_digest or None,
            "actual_digest": actual_definition_digest,
        }
    effect_ids = [
        str(row.get("effect_id") or "")
        for row in list(definition.get("steps") or [])
        if isinstance(row, dict)
    ]
    if any(not value for value in effect_ids) or len(effect_ids) != len(set(effect_ids)):
        return {"ok": False, "code": "FROZEN_PLAN_EFFECT_IDS_INVALID"}

    if not isinstance(plan_run, dict):
        return {"ok": False, "code": "PLAN_RUN_REQUIRED"}
    if str(plan_run.get("version") or "") != PLAN_RUN_VERSION:
        return {"ok": False, "code": "PLAN_RUN_VERSION_INVALID"}
    if (
        str(plan_run.get("plan_definition_id") or "")
        != str(definition.get("plan_definition_id") or "")
        or str(plan_run.get("plan_definition_digest") or "")
        != recorded_definition_digest
    ):
        return {"ok": False, "code": "PLAN_RUN_DEFINITION_MISMATCH"}
    expected_effects = set(effect_ids)
    actual_effects = (
        set(plan_run.get("step_states") or {})
        if isinstance(plan_run.get("step_states"), dict)
        else set()
    )
    if expected_effects != actual_effects:
        return {
            "ok": False,
            "code": "PLAN_RUN_STEP_STATE_MISMATCH",
            "missing": sorted(expected_effects - actual_effects),
            "extra": sorted(actual_effects - expected_effects),
        }
    return {
        "ok": True,
        "code": "PLAN_AUTHORITY_PAIR_VALID",
        "definition_digest": actual_definition_digest,
        "plan_run_digest": canonical_contract_digest(plan_run),
    }


def _refresh_goal_coverage(
    goals: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive goal coverage while preserving staged dependency semantics.

    A provider call may legally contain only the current capability frontier.
    Required downstream goals therefore remain BLOCKED until their declared
    Goal dependencies are covered; they are not treated as malformed plans for
    lacking a completion step in the current invocation. Durable completed
    Goals remain covered across revised PlanDefinitions.
    """

    output: list[dict[str, Any]] = []
    for goal in goals:
        row = deepcopy(goal)
        goal_id = str(row.get("goal_id") or "")
        covered = [
            step
            for step in steps
            if goal_id in {str(value) for value in list(step.get("goal_ids") or [])}
            and bool((step.get("verification") or {}).get("goal_completion_eligible"))
        ]
        row["covered_by_step_ids"] = [
            str(step.get("step_id") or "")
            for step in covered
            if str(step.get("step_id") or "")
        ]
        terminal_tools = {
            str(name) for name in list(row.get("covered_by_terminal_tools") or [])
        }
        proof = row.get("satisfaction_proof") if isinstance(row.get("satisfaction_proof"), dict) else {}
        durable_completed = str(proof.get("kind") or "") == "durable_goal_lifecycle_completed"
        clarification_pause = (
            "ask_user_clarification" in terminal_tools
            and str(row.get("goal_type") or "") != "clarification"
        )
        if durable_completed:
            row["coverage_status"] = "COVERED"
        elif clarification_pause:
            row["coverage_status"] = "BLOCKED"
        elif not covered and not terminal_tools:
            row["coverage_status"] = _RUNTIME_GOAL_COVERAGE_PENDING
        elif any(str(step.get("status") or "") == "FAILED_FINAL" for step in covered):
            row["coverage_status"] = "FAILED"
        elif any(
            str(step.get("status") or "")
            in {"NEEDS_INPUT", "AWAITING_AUTHORIZATION", "SUBMISSION_UNKNOWN"}
            for step in covered
        ):
            row["coverage_status"] = "BLOCKED"
        elif covered and all(
            str(step.get("status") or "") in {"SUCCEEDED", "SKIPPED"}
            for step in covered
        ):
            row["coverage_status"] = "COVERED"
        elif terminal_tools:
            row["coverage_status"] = "COVERED"
        else:
            row["coverage_status"] = _RUNTIME_GOAL_COVERAGE_PENDING
        output.append(row)

    # Dependency blocking is derived from the same Goal graph and current
    # projection. Iterate to a fixed point so multi-hop chains remain blocked
    # until every upstream Goal is covered.
    by_id = {
        str(row.get("goal_id") or ""): row
        for row in output
        if str(row.get("goal_id") or "")
    }
    changed = True
    while changed:
        changed = False
        for row in output:
            if str(row.get("coverage_status") or "") != _RUNTIME_GOAL_COVERAGE_PENDING:
                continue
            missing = [
                str(value)
                for value in list(row.get("depends_on") or [])
                if str(value)
                and str((by_id.get(str(value)) or {}).get("coverage_status") or "") != "COVERED"
            ]
            if missing and not list(row.get("covered_by_step_ids") or []):
                row["coverage_status"] = "BLOCKED"
                proof = row.get("satisfaction_proof") if isinstance(row.get("satisfaction_proof"), dict) else {}
                if str(proof.get("kind") or "") != "clarification_pause":
                    row["satisfaction_proof"] = {
                        "kind": "declared_goal_dependency_pause",
                        "goal_id": str(row.get("goal_id") or ""),
                        "missing_dependency_goal_ids": missing,
                        "goal_remains_incomplete": True,
                    }
                changed = True
    return output


def _aggregate_status(
    steps: list[dict[str, Any]],
    *,
    goals: list[dict[str, Any]],
) -> str:
    pending_goals = [
        goal
        for goal in goals
        if bool(goal.get("required", True))
        and str(goal.get("coverage_status") or "") == _RUNTIME_GOAL_COVERAGE_PENDING
    ]
    clarification_pause = any(
        str(goal.get("coverage_status") or "") in {"COVERED", "BLOCKED"}
        and "ask_user_clarification"
        in {str(name) for name in list(goal.get("covered_by_terminal_tools") or [])}
        for goal in goals
    )
    if pending_goals and clarification_pause:
        return "NEEDS_INPUT"

    required_steps = [step for step in steps if bool(step.get("required", True))]
    statuses = [str(step.get("status") or "PLANNED") for step in required_steps]
    pending_goal_ids = {
        str(goal.get("goal_id") or "")
        for goal in pending_goals
        if str(goal.get("goal_id") or "")
    }
    active_step_goal_ids = {
        str(goal_id)
        for step in required_steps
        if str(step.get("status") or "PLANNED") in {"PLANNED", "RUNNING"}
        for goal_id in list(step.get("goal_ids") or [])
        if str(goal_id)
    }
    unfinished_goal_ids = pending_goal_ids | active_step_goal_ids
    # A pause on the same unfinished Goal is the actionable workflow state even
    # if another structural step for that Goal remains PLANNED. A separate
    # unfinished Goal keeps a multi-intent workflow RUNNING instead of allowing
    # one paused branch to hide unrelated work.
    for paused_status in (
        "SUBMISSION_UNKNOWN",
        "NEEDS_INPUT",
        "AWAITING_AUTHORIZATION",
    ):
        paused_goal_ids = {
            str(goal_id)
            for step in required_steps
            if str(step.get("status") or "PLANNED") == paused_status
            for goal_id in list(step.get("goal_ids") or [])
            if str(goal_id)
        }
        if (
            paused_goal_ids
            and unfinished_goal_ids
            and unfinished_goal_ids.issubset(paused_goal_ids)
        ):
            return paused_status
    if pending_goals:
        return "RUNNING"
    if not statuses:
        return "NOT_REQUIRED"
    if any(status in {"PLANNED", "RUNNING"} for status in statuses):
        return "RUNNING"
    for status in (
        "SUBMISSION_UNKNOWN",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "NEEDS_INPUT",
        "AWAITING_AUTHORIZATION",
    ):
        if status in statuses:
            return status
    if all(status in {"SUCCEEDED", "SKIPPED"} for status in statuses):
        return "SUCCEEDED"
    return "RUNNING"


def _sync_task_statuses(
    tasks: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(step.get("step_id") or ""): step for step in steps}
    output: list[dict[str, Any]] = []
    for task in tasks:
        row = deepcopy(task)
        owned = [
            by_id[str(step_id)]
            for step_id in list(row.get("step_ids") or [])
            if str(step_id) in by_id
        ]
        row["status"] = (
            "PLANNED"
            if not owned and row.get("goal_id")
            else _aggregate_status(owned, goals=[])
        )
        output.append(row)
    return output


def derive_plan_runtime_view(
    *,
    goals: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive the complete Goal/Task/Workflow compatibility runtime view.

    The function is intentionally neutral: callers supply already-grounded
    structural rows plus runtime-owned Step evidence.  Both the authoritative
    Definition/Run projector and the same-turn compatibility plan use this one
    derivation so status semantics cannot drift between packages.
    """
    derived_steps = [deepcopy(step) for step in steps if isinstance(step, dict)]
    derived_goals = _refresh_goal_coverage(
        [deepcopy(goal) for goal in goals if isinstance(goal, dict)],
        derived_steps,
    )
    derived_tasks = _sync_task_statuses(
        [deepcopy(task) for task in tasks if isinstance(task, dict)],
        derived_steps,
    )
    return {
        "goals": derived_goals,
        "tasks": derived_tasks,
        "steps": derived_steps,
        "status": _aggregate_status(derived_steps, goals=derived_goals),
        "goal_coverage_complete": not any(
            bool(goal.get("required", True))
            and str(goal.get("coverage_status") or "")
            == _RUNTIME_GOAL_COVERAGE_PENDING
            for goal in derived_goals
        ),
    }


def _projection_payload(projection: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in projection.items()
        if key != "projection_digest"
    }


def project_plan_projection(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
) -> dict[str, Any]:
    integrity = validate_plan_authority_pair(definition=definition, plan_run=plan_run)
    if not integrity.get("ok"):
        raise ValueError(str(integrity.get("code") or "PLAN_AUTHORITY_PAIR_INVALID"))

    step_states = (
        plan_run.get("step_states")
        if isinstance(plan_run.get("step_states"), dict)
        else {}
    )
    steps: list[dict[str, Any]] = []
    for structural in list(definition.get("steps") or []):
        if not isinstance(structural, dict):
            continue
        effect_id = str(structural.get("effect_id") or "")
        state = dict(step_states.get(effect_id) or {})
        verification = {
            **dict(structural.get("verification") or {}),
            **dict(state.get("verification") or {}),
        }
        steps.append({
            **deepcopy(structural),
            "status": str(state.get("status") or "PLANNED"),
            "result_summary": state.get("result_summary"),
            "failure_type": str(state.get("failure_type") or "NONE"),
            "failure_reason": state.get("failure_reason"),
            "verification": verification,
        })

    tasks = [
        {**deepcopy(task), "status": "PLANNED"}
        for task in list(definition.get("tasks") or [])
        if isinstance(task, dict)
    ]
    terminal_states = (
        plan_run.get("terminal_goal_states")
        if isinstance(plan_run.get("terminal_goal_states"), dict)
        else {}
    )
    goals: list[dict[str, Any]] = []
    for goal in list(definition.get("goals") or []):
        if not isinstance(goal, dict):
            continue
        goal_id = str(goal.get("goal_id") or "")
        terminal = (
            terminal_states.get(goal_id)
            if isinstance(terminal_states.get(goal_id), dict)
            else {}
        )
        terminal_tool = str(terminal.get("terminal_tool") or "")
        goals.append({
            **deepcopy(goal),
            "coverage_status": _RUNTIME_GOAL_COVERAGE_PENDING,
            "covered_by_terminal_tools": [terminal_tool] if terminal_tool else [],
        })

    runtime_view = derive_plan_runtime_view(
        goals=goals,
        tasks=tasks,
        steps=steps,
    )
    goals = runtime_view["goals"]
    tasks = runtime_view["tasks"]
    steps = runtime_view["steps"]
    projection: dict[str, Any] = {
        "plan_contract_version": str(
            definition.get("source_plan_contract_version")
            or "grounded-execution-plan@2"
        ),
        "projection_contract_version": PLAN_PROJECTION_CACHE_VERSION,
        "authority": PLAN_PROJECTION_AUTHORITY,
        "plan_definition_id": str(definition.get("plan_definition_id") or ""),
        "plan_definition_digest": str(definition.get("definition_digest") or ""),
        "plan_run_id": str(plan_run.get("plan_run_id") or ""),
        "plan_run_digest": str(integrity.get("plan_run_digest") or ""),
        "workflow_id": str(definition.get("workflow_id") or ""),
        "turn_plan_id": str(definition.get("turn_plan_id") or ""),
        "formal_semantic_contract_id": definition.get("formal_semantic_contract_id"),
        "formal_semantic_digest": definition.get("formal_semantic_digest"),
        "goal_source": str(definition.get("goal_source") or ""),
        "level": str(definition.get("level") or ""),
        "status": str(runtime_view["status"]),
        "goal": deepcopy(definition.get("goal")),
        "goals": goals,
        "goal_coverage_complete": bool(runtime_view["goal_coverage_complete"]),
        "tasks": tasks,
        "steps": steps,
        "created_turn": int(definition.get("created_turn") or 0),
        "updated_turn": int(
            plan_run.get("updated_turn") or plan_run.get("created_turn") or 0
        ),
        "reasons": deepcopy(definition.get("reasons") or []),
        "validation": deepcopy(definition.get("validation")),
        "plan_digest": str(
            definition.get("source_plan_digest")
            or definition.get("definition_digest")
            or ""
        ),
        "immutable_structure": True,
        "compatibility_projection": True,
    }
    projection["projection_digest"] = canonical_contract_digest(
        _projection_payload(projection)
    )
    return projection


def derive_plan_runtime_status(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
) -> str:
    """Derive the persisted PlanRun status from the same Kernel projection.

    ``plan_run.status`` is stored for operational inspection, but it is not a
    separate state machine. Every writer calls this function after mutating
    PlanRun evidence so the persisted value and compatibility projection cannot
    diverge.
    """
    return str(
        project_plan_projection(
            definition=definition,
            plan_run=plan_run,
        ).get("status")
        or "RUNNING"
    )


def validate_cached_plan_projection(
    *,
    projection: dict[str, Any] | None,
    definition: dict[str, Any] | None,
    plan_run: dict[str, Any] | None,
) -> dict[str, Any]:
    authority = validate_plan_authority_pair(definition=definition, plan_run=plan_run)
    if not authority.get("ok"):
        return authority
    assert isinstance(definition, dict)
    assert isinstance(plan_run, dict)
    if not isinstance(projection, dict):
        return {"ok": False, "code": "PLAN_PROJECTION_REQUIRED"}
    if str(projection.get("projection_contract_version") or "") != PLAN_PROJECTION_CACHE_VERSION:
        return {"ok": False, "code": "PLAN_PROJECTION_VERSION_INVALID"}
    if str(projection.get("authority") or "") != PLAN_PROJECTION_AUTHORITY:
        return {"ok": False, "code": "PLAN_PROJECTION_AUTHORITY_INVALID"}
    if not bool(projection.get("compatibility_projection")):
        return {"ok": False, "code": "PLAN_PROJECTION_MARKER_INVALID"}
    expected_binding = {
        "plan_definition_id": str(definition.get("plan_definition_id") or ""),
        "plan_definition_digest": str(definition.get("definition_digest") or ""),
        "plan_run_id": str(plan_run.get("plan_run_id") or ""),
        "plan_run_digest": str(authority.get("plan_run_digest") or ""),
    }
    actual_binding = {key: str(projection.get(key) or "") for key in expected_binding}
    if actual_binding != expected_binding:
        return {
            "ok": False,
            "code": "PLAN_PROJECTION_BINDING_MISMATCH",
            "expected": expected_binding,
            "actual": actual_binding,
        }
    recorded = str(projection.get("projection_digest") or "")
    actual = canonical_contract_digest(_projection_payload(projection))
    if not recorded or recorded != actual:
        return {
            "ok": False,
            "code": "PLAN_PROJECTION_DIGEST_INVALID",
            "recorded_digest": recorded or None,
            "actual_digest": actual,
        }
    return {
        "ok": True,
        "code": "PLAN_PROJECTION_VALID",
        "projection_digest": actual,
        "plan_run_digest": expected_binding["plan_run_digest"],
    }



def validate_ephemeral_grounded_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a same-turn plan before Definition/Run materialization.

    This path is intentionally narrower than the compatibility projection.  It
    accepts only the planner's self-bound structural output and is never used as
    durable authority.
    """
    if not isinstance(plan, dict):
        return {"ok": False, "code": "EPHEMERAL_PLAN_REQUIRED"}
    if str(plan.get("authority") or "") != "validated_execution_plan_not_semantic_or_business_fact":
        return {"ok": False, "code": "EPHEMERAL_PLAN_AUTHORITY_INVALID"}
    if not bool(plan.get("immutable_structure")):
        return {"ok": False, "code": "EPHEMERAL_PLAN_NOT_IMMUTABLE"}
    validation = plan.get("validation") if isinstance(plan.get("validation"), dict) else {}
    validation_status = str(validation.get("status") or "")
    if validation_status not in {"ACCEPTED", "REJECTED"}:
        return {"ok": False, "code": "EPHEMERAL_PLAN_VALIDATION_REQUIRED"}
    recorded = str(plan.get("plan_digest") or "")
    expected = str(validation.get("structure_digest") or "")
    if not recorded or recorded != expected:
        return {
            "ok": False,
            "code": "EPHEMERAL_PLAN_DIGEST_MISMATCH",
            "recorded_digest": recorded or None,
            "expected_digest": expected or None,
        }
    return {
        "ok": True,
        "code": "EPHEMERAL_PLAN_VALID",
        "validation_status": validation_status,
        "dispatch_allowed": bool(validation.get("dispatch_allowed")),
    }

def resolve_plan_projection(state: dict[str, Any] | None) -> dict[str, Any]:
    source = state if isinstance(state, dict) else {}
    definition = (
        source.get("frozen_plan_definition")
        if isinstance(source.get("frozen_plan_definition"), dict)
        else None
    )
    plan_run = source.get("plan_run") if isinstance(source.get("plan_run"), dict) else None
    persisted = (
        source.get("grounded_execution_plan")
        if isinstance(source.get("grounded_execution_plan"), dict)
        else None
    )

    if definition is not None or plan_run is not None:
        authority = validate_plan_authority_pair(
            definition=definition,
            plan_run=plan_run,
        )
        if not authority.get("ok"):
            return {
                "ok": False,
                "present": True,
                "plan": None,
                "code": str(authority.get("code") or "PLAN_AUTHORITY_PAIR_INVALID"),
                "source": "authoritative_pair",
                "integrity": authority,
            }
        cached = validate_cached_plan_projection(
            projection=persisted,
            definition=definition,
            plan_run=plan_run,
        )
        if cached.get("ok"):
            return {
                "ok": True,
                "present": True,
                "plan": deepcopy(persisted),
                "code": "PLAN_PROJECTION_CACHE_ACCEPTED",
                "source": "validated_cache",
                "integrity": cached,
            }
        try:
            derived = project_plan_projection(
                definition=definition or {},
                plan_run=plan_run or {},
            )
        except ValueError as exc:
            return {
                "ok": False,
                "present": True,
                "plan": None,
                "code": str(exc) or "PLAN_PROJECTION_DERIVATION_FAILED",
                "source": "authoritative_pair",
                "integrity": cached,
            }
        return {
            "ok": True,
            "present": True,
            "plan": derived,
            "code": "PLAN_PROJECTION_REDERIVED",
            "source": "authoritative_pair",
            "integrity": cached,
        }

    ephemeral = validate_ephemeral_grounded_plan(persisted)
    if ephemeral.get("ok"):
        return {
            "ok": True,
            "present": True,
            "plan": deepcopy(persisted),
            "code": (
                "EPHEMERAL_PLAN_ACCEPTED"
                if str(ephemeral.get("validation_status") or "") == "ACCEPTED"
                else "EPHEMERAL_PLAN_REPAIR_VIEW"
            ),
            "source": "same_turn_validated_plan",
            "integrity": ephemeral,
        }

    return {
        "ok": True,
        "present": False,
        "plan": None,
        "code": "PLAN_PROJECTION_NOT_PRESENT",
        "source": "none",
    }


def read_plan_projection(state: dict[str, Any] | None) -> dict[str, Any] | None:
    resolution = resolve_plan_projection(state)
    plan = resolution.get("plan")
    return deepcopy(plan) if resolution.get("ok") and isinstance(plan, dict) else None


__all__ = [
    "FROZEN_PLAN_DEFINITION_VERSION",
    "PLAN_RUN_VERSION",
    "PLAN_PROJECTION_CACHE_VERSION",
    "PLAN_PROJECTION_AUTHORITY",
    "canonical_contract_digest",
    "validate_plan_authority_pair",
    "project_plan_projection",
    "derive_plan_runtime_view",
    "derive_plan_runtime_status",
    "validate_cached_plan_projection",
    "validate_ephemeral_grounded_plan",
    "resolve_plan_projection",
    "read_plan_projection",
]
