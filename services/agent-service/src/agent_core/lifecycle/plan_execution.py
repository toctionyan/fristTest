from __future__ import annotations

"""Immutable plan definitions and separate execution-run evidence.

The module never interprets user language and never grants business authority.
It freezes an already validated grounded plan, records runtime attempts/outcomes,
and can project a compatibility view for legacy consumers during migration.
"""

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from agent_core.kernel.plan_projection_contract import (
    derive_plan_runtime_status,
    project_plan_projection,
)

FROZEN_PLAN_DEFINITION_VERSION = "frozen-plan-definition@1"
PLAN_RUN_VERSION = "plan-run@1"
STEP_ATTEMPT_VERSION = "step-attempt@1"
STEP_OUTCOME_VERSION = "step-outcome@1"

_RUNTIME_STEP_FIELDS = {
    "status",
    "result_summary",
    "failure_type",
    "failure_reason",
}
_RUNTIME_GOAL_FIELDS = {
    "coverage_status",
    "covered_by_step_ids",
    "covered_by_terminal_tools",
    "satisfaction_proof",
}
_RUNTIME_TASK_FIELDS = {"status"}
_RUNTIME_VERIFICATION_FIELDS = {
    "last_result_code",
    "runtime_outcome_type",
    "verified_by_runtime",
    "verified_result_member_count",
    "goal_cardinality_eligible",
    "goal_completion_eligible",
    "superseded_by_effect_id",
    "candidate_repaired",
}

_TERMINAL_SUCCESS = {"SUCCEEDED", "SKIPPED"}
_TERMINAL_FAILURE = {"FAILED_FINAL"}
_BLOCKED = {"NEEDS_INPUT", "AWAITING_AUTHORIZATION", "SUBMISSION_UNKNOWN"}
_RETRYABLE = {"FAILED_RETRYABLE"}


def _canonical_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
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


def _strip_fields(row: dict[str, Any], denied: set[str]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in row.items() if key not in denied}


def _strip_step_runtime(row: dict[str, Any]) -> dict[str, Any]:
    output = _strip_fields(row, _RUNTIME_STEP_FIELDS)
    verification = output.get("verification") if isinstance(output.get("verification"), dict) else {}
    output["verification"] = {
        key: deepcopy(value)
        for key, value in verification.items()
        if key not in _RUNTIME_VERIFICATION_FIELDS
    }
    return output


def freeze_plan_definition(
    plan: dict[str, Any],
    *,
    plan_definition_id: str | None = None,
) -> dict[str, Any]:
    """Freeze structural plan data and exclude all runtime progress fields."""
    if not isinstance(plan, dict):
        raise ValueError("GROUNDED_PLAN_REQUIRED")
    steps = [
        _strip_step_runtime(row)
        for row in list(plan.get("steps") or [])
        if isinstance(row, dict)
    ]
    goals = [
        _strip_fields(row, _RUNTIME_GOAL_FIELDS)
        for row in list(plan.get("goals") or [])
        if isinstance(row, dict)
    ]
    tasks = [
        _strip_fields(row, _RUNTIME_TASK_FIELDS)
        for row in list(plan.get("tasks") or [])
        if isinstance(row, dict)
    ]
    source_digest = str(plan.get("plan_digest") or "")
    definition: dict[str, Any] = {
        "version": FROZEN_PLAN_DEFINITION_VERSION,
        "plan_definition_id": str(plan_definition_id or plan.get("plan_definition_id") or f"plan-definition:{uuid4().hex}"),
        "source_plan_contract_version": str(plan.get("plan_contract_version") or ""),
        "source_plan_digest": source_digest or None,
        "workflow_id": str(plan.get("workflow_id") or ""),
        "turn_plan_id": str(plan.get("turn_plan_id") or ""),
        "formal_semantic_contract_id": str(plan.get("formal_semantic_contract_id") or "") or None,
        "formal_semantic_digest": str(plan.get("formal_semantic_digest") or "") or None,
        "goal_source": str(plan.get("goal_source") or ""),
        "level": str(plan.get("level") or ""),
        "goal": deepcopy(plan.get("goal")),
        "goals": goals,
        "tasks": tasks,
        "steps": steps,
        "created_turn": int(plan.get("created_turn") or 0),
        "reasons": [str(value) for value in list(plan.get("reasons") or []) if str(value)],
        "validation": deepcopy(plan.get("validation")) if isinstance(plan.get("validation"), dict) else None,
        "authority": "immutable_plan_structure_not_execution_or_business_fact",
    }
    definition["definition_digest"] = _canonical_digest(_definition_payload(definition))
    definition["immutable"] = True
    return definition


