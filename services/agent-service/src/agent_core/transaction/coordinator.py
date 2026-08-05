from __future__ import annotations

"""Durable Agent-side transaction coordination helpers.

The coordinator never decides whether a business operation is allowed.  It
records the Agent-side protocol around a Business Service command so that an
ambiguous network outcome can be reconciled safely with the same idempotency
key and canonical payload.
"""

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from agent_core.transaction.model import canonical_command_payload, command_digest_for_offer
from agent_core.transaction.failure import classify_business_failure


def get_store_provider():
    """Lazy provider lookup keeps pure protocol imports graph-independent."""
    from agent_core.persistence.store_provider import get_store_provider as provider
    return provider()


def transaction_store(state: dict[str, Any] | None = None):
    injected = (state or {}).get("_transaction_repository")
    if injected is not None:
        return injected
    return get_store_provider().transactions


def stable_command_id(state: dict[str, Any], offer: dict[str, Any]) -> str:
    """Stable command identity for every business mutation.

    A command id binds one tenant/user/draft/effect snapshot.  It is distinct
    from a UI confirmation token and survives a transport retry, so the Agent
    and Business Service can reconcile the same command without guessing.
    """
    digest = str(offer.get("command_digest") or command_digest_for_offer(offer))
    raw = ":".join(
        [
            "customer-agent-command",
            "command@1",
            str(state.get("current_tenant_id") or "default"),
            str(state.get("current_user_id") or ""),
            str(offer.get("draft_id") or offer.get("handle") or ""),
            digest,
        ]
    )
    return "cmd:" + sha256(raw.encode("utf-8")).hexdigest()


def stable_idempotency_key(state: dict[str, Any], offer: dict[str, Any]) -> str:
    command_id = str(offer.get("command_id") or stable_command_id(state, offer))
    raw = ":".join(["customer-agent-idempotency", "idempotency@1", command_id])
    return sha256(raw.encode("utf-8")).hexdigest()


def _scope(state: dict[str, Any], offer: dict[str, Any]) -> dict[str, str]:
    scope = offer.get("scope") if isinstance(offer.get("scope"), dict) else {}
    return {
        "tenant_id": str(scope.get("tenant_id") or state.get("current_tenant_id") or "default"),
        "user_id": str(scope.get("user_id") or state.get("current_user_id") or ""),
        "thread_id": str(scope.get("thread_id") or state.get("current_thread_id") or ""),
    }


def _grant_expiry(offer: dict[str, Any]) -> str | None:
    """Use the offer TTL as the upper bound for Grant usability."""
    raw = offer.get("expires_at")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _draft_projection(offer: dict[str, Any]) -> dict[str, Any]:
    keys=("kind","handle","draft_id","draft_revision","draft_state","label","action_id","operation","target_handle","input_values","preview","required_inputs","scope","expires_at","created_at","updated_at","transaction_schema_version","transaction_contract_version","operation_capability_id","operation_capability_version","operation_capability_digest","operation_capability_snapshot","command_id","command_digest","business_command_envelope")
    return {key: offer.get(key) for key in keys if key in offer}


def persist_draft_from_offer(*, state: dict[str, Any], offer: dict[str, Any], draft_state: str | None = None, active_grant_id: str | None = None, current_attempt_id: str | None = None) -> dict[str, Any]:
    scope=_scope(state, offer)
    return transaction_store(state).create_draft(
        draft_id=str(offer.get("draft_id") or offer.get("handle") or ""),
        tenant_id=scope["tenant_id"], user_id=scope["user_id"], thread_id=scope["thread_id"],
        draft_revision=int(offer.get("draft_revision") or 1),
        draft_state=str(draft_state or offer.get("draft_state") or "READY"),
        action_id=str(offer.get("action_id") or ""),
        command_digest=str(offer.get("command_digest") or command_digest_for_offer(offer)),
        command_envelope=dict(offer.get("business_command_envelope") or {}) or None,
        projection=_draft_projection(offer),
        active_grant_id=active_grant_id if active_grant_id is not None else str(offer.get("active_grant_id") or "") or None,
        current_attempt_id=current_attempt_id if current_attempt_id is not None else str(offer.get("commit_attempt_id") or "") or None,
    )


