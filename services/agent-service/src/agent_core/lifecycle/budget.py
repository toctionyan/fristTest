from __future__ import annotations

"""Small, deterministic and domain-neutral loop-budget policy.

The policy observes only already executed Runtime outcomes and execution
classifications.  It never names a resource type or tool name.  A completed,
customer-safe read/status observation may restrict the following model step to
a terminal response or clarification, preventing identical re-queries without
turning the Core into a business-specific router.
"""

from dataclasses import dataclass
from typing import Any

from agent_core.kernel.plan_projection_contract import read_plan_projection


@dataclass(frozen=True)
class LoopBudget:
    mode: str
    reason: str
    terminal_only: bool


def _is_sufficient_observation(item: dict[str, Any]) -> bool:
    """Return true only for an already verified, non-mutating observation.

    RuntimeOutcome is the closed, cross-domain boundary: Core uses its effects
    and interaction requirement rather than a hard-coded list of ecommerce
    tool names.  A domain Plugin can opt out by returning an interaction or a
    non-``none`` effect.
    """
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    if not result.get("ok"):
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if data.get("needs_clarification") or data.get("supported") is False:
        return False
    runtime_outcome = result.get("runtime_outcome") if isinstance(result.get("runtime_outcome"), dict) else {}
    if not runtime_outcome:
        return False
    if str(runtime_outcome.get("effects") or "none") != "none":
        return False
    if str(runtime_outcome.get("next_interaction") or "none") != "none":
        return False
    if not bool(runtime_outcome.get("safe_to_continue")):
        return False
    disposition = result.get("execution_disposition") if isinstance(result.get("execution_disposition"), dict) else {}
    # Unknown/unavailable outcomes must be presented safely, not treated as a
    # completed fact from which a terminal answer can be derived.
    if str(disposition.get("kind") or disposition.get("disposition") or "") in {
        "submission_unknown", "system_unavailable", "failure", "clarify",
    }:
        return False
    return True


def verified_history_recall_results(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return complete latest audit observations, or an empty list.

    A comparison may inspect several prior public turns.  One successful audit
    must not hide a failed sibling and prematurely narrow the tool surface to a
    terminal response.  Group by trace handle and consider only the latest
    attempt for each handle, allowing a bounded literal-span repair.
    """
    latest_by_handle: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(list(state.get("tool_trace") or [])):
        if not isinstance(row, dict) or str(row.get("name") or "") != "inspect_audit_event":
            continue
        args = row.get("args") if isinstance(row.get("args"), dict) else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        handle = str(args.get("trace_handle") or data.get("trace_handle") or "")
        # Older persisted checkpoints may omit the call args/echoed handle.
        # Retain their single successful audit as an isolated observation;
        # current executions always use the real handle and can supersede a
        # failed attempt for that same event.
        latest_by_handle[handle or f"__legacy_audit__:{index}"] = row
    if not latest_by_handle:
        return []
    results: list[dict[str, Any]] = []
    for row in latest_by_handle.values():
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        if not (
            bool(result.get("ok"))
            and bool(data.get("historical_only"))
            and str(data.get("answer_summary") or "").strip()
            and any(str(handle) for handle in list(data.get("result_handles") or []))
        ):
            return []
        results.append(dict(data))
    return results


def compute_loop_budget(state: dict[str, Any]) -> LoopBudget:
    workflow = read_plan_projection(state) or {}
    uncovered_goals = [
        goal for goal in list(workflow.get("goals") or [])
        if isinstance(goal, dict)
        and bool(goal.get("required", True))
        and str(goal.get("coverage_status") or "") == "PENDING"
    ]
    pending_steps = [
        step for step in list(workflow.get("steps") or [])
        if isinstance(step, dict)
        and bool(step.get("required", True))
        and str(step.get("status") or "") in {"PLANNED", "RUNNING"}
    ]
    if uncovered_goals or pending_steps:
        # A successful, runtime-owned audit lookup may expose the exact result
        # handles that backed a prior customer-visible answer.  It is a
        # sufficient observation for an explicit history-recall turn even
        # though the bound query goal remains pending until respond_to_user
        # cites those handles.  Re-querying business data here would require
        # inventing current-turn entity spans from history.
        if verified_history_recall_results(state):
            return LoopBudget("terminal", "verified_history_recall_ready", True)
        return LoopBudget("open", "workflow_goal_or_step_incomplete", False)
    trace = [item for item in (state.get("tool_trace") or []) if isinstance(item, dict)]
    external = [item for item in trace if str(item.get("classification") or "") != "internal"]
    if not external:
        return LoopBudget("observe", "no_external_observation_yet", False)
    if state.get("action_queue"):
        return LoopBudget("transaction", "action_draft_requires_gateway", False)
    if not all(_is_sufficient_observation(item) for item in external):
        return LoopBudget("open", "observation_requires_follow_up", False)
    return LoopBudget("terminal", "sufficient_verified_observation", True)