def validate_frozen_plan_definition(definition: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(definition, dict):
        return {"ok": False, "code": "FROZEN_PLAN_DEFINITION_REQUIRED"}
    if str(definition.get("version") or "") != FROZEN_PLAN_DEFINITION_VERSION:
        return {"ok": False, "code": "FROZEN_PLAN_DEFINITION_VERSION_INVALID"}
    if not bool(definition.get("immutable")):
        return {"ok": False, "code": "FROZEN_PLAN_DEFINITION_NOT_IMMUTABLE"}
    recorded = str(definition.get("definition_digest") or "")
    actual = _canonical_digest(_definition_payload(definition))
    if not recorded or recorded != actual:
        return {
            "ok": False,
            "code": "FROZEN_PLAN_DEFINITION_DIGEST_INVALID",
            "recorded_digest": recorded or None,
            "actual_digest": actual,
        }
    effect_ids = [str(row.get("effect_id") or "") for row in list(definition.get("steps") or []) if isinstance(row, dict)]
    if any(not value for value in effect_ids) or len(effect_ids) != len(set(effect_ids)):
        return {"ok": False, "code": "FROZEN_PLAN_EFFECT_IDS_INVALID"}
    return {"ok": True, "code": "FROZEN_PLAN_DEFINITION_VALID", "definition_digest": actual}


def _initial_step_states(definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(step.get("effect_id")): {
            "status": "PLANNED",
            "latest_attempt_id": None,
            "latest_outcome_id": None,
            "attempt_count": 0,
            "result_summary": None,
            "failure_type": "NONE",
            "failure_reason": None,
            "verification": {},
        }
        for step in list(definition.get("steps") or [])
        if isinstance(step, dict) and str(step.get("effect_id") or "")
    }


def create_plan_run(
    definition: dict[str, Any],
    *,
    turn_index: int,
    previous_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    integrity = validate_frozen_plan_definition(definition)
    if not integrity.get("ok"):
        raise ValueError(str(integrity.get("code") or "FROZEN_PLAN_DEFINITION_INVALID"))
    if isinstance(previous_run, dict):
        previous_check = validate_plan_run(definition=definition, plan_run=previous_run)
        if previous_check.get("ok"):
            return deepcopy(previous_run)
    run = {
        "version": PLAN_RUN_VERSION,
        "plan_run_id": f"plan-run:{uuid4().hex}",
        "plan_definition_id": str(definition.get("plan_definition_id") or ""),
        "plan_definition_digest": str(definition.get("definition_digest") or ""),
        "status": "PLANNED",
        "created_turn": int(turn_index),
        "updated_turn": int(turn_index),
        "step_states": _initial_step_states(definition),
        "terminal_goal_states": {},
        "attempts": [],
        "outcomes": [],
        "authority": "execution_progress_only_not_plan_structure_or_business_fact",
    }
    run["status"] = derive_plan_runtime_status(
        definition=definition,
        plan_run=run,
    )
    return run


def revise_plan_run(
    *,
    previous_definition: dict[str, Any],
    previous_run: dict[str, Any],
    definition: dict[str, Any],
    turn_index: int,
) -> dict[str, Any]:
    """Create a run for a new immutable plan revision and inherit safe evidence.

    Only unchanged effect definitions retain runtime progress. New or structurally
    changed effects start PLANNED. The prior definition remains immutable and is
    linked through revision provenance.
    """
    previous_check = validate_frozen_plan_definition(previous_definition)
    current_check = validate_frozen_plan_definition(definition)
    if not previous_check.get("ok") or not current_check.get("ok"):
        raise ValueError("PLAN_REVISION_DEFINITION_INVALID")
    previous_run_check = validate_plan_run(definition=previous_definition, plan_run=previous_run)
    if not previous_run_check.get("ok"):
        raise ValueError(str(previous_run_check.get("code") or "PLAN_REVISION_RUN_INVALID"))
    run = create_plan_run(definition, turn_index=turn_index)
    prior_steps = {
        str(row.get("effect_id") or ""): row
        for row in list(previous_definition.get("steps") or [])
        if isinstance(row, dict) and str(row.get("effect_id") or "")
    }
    current_steps = {
        str(row.get("effect_id") or ""): row
        for row in list(definition.get("steps") or [])
        if isinstance(row, dict) and str(row.get("effect_id") or "")
    }
    inherited: set[str] = set()
    prior_states = previous_run.get("step_states") if isinstance(previous_run.get("step_states"), dict) else {}
    for effect_id, current_step in current_steps.items():
        prior_step = prior_steps.get(effect_id)
        if prior_step is None:
            continue
        if _canonical_digest({"step": prior_step}) != _canonical_digest({"step": current_step}):
            continue
        if isinstance(prior_states.get(effect_id), dict):
            run["step_states"][effect_id] = deepcopy(prior_states[effect_id])
            inherited.add(effect_id)
    run["attempts"] = [
        deepcopy(row)
        for row in list(previous_run.get("attempts") or [])
        if isinstance(row, dict) and str(row.get("effect_id") or "") in inherited
    ]
    run["outcomes"] = [
        deepcopy(row)
        for row in list(previous_run.get("outcomes") or [])
        if isinstance(row, dict) and str(row.get("effect_id") or "") in inherited
    ]
    run["terminal_goal_states"] = deepcopy(previous_run.get("terminal_goal_states") or {})
    run["status"] = derive_plan_runtime_status(
        definition=definition,
        plan_run=run,
    )
    run["migrated_from_plan_run_id"] = str(previous_run.get("plan_run_id") or "") or None
    run["previous_plan_definition_id"] = str(previous_definition.get("plan_definition_id") or "") or None
    run["inherited_effect_ids"] = sorted(inherited)
    return run


def validate_plan_run(*, definition: dict[str, Any], plan_run: dict[str, Any] | None) -> dict[str, Any]:
    definition_check = validate_frozen_plan_definition(definition)
    if not definition_check.get("ok"):
        return definition_check
    if not isinstance(plan_run, dict):
        return {"ok": False, "code": "PLAN_RUN_REQUIRED"}
    if str(plan_run.get("version") or "") != PLAN_RUN_VERSION:
        return {"ok": False, "code": "PLAN_RUN_VERSION_INVALID"}
    if (
        str(plan_run.get("plan_definition_id") or "") != str(definition.get("plan_definition_id") or "")
        or str(plan_run.get("plan_definition_digest") or "") != str(definition.get("definition_digest") or "")
    ):
        return {"ok": False, "code": "PLAN_RUN_DEFINITION_MISMATCH"}
    expected_effects = {
        str(row.get("effect_id") or "")
        for row in list(definition.get("steps") or [])
        if isinstance(row, dict) and str(row.get("effect_id") or "")
    }
    actual_effects = set((plan_run.get("step_states") or {}).keys()) if isinstance(plan_run.get("step_states"), dict) else set()
    if expected_effects != actual_effects:
        return {
            "ok": False,
            "code": "PLAN_RUN_STEP_STATE_MISMATCH",
            "missing": sorted(expected_effects - actual_effects),
            "extra": sorted(actual_effects - expected_effects),
        }
    return {"ok": True, "code": "PLAN_RUN_VALID"}


def _step_definition(definition: dict[str, Any], effect_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in list(definition.get("steps") or [])
            if isinstance(row, dict) and str(row.get("effect_id") or "") == effect_id
        ),
        None,
    )


def begin_step_attempt(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
    effect_id: str,
    tool_name: str,
    args: dict[str, Any],
    execution_permit: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    check = validate_plan_run(definition=definition, plan_run=plan_run)
    if not check.get("ok"):
        raise ValueError(str(check.get("code") or "PLAN_RUN_INVALID"))
    step = _step_definition(definition, effect_id)
    if step is None:
        raise ValueError("PLAN_STEP_NOT_FOUND")
    expected_tool = str(step.get("tool_name") or "")
    if expected_tool and tool_name != expected_tool:
        raise ValueError("PLAN_STEP_TOOL_MISMATCH")
    run = deepcopy(plan_run)
    attempt_id = f"step-attempt:{uuid4().hex}"
    attempt = {
        "version": STEP_ATTEMPT_VERSION,
        "attempt_id": attempt_id,
        "plan_run_id": str(run.get("plan_run_id") or ""),
        "plan_definition_id": str(definition.get("plan_definition_id") or ""),
        "effect_id": effect_id,
        "step_id": str(step.get("step_id") or ""),
        "tool_name": tool_name,
        "input_digest": _canonical_digest({"tool_name": tool_name, "args": deepcopy(args)}),
        "permit_id": str((execution_permit or {}).get("permit_id") or "") or None,
        "status": "STARTED",
        "completion_proof": False,
    }
    run.setdefault("attempts", []).append(attempt)
    step_state = dict((run.get("step_states") or {}).get(effect_id) or {})
    step_state.update({
        "status": "RUNNING",
        "latest_attempt_id": attempt_id,
        "attempt_count": int(step_state.get("attempt_count") or 0) + 1,
    })
    run["step_states"][effect_id] = step_state
    run["status"] = derive_plan_runtime_status(
        definition=definition,
        plan_run=run,
    )
    return run, deepcopy(attempt)


def _extract_identifier(result: dict[str, Any], *keys: str) -> str | None:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    for source in (result, data):
        for key in keys:
            value = source.get(key)
            if value is not None and str(value):
                return str(value)
    return None



def complete_step_attempt(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
    attempt_id: str,
    result: dict[str, Any],
    step_status: str,
    failure_type: str,
    result_summary: str | None = None,
    verification: dict[str, Any] | None = None,
    related_step_updates: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    check = validate_plan_run(definition=definition, plan_run=plan_run)
    if not check.get("ok"):
        raise ValueError(str(check.get("code") or "PLAN_RUN_INVALID"))
    run = deepcopy(plan_run)
    attempts = [row for row in list(run.get("attempts") or []) if isinstance(row, dict)]
    attempt_index = next((index for index, row in enumerate(attempts) if str(row.get("attempt_id") or "") == attempt_id), None)
    if attempt_index is None:
        raise ValueError("STEP_ATTEMPT_NOT_FOUND")
    attempt = dict(attempts[attempt_index])
    if str(attempt.get("status") or "") != "STARTED":
        raise ValueError("STEP_ATTEMPT_ALREADY_COMPLETED")
    effect_id = str(attempt.get("effect_id") or "")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    outcome_effects = str((result.get("runtime_outcome") or {}).get("effects") or "") if isinstance(result.get("runtime_outcome"), dict) else ""
    receipt_id = _extract_identifier(result, "receipt_id", "refund_receipt_id", "invoice_receipt_id")
    draft_id = _extract_identifier(result, "draft_id", "offer_handle")
    assessment_id = _extract_identifier(result, "assessment_id", "eligibility_id")
    step = _step_definition(definition, effect_id) or {}
    step_verification = step.get("verification") if isinstance(step.get("verification"), dict) else {}
    completion_owner = str(step_verification.get("completion_owner") or "")
    completion_proof = bool(
        receipt_id
        and completion_owner == "transaction_runtime"
        and step_status == "SUCCEEDED"
        and outcome_effects not in {"draft_created", "authority_required", "input_required"}
    )
    outcome = {
        "version": STEP_OUTCOME_VERSION,
        "outcome_id": f"step-outcome:{uuid4().hex}",
        "attempt_id": attempt_id,
        "plan_run_id": str(run.get("plan_run_id") or ""),
        "effect_id": effect_id,
        "status": step_status,
        "result_code": str(result.get("code") or "") or None,
        "result_summary": result_summary if result_summary is not None else (str(result.get("message") or "")[:500] or None),
        "failure_type": failure_type,
        "failure_reason": None if failure_type in {"", "NONE"} else str(result.get("message") or result.get("code") or ""),
        "result_handle": _extract_identifier(result, "result_handle", "handle"),
        "assessment_id": assessment_id,
        "draft_id": draft_id,
        "receipt_id": receipt_id,
        "completion_proof": completion_proof,
        "completion_proof_kind": "transaction_receipt" if completion_proof else None,
        "completion_owner": completion_owner or None,
        "verification": deepcopy(verification or {}),
        "result_digest": _canonical_digest({"result": deepcopy(result)}),
    }
    attempt["status"] = "COMPLETED"
    attempt["outcome_id"] = outcome["outcome_id"]
    attempt["completion_proof"] = completion_proof
    attempts[attempt_index] = attempt
    run["attempts"] = attempts
    run.setdefault("outcomes", []).append(outcome)
    step_state = dict((run.get("step_states") or {}).get(effect_id) or {})
    step_state.update({
        "status": step_status,
        "latest_outcome_id": outcome["outcome_id"],
        "result_summary": outcome["result_summary"],
        "failure_type": failure_type,
        "failure_reason": outcome["failure_reason"],
        "verification": deepcopy(verification or {}),
        "completion_proof": completion_proof,
    })
    run["step_states"][effect_id] = step_state

    # A successful repair may deterministically supersede earlier retryable
    # candidate steps. Persist those related updates in PlanRun in the same
    # atomic write as the current outcome; a compatibility projection must
    # never be used as an intermediate write authority.
    for related_effect_id, patch in dict(related_step_updates or {}).items():
        related_id = str(related_effect_id or "")
        if not related_id or related_id == effect_id:
            continue
        if related_id not in run["step_states"]:
            raise ValueError("RELATED_PLAN_STEP_NOT_FOUND")
        if not isinstance(patch, dict):
            raise ValueError("RELATED_PLAN_STEP_UPDATE_INVALID")
        related_state = dict(run["step_states"].get(related_id) or {})
        for key in ("status", "result_summary", "failure_type", "failure_reason", "completion_proof"):
            if key in patch:
                related_state[key] = deepcopy(patch.get(key))
        if isinstance(patch.get("verification"), dict):
            related_state["verification"] = {
                **dict(related_state.get("verification") or {}),
                **deepcopy(patch["verification"]),
            }
        run["step_states"][related_id] = related_state

    run["status"] = derive_plan_runtime_status(
        definition=definition,
        plan_run=run,
    )
    return run, deepcopy(outcome)


def record_terminal_goal_outcome(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
    goal_ids: list[str],
    terminal_tool: str,
) -> dict[str, Any]:
    """Record terminal dialogue handling in PlanRun, not in plan structure."""
    check = validate_plan_run(definition=definition, plan_run=plan_run)
    if not check.get("ok"):
        raise ValueError(str(check.get("code") or "PLAN_RUN_INVALID"))
    known_goals = {
        str(row.get("goal_id") or "")
        for row in list(definition.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    requested = [str(value) for value in goal_ids if str(value)]
    if not requested or any(value not in known_goals for value in requested):
        raise ValueError("TERMINAL_GOAL_BINDING_INVALID")
    run = deepcopy(plan_run)
    terminal_states = dict(run.get("terminal_goal_states") or {})
    for goal_id in requested:
        terminal_states[goal_id] = {
            "terminal_tool": terminal_tool,
            "handling_status": "BLOCKED" if terminal_tool == "ask_user_clarification" else "COVERED",
        }
    run["terminal_goal_states"] = terminal_states
    run["status"] = derive_plan_runtime_status(
        definition=definition,
        plan_run=run,
    )
    return run



def project_grounded_execution_plan(
    *,
    definition: dict[str, Any],
    plan_run: dict[str, Any],
) -> dict[str, Any]:
    """Return the single Kernel-owned compatibility projection."""
    return project_plan_projection(definition=definition, plan_run=plan_run)


__all__ = [
    "FROZEN_PLAN_DEFINITION_VERSION",
    "PLAN_RUN_VERSION",
    "STEP_ATTEMPT_VERSION",
    "STEP_OUTCOME_VERSION",
    "freeze_plan_definition",
    "validate_frozen_plan_definition",
    "create_plan_run",
    "revise_plan_run",
    "validate_plan_run",
    "begin_step_attempt",
    "complete_step_attempt",
    "record_terminal_goal_outcome",
    "project_grounded_execution_plan",
]
