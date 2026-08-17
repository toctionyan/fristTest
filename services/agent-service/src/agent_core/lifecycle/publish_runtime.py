from __future__ import annotations

"""Final persistence and public-stream boundary for the generic lifecycle graph.

LangGraph persists the merged state after this node through the configured
checkpointer.  HTTP/SSE adapters publish only the resulting public projection
after the graph returns.  This node deliberately does not re-render, re-plan or
write business state; it creates one auditable boundary between finalization and
transport delivery.
"""

from typing import Any

from agent_core.runtime.node_support import append_decision as _append_decision


def persist_and_stream_node(state: dict[str, Any]) -> dict[str, Any]:
    outcome = state.get("runtime_outcome") if isinstance(state.get("runtime_outcome"), dict) else {}
    return {
        "decision_chain": _append_decision(
            state,
            stage="persist_and_stream",
            decision="checkpoint_and_public_projection_boundary_reached",
            details={
                "runtime_outcome_type": outcome.get("outcome_type"),
                "has_final_answer": bool(state.get("current_final_answer")),
                "has_interaction_contract": bool(state.get("response_contract")),
                "semantic_replan": False,
            },
        )
    }
