from __future__ import annotations

"""Deterministic adapter command construction for transaction snapshots.

This module owns no lifecycle state and grants no authority.  It translates an
already-selected operation, verified target and actor scope into the exact
Business Service command that the Transaction Repository will bind to a Draft.
"""

from typing import Any

from agent_core.business import ActorContext
from agent_core.modules import current_runtime_registry
from agent_core.transaction.coordinator import stable_command_id


def actor_context_from_state(state: dict[str, Any]) -> ActorContext:
    permissions = tuple(str(item) for item in (state.get("actor_permissions") or []) if str(item))
    return ActorContext(
        user_id=str(state.get("current_user_id") or ""),
        role=str(state.get("current_role") or "customer"),
        tenant_id=str(state.get("current_tenant_id") or "") or None,
        subject_user_id=str(state.get("current_subject") or state.get("current_user_id") or "") or None,
        subject=str(state.get("current_subject") or "") or None,
        permissions=permissions,
    )


def build_business_command_envelope(
    state: dict[str, Any],
    offer: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    """Build the immutable adapter command before an authority card is exposed."""
    action = str(offer.get("action_id") or "")
    registry = current_runtime_registry()
    plugin = registry.operations.get(action)
    if plugin is None or action not in set(registry.preparable_action_ids()):
        raise ValueError(f"未实现的业务动作：{action}")
    target_id = str(target.get("resource_id") or "")
    commit_target = {
        "resource_type": str(target.get("resource_type") or ""),
        "resource_id": target_id,
    }
    envelope = plugin.build_business_command_envelope(
        actor=actor_context_from_state(state),
        target=commit_target,
        input_values=dict(offer.get("input_values") or {}),
        preview=offer.get("preview") if isinstance(offer.get("preview"), dict) else None,
    )
    envelope["command_id"] = str(offer.get("command_id") or stable_command_id(state, offer))
    return envelope