def record_transaction_receipt(*, state: dict[str, Any], offer: dict[str, Any], attempt_id: str | None, receipt_handle: str | None, receipt_state: str, business_result: dict[str, Any], store: Any | None = None) -> dict[str, Any]:
    scope=_scope(state, offer)
    # Receipts record the canonical command target.  Created-resource details
    # remain inside the immutable business result and are projected by the
    # OperationPlugin; transaction coordination never guesses resource ids from
    # domain-specific response field names.
    envelope = offer.get("business_command_envelope") if isinstance(offer.get("business_command_envelope"), dict) else {}
    target = envelope.get("target") if isinstance(envelope.get("target"), dict) else {}
    resource_id = str(target.get("resource_id") or "") or None
    repository = store or transaction_store(state)
    return repository.record_receipt(
        receipt_id=f"receipt:{uuid4().hex}", tenant_id=scope["tenant_id"], user_id=scope["user_id"], thread_id=scope["thread_id"],
        draft_id=str(offer.get("draft_id") or offer.get("handle") or ""), attempt_id=attempt_id, receipt_handle=receipt_handle,
        receipt_state=receipt_state, business_result=dict(business_result or {}), business_resource_id=resource_id,
    )


def issue_grant_for_authority(*, state: dict[str, Any], offer: dict[str, Any], authority: dict[str, Any]) -> dict[str, Any]:
    scope = _scope(state, offer)
    grant_id = str(authority.get("grant_id") or f"grant:{uuid4().hex}")
    digest = str(offer.get("command_digest") or command_digest_for_offer(offer))
    store = transaction_store(state)
    persist_draft_from_offer(state=state, offer=offer, draft_state=str(offer.get("draft_state") or "AWAITING_AUTHORIZATION"))
    record = store.issue_grant(
        grant_id=grant_id,
        tenant_id=scope["tenant_id"],
        user_id=scope["user_id"],
        thread_id=scope["thread_id"],
        draft_id=str(offer.get("draft_id") or offer.get("handle") or ""),
        draft_revision=int(offer.get("draft_revision") or 1),
        command_digest=digest,
        confirmation_id=str(offer.get("confirmation_id") or ""),
        client_request_id=str(authority.get("client_request_id") or ""),
        actor_id=str(authority.get("actor_id") or ""),
        actor_role=str(authority.get("actor_role") or ""),
        expires_at=_grant_expiry(offer),
    )
    authority.update(
        {
            "grant_id": str(record.get("grant_id") or grant_id),
            "draft_id": str(offer.get("draft_id") or offer.get("handle") or ""),
            "draft_revision": int(offer.get("draft_revision") or 1),
            "command_digest": digest,
            "grant_state": str(record.get("state") or "ISSUED"),
            "expires_at": record.get("expires_at"),
        }
    )
    return authority


def reserve_grant_and_start_attempt(*, state: dict[str, Any], offer: dict[str, Any], authority: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    store = transaction_store(state)
    grant_id = str(authority.get("grant_id") or "")
    key = stable_idempotency_key(state, offer)
    attempt_id = f"attempt:{uuid4().hex}"
    scope = _scope(state, offer)
    lifecycle = store.reserve_grant_and_start_attempt(
        grant_id=grant_id,
        attempt_id=attempt_id,
        tenant_id=scope["tenant_id"],
        user_id=scope["user_id"],
        thread_id=scope["thread_id"],
        draft_id=str(offer.get("draft_id") or offer.get("handle") or ""),
        draft_revision=int(offer.get("draft_revision") or 1),
        action_id=str(offer.get("action_id") or ""),
        command_digest=str(offer.get("command_digest") or command_digest_for_offer(offer)),
        idempotency_key=key,
        canonical_payload=canonical_command_payload(offer),
        business_command_envelope=dict(offer.get("business_command_envelope") or {}) or None,
        draft_projection=_draft_projection(offer),
    )
    attempt = dict(lifecycle.get("attempt") or {})
    return (
        {"reserved": bool(lifecycle.get("reserved")), "grant": dict(lifecycle.get("grant") or {})},
        {"created": bool(lifecycle.get("created")), "attempt": attempt},
    )
