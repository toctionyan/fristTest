from __future__ import annotations

"""Generic structured-authority checks for transaction drafts.

This module deliberately owns no domain action list.  Installed modules own
operation definitions; policy metadata is derived from the current runtime
registry at use time so module enablement cannot leave a stale global snapshot.
"""

from dataclasses import dataclass
from typing import Any

from agent_core.modules import current_runtime_registry
from agent_core.transaction.model import DRAFT_READY


UI_CONFIRMED = "ui_confirmed"
UI_REJECTED = "ui_rejected"
UI_AUTHORITY_PROTOCOL = "ui_action_authority@1"


@dataclass(frozen=True)
class ActionPolicy:
    action_id: str
    risk_level: str
    authority_requirement: str  # ui_action_authority | human_review
    description: str


def registered_action_policies() -> dict[str, ActionPolicy]:
    """Project installed-module policies without creating a second authority."""
    return {
        plugin.action_id: ActionPolicy(
            plugin.action_id,
            plugin.risk_level,
            "ui_action_authority",
            f"{plugin.label}；需要一次性 UI 授权。",
        )
        for plugin in current_runtime_registry().operations.all()
    }


def registered_action_policy_ids() -> set[str]:
    return set(registered_action_policies())


def policy_for_action(action_id: str) -> ActionPolicy:
    return registered_action_policies().get(
        str(action_id or ""),
        ActionPolicy(
            str(action_id or "unknown"),
            "high_risk",
            "ui_action_authority",
            "未注册动作默认需要一次性 UI 授权，且业务服务仍可拒绝。",
        ),
    )


def deterministic_offer_readiness(*, offer: dict[str, Any], current_turn: int, target_exists: bool) -> tuple[bool, str]:
    """Validate objective draft lifecycle state without interpreting text.

    A draft is eligible for the gateway only when the runtime observed it become
    ``ready`` in *this* user turn.  The first action request may be in an
    earlier turn and a later turn may only supply a missing field (reason,
    document, date, etc.); requiring action words to reappear would
    strand valid drafts.  The transition itself is safe because the specific
    completion tool validates its current-turn evidence and the actual write
    still requires independent UI authority.
    """
    if not target_exists:
        return False, "action_target_missing"
    if str(offer.get("draft_state") or "").upper() != DRAFT_READY:
        return False, "offer_not_ready"
    if int(offer.get("ready_turn") or 0) != int(current_turn or 0):
        return False, "action_draft_not_transitioned_in_current_turn"
    return True, "ok"


def build_ui_authority(*, payload: dict[str, Any], actor_id: str, actor_role: str, current_revision: int) -> dict[str, Any]:
    """Normalize a UI authority envelope for state and immutable audit storage."""
    return {
        "protocol": UI_AUTHORITY_PROTOCOL,
        "authority_type": str(payload.get("authority_type") or ""),
        "offer_handle": str(payload.get("offer_handle") or ""),
        "action_id": str(payload.get("action_id") or ""),
        "target_handle": str(payload.get("target_handle") or ""),
        "confirmation_id": str(payload.get("confirmation_id") or ""),
        "confirmation_version": int(payload.get("confirmation_version") or 0),
        "conversation_revision": int(payload.get("conversation_revision") or 0),
        "actor_id": str(actor_id or ""),
        "actor_role": str(actor_role or ""),
        "client_request_id": str(payload.get("client_request_id") or ""),
        "comment": str(payload.get("comment") or ""),
        "validated_at_revision": int(current_revision),
        # Bound by the transaction coordinator after it loads the server-side
        # draft.  Client payload never supplies these values.
        "grant_id": "",
        "draft_id": "",
        "draft_revision": 0,
        "command_digest": "",
        "grant_state": "ISSUED",
    }


def validate_ui_authority(*, authority: dict[str, Any] | None, offer: dict[str, Any], current_revision: int) -> tuple[bool, str]:
    """Validate only structural facts; never infer a user's intent from text."""
    row = authority or {}
    if str(row.get("protocol") or "") != UI_AUTHORITY_PROTOCOL:
        return False, "authority_protocol_mismatch"
    if str(row.get("authority_type") or "") != UI_CONFIRMED:
        return False, "authority_not_confirmed"
    if str(row.get("offer_handle") or "") != str(offer.get("handle") or ""):
        return False, "authority_offer_mismatch"
    if str(row.get("action_id") or "") != str(offer.get("action_id") or ""):
        return False, "authority_action_mismatch"
    if str(row.get("target_handle") or "") != str(offer.get("target_handle") or ""):
        return False, "authority_target_mismatch"
    if str(row.get("confirmation_id") or "") != str(offer.get("confirmation_id") or ""):
        return False, "authority_confirmation_id_mismatch"
    if int(row.get("confirmation_version") or 0) != int(offer.get("confirmation_version") or 0):
        return False, "authority_confirmation_version_mismatch"
    if int(row.get("conversation_revision") or 0) != int(offer.get("authority_revision") or current_revision):
        return False, "authority_conversation_revision_mismatch"
    if int(row.get("validated_at_revision") or 0) != int(current_revision):
        return False, "authority_validated_revision_mismatch"
    if str(row.get("draft_id") or "") != str(offer.get("draft_id") or offer.get("handle") or ""):
        return False, "authority_draft_id_mismatch"
    if int(row.get("draft_revision") or 0) != int(offer.get("draft_revision") or 0):
        return False, "authority_draft_revision_mismatch"
    if str(row.get("command_digest") or "") != str(offer.get("command_digest") or ""):
        return False, "authority_command_digest_mismatch"
    if not str(row.get("grant_id") or ""):
        return False, "authority_grant_id_missing"
    if not str(row.get("actor_id") or "") or not str(row.get("client_request_id") or ""):
        return False, "authority_actor_or_request_missing"
    return True, "ok"
