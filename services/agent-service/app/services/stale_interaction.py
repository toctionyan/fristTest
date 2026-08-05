from __future__ import annotations

"""Pure public projection for rejected stale transaction controls.

The projection is intentionally independent from LangGraph and the AgentService
orchestrator. It expires the submitted control, exposes the canonical current
focus when one exists, and never turns a stale legacy pointer into authority.
"""

from typing import Any

from app.schemas.chat_schema import ChatResponse
from agent_core.transaction.focus import get_focused_draft_id
from agent_core.transaction.interaction import (
    INTERACTION_SCHEMA_VERSION,
    interaction_response_contract,
    pending_transaction_summaries_from_state,
)


def build_stale_interaction_response(
    thread_id: str,
    *,
    include_debug: bool,
    reason: str,
    interaction_id: str | None = None,
    latest_state: dict[str, Any] | None = None,
) -> ChatResponse:
    message = "该确认已失效或已处理，未执行任何业务写操作。"
    latest = dict(latest_state or {})
    focused_draft_id = get_focused_draft_id(latest)
    current_contract = interaction_response_contract(latest) if latest else None
    current_interaction = (
        dict(current_contract.get("interaction") or {})
        if isinstance(current_contract, dict) and isinstance(current_contract.get("interaction"), dict)
        else None
    )
    if current_interaction is not None and str(current_interaction.get("interaction_id") or "") == str(interaction_id or ""):
        current_interaction = None

    state: dict[str, Any] = {
        "phase": "done",
        "status": "ConfirmationExpired",
        "transaction_error": "STALE_INTERACTION",
    }
    if focused_draft_id:
        state["focused_draft_id"] = focused_draft_id
        state["active_draft_id"] = focused_draft_id
    if latest:
        state["pending_transactions"] = pending_transaction_summaries_from_state(latest)
    if include_debug:
        state["debug_confirmation_error"] = {"reason": reason}

    return ChatResponse(
        type="interaction_required" if current_interaction is not None else "answer",
        thread_id=thread_id,
        answer=message if current_interaction is None else None,
        message=(
            str((current_contract or {}).get("message") or "请使用最新事务卡片继续办理。")
            if current_interaction is not None else None
        ),
        interaction=current_interaction,
        interaction_update={
            "schema_version": INTERACTION_SCHEMA_VERSION,
            "interaction_id": str(interaction_id or "") or None,
            "kind": "transaction",
            "lifecycle": "expired",
            "message": message,
        } if interaction_id else None,
        state=state,
        error="STALE_INTERACTION",
    )


__all__ = ["build_stale_interaction_response"]
