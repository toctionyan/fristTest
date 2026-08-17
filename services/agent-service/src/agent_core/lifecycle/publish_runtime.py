from __future__ import annotations

"""Final persistence and public-stream boundary for the generic lifecycle graph.

LangGraph persists the merged state after this node through the configured
checkpointer.  HTTP/SSE adapters publish only the resulting public projection
after the graph returns.  This node deliberately does not re-render, re-plan or
write business state; it creates one auditable boundary between finalization and
transport delivery.
"""

from copy import deepcopy
from typing import Any

from agent_core.ledger import append_entries, find_handle, scope_for_state
from agent_core.runtime.node_support import append_decision as _append_decision


def _released_transaction_target_ledger(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Persist visibility for the exact business target rendered in a transaction card.

    A transaction interaction visibly renders its verified target label, but the
    opaque target Artifact previously remained internal while only the Offer
    crossed the presentation boundary. That made a later reference such as
    ``它`` fail after the Offer was explicitly dismissed, even though the user
    had just seen the business target.

    This projection records presentation provenance on that exact scoped
    Artifact only. It does not keep the Offer active, create authority, choose a
    target from language, or authorize a business write; downstream reference,
    capability and transaction gates remain unchanged.
    """
    ledger = list(state.get("artifact_ledger") or [])
    response = state.get("response_contract") if isinstance(state.get("response_contract"), dict) else {}
    interaction = response.get("interaction") if isinstance(response.get("interaction"), dict) else {}
    if str(interaction.get("kind") or "") != "transaction":
        return ledger
    control = interaction.get("control") if isinstance(interaction.get("control"), dict) else {}
    target_handle = str(control.get("target_handle") or "").strip()
    interaction_id = str(interaction.get("interaction_id") or "").strip()
    if not target_handle or not interaction_id:
        return ledger
    target = find_handle(
        ledger,
        target_handle,
        scope=scope_for_state(state),
        allowed_kinds={"artifact"},
        active_only=True,
    )
    if target is None:
        return ledger
    visible_target = deepcopy(target)
    visible_target["presentation_origin"] = {
        "origin": "customer_transaction_target",
        "source_turn": int(state.get("turn_index") or 0),
        "source_result_handle": interaction_id,
        "source_effect_id": None,
    }
    visible_target["updated_turn"] = int(state.get("turn_index") or 0)
    return append_entries(ledger, [visible_target])


def persist_and_stream_node(state: dict[str, Any]) -> dict[str, Any]:
    outcome = state.get("runtime_outcome") if isinstance(state.get("runtime_outcome"), dict) else {}
    released_ledger = _released_transaction_target_ledger(state)
    return {
        "artifact_ledger": released_ledger,
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
        ),
    }
