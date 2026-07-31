from __future__ import annotations

"""Graph node that routes a verified execution disposition without semantic replanning."""

from typing import Any

from agent_core.lifecycle.execution_disposition import latest_disposition
from agent_core.transaction.interaction import explicit_interaction_response_contract
from agent_core.runtime.node_support import append_decision as _append_decision


def classify_execution_disposition_node(state: dict[str, Any]) -> dict[str, Any]:
    # A structured form/authority contract is an explicit runtime state, not
    # model prose. It always preempts a generic explanation loop.
    if explicit_interaction_response_contract(state) is not None:
        return {
            "phase": "offer_confirmation",
            "model_mode_restriction": None,
            "status": "ExecutionDisposition:structured_interaction",
            "decision_chain": _append_decision(state, stage="classify_execution_disposition", decision="structured_interaction_preempted_model_loop", details={}),
        }
    disposition = latest_disposition(state)
    if disposition is None:
        return {
            "phase": "action_gateway" if state.get("action_queue") else "agent_loop",
            "model_mode_restriction": None,
            "decision_chain": _append_decision(state, stage="classify_execution_disposition", decision="no_formal_execution_to_classify", details={}),
        }
    kind = str(disposition.get("disposition") or "continue")
    if state.get("action_queue") and kind == "continue":
        phase = "action_gateway"
        restriction = None
    elif kind == "continue":
        phase = "agent_loop"
        restriction = None
    elif kind == "needs_clarification":
        phase = "agent_loop"
        restriction = list(disposition.get("allowed_model_modes") or ["clarify", "safe_grounding", "respond"])
    elif kind in {"business_conclusion", "unsupported"}:
        # A formal runtime step may already have produced a bounded, grounded
        # final answer (for example a commit or reconciliation result).  In
        # that case do not make the model plan again; preserve the exact
        # conclusion and finalize.  Otherwise the model is explanation-only.
        phase = "final" if state.get("current_final_answer") else "agent_loop"
        restriction = list(disposition.get("allowed_model_modes") or ["respond"])
    elif kind == "reconcile_submission":
        phase = "reconcile_submission"
        restriction = ["explain"]
    else:  # retry_infrastructure means the adapter has exhausted its own bounded retry.
        phase = "final"
        restriction = ["explain"]
    return {
        "latest_execution_disposition": disposition,
        "model_mode_restriction": restriction,
        "phase": phase,
        "status": f"ExecutionDisposition:{kind}",
        "decision_chain": _append_decision(
            state,
            stage="classify_execution_disposition",
            decision="execution_disposition_routed",
            details={"disposition": kind, "runtime_action": disposition.get("runtime_action"), "auto_target_switch": False},
        ),
    }


def route_after_execution_disposition(state: dict[str, Any]) -> str:
    phase = str(state.get("phase") or "")
    if phase == "offer_confirmation":
        return "confirm"
    if phase == "action_gateway":
        return "gateway"
    if phase == "reconcile_submission":
        return "reconcile"
    if phase == "final":
        return "final"
    return "loop"
