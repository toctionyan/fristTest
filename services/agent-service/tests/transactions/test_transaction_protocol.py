from __future__ import annotations

from pathlib import Path

from agent_core.ledger import append_entries, ledger_cards, offer_entry
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.transaction import ensure_transaction_draft, transition_draft


def _offer(*, reason: str = "质量问题", version: int = 1):
    return offer_entry(
        action_id="create_refund",
        operation="APPLY_REFUND",
        target_handle="h_order:10002",
        input_values={"reason": reason, "expected_version": version},
        preview={"decision": "ALLOWED", "snapshot": {"version": version}, "message": "display text"},
        scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"},
        turn=1,
        label="退款申请",
    )


def _persist_authorization_draft(
    store,
    *,
    draft_id: str,
    command_digest: str,
    confirmation_id: str,
    action_id: str = "create_refund",
    projection: dict | None = None,
):
    durable_projection = dict(projection or {})
    durable_projection.update({
        "kind": "offer",
        "handle": draft_id,
        "draft_id": draft_id,
        "draft_revision": 1,
        "draft_state": "AWAITING_AUTHORIZATION",
        "action_id": action_id,
        "command_digest": command_digest,
        "confirmation_id": confirmation_id,
        "confirmation_version": int(durable_projection.get("confirmation_version") or 1),
    })
    return store.create_draft(
        draft_id=draft_id,
        tenant_id="tenant-a",
        user_id="u001",
        thread_id="t001",
        draft_revision=1,
        draft_state="AWAITING_AUTHORIZATION",
        action_id=action_id,
        command_digest=command_digest,
        command_envelope=durable_projection.get("business_command_envelope"),
        projection=durable_projection,
    )


def _prepare_reconciliation_attempt(
    store,
    *,
    order,
    grant_id: str,
    attempt_id: str,
    confirmation_id: str,
    client_request_id: str,
    idempotency_key: str,
):
    offer = offer_entry(
        action_id="create_refund", operation="APPLY_REFUND", target_handle=order["handle"],
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}},
        scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"},
        turn=1, label="退款申请",
    )
    offer["business_command_envelope"] = {
        "contract": "business_adapter.commit@1", "method": "POST", "path": "/refunds",
        "action_id": "create_refund", "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002", "order_id": "10002"},
        "payload": {"reason": "质量问题", "expected_version": 1},
        "actor_scope": {"tenant_id": "tenant-a", "user_id": "u001"},
    }
    offer = transition_draft(offer, "AWAITING_AUTHORIZATION")
    offer["confirmation_id"] = confirmation_id
    offer["confirmation_version"] = 1
    offer["authority_revision"] = 1
    offer["authority_protocol"] = "ui-authority@1"
    offer = ensure_transaction_draft(offer)
    _persist_authorization_draft(
        store,
        draft_id=offer["draft_id"],
        command_digest=offer["command_digest"],
        confirmation_id=confirmation_id,
        projection=offer,
    )
    store.issue_grant(
        grant_id=grant_id, tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
        confirmation_id=confirmation_id, client_request_id=client_request_id, actor_id="u001", actor_role="customer",
    )
    started = store.reserve_grant_and_start_attempt(
        grant_id=grant_id, attempt_id=attempt_id, tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], action_id="create_refund",
        command_digest=offer["command_digest"], idempotency_key=idempotency_key, canonical_payload=offer["command_payload"],
        business_command_envelope=offer["business_command_envelope"], draft_projection=offer,
    )
    assert started["reserved"] is True and started["created"] is True
    store.transition_attempt(attempt_id, state="SUBMISSION_UNKNOWN", error="timeout")
    store.advance_draft(
        offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"],
        current_attempt_id=attempt_id,
    )
    projected = transition_draft(offer, "SUBMISSION_UNKNOWN")
    projected["active_grant_id"] = grant_id
    projected["commit_attempt_id"] = attempt_id
    return projected


def test_offer_is_canonical_transaction_draft_and_legacy_fields_are_output_only():
    offer = _offer()
    assert offer["draft_id"] == offer["handle"]
    assert offer["draft_state"] == "READY"
    assert "status" not in offer
    assert "action_state" not in offer

    # Legacy values are accepted only while reading historical rows, then are
    # removed from the canonical runtime offer.
    legacy = dict(offer)
    legacy["status"] = "ready"
    legacy["action_state"] = "commit_failed"
    normalized = ensure_transaction_draft(legacy, previous=offer)
    assert normalized["draft_state"] == "READY"
    assert "status" not in normalized
    assert "action_state" not in normalized

    # Customer-facing ledger cards retain the historical display shape through
    # an immediate projection rather than persisted runtime fields.
    cards = ledger_cards([normalized], scope=normalized["scope"])
    assert cards["offers"][0]["status"] == "ready"


def test_effect_bearing_change_bumps_revision_and_invalidates_old_grant():
    first = _offer(reason="质量问题", version=1)
    changed = dict(first)
    changed["input_values"] = {"reason": "收到商品破损", "expected_version": 1}
    second = ensure_transaction_draft(changed, previous=first)
    assert second["draft_revision"] == first["draft_revision"] + 1
    assert second["command_digest"] != first["command_digest"]
    assert second["grant_invalidated_reason"] == "effect_bearing_payload_changed"


def test_display_only_change_does_not_bump_revision_or_digest():
    first = _offer()
    changed = dict(first)
    changed["label"] = "退款申请（界面改名）"
    changed["preview"] = {**changed["preview"], "message": "different UI copy"}
    second = ensure_transaction_draft(changed, previous=first)
    assert second["draft_revision"] == first["draft_revision"]
    assert second["command_digest"] == first["command_digest"]


def test_append_entries_discards_legacy_projection_conflicts():
    first = _offer()
    legacy = dict(first)
    legacy["status"] = "ready"
    legacy["action_state"] = "commit_failed"
    ledger = append_entries([first], [legacy])
    row = next(item for item in ledger if item["handle"] == first["handle"])
    assert row["draft_state"] == "READY"
    assert "status" not in row and "action_state" not in row


def test_lifecycle_store_reserves_only_one_grant_and_persists_attempt(tmp_path: Path):
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    _persist_authorization_draft(
        store, draft_id="h_offer:1", command_digest="digest", confirmation_id="confirm-1"
    )
    grant = store.issue_grant(
        grant_id="grant-1", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:1", draft_revision=1, command_digest="digest", confirmation_id="confirm-1",
        client_request_id="client-1", actor_id="u001", actor_role="customer",
    )
    assert grant["state"] == "ISSUED"
    first = store.reserve_grant_and_start_attempt(
        grant_id="grant-1", attempt_id="attempt-1", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",
        idempotency_key="idem-1", canonical_payload={"action": "create_refund"},
    )
    assert first["reserved"] is True and first["created"] is True
    duplicate = store.reserve_grant_and_start_attempt(
        grant_id="grant-1", attempt_id="attempt-2", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",
        idempotency_key="idem-1", canonical_payload={"action": "create_refund"},
    )
    assert duplicate["reserved"] is False and duplicate["created"] is False
    assert duplicate["attempt"]["attempt_id"] == "attempt-1"
    assert store.list_reconcilable_attempts(tenant_id="tenant-a", user_id="u001", thread_id="t001")[0]["attempt_id"] == "attempt-1"


def test_atomic_grant_reservation_creates_recoverable_attempt_and_rejects_expiry(tmp_path: Path):
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    _persist_authorization_draft(
        store, draft_id="h_offer:1", command_digest="digest", confirmation_id="confirm-atomic"
    )
    store.issue_grant(
        grant_id="grant-atomic", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:1", draft_revision=1, command_digest="digest", confirmation_id="confirm-atomic",
        client_request_id="client-atomic", actor_id="u001", actor_role="customer",
    )
    first = store.reserve_grant_and_start_attempt(
        grant_id="grant-atomic", attempt_id="attempt-atomic", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",
        idempotency_key="idem-atomic", canonical_payload={"action": "create_refund"},
    )
    assert first["reserved"] is True and first["created"] is True
    assert first["attempt"]["state"] == "STARTED"

    duplicate = store.reserve_grant_and_start_attempt(
        grant_id="grant-atomic", attempt_id="attempt-duplicate", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",
        idempotency_key="idem-atomic", canonical_payload={"action": "create_refund"},
    )
    assert duplicate["reserved"] is False and duplicate["created"] is False
    assert duplicate["attempt"]["attempt_id"] == "attempt-atomic"

    _persist_authorization_draft(
        store, draft_id="h_offer:2", command_digest="digest-2", confirmation_id="confirm-expired"
    )
    store.issue_grant(
        grant_id="grant-expired", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:2", draft_revision=1, command_digest="digest-2", confirmation_id="confirm-expired",
        client_request_id="client-expired", actor_id="u001", actor_role="customer",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    expired = store.reserve_grant_and_start_attempt(
        grant_id="grant-expired", attempt_id="attempt-expired", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id="h_offer:2", draft_revision=1, action_id="create_refund", command_digest="digest-2",
        idempotency_key="idem-expired", canonical_payload={"action": "create_refund"},
    )
    assert expired["reserved"] is False and expired["created"] is False
    assert expired["grant"]["state"] == "EXPIRED"
    assert store.get_attempt("attempt-expired") is None


def test_transaction_attempt_does_not_write_legacy_lifecycle_mirrors(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from agent_core.persistence.action_lifecycle_store import ActionRunStore, IdempotencyStore, OutboxStore
    from agent_core.transaction import coordinator

    db_path = tmp_path / "agent.db"
    provider = SimpleNamespace(
        transactions=TransactionLifecycleStore(db_path),
        idempotency=IdempotencyStore(db_path),
        action_runs=ActionRunStore(db_path),
        outbox=OutboxStore(db_path),
    )
    monkeypatch.setattr(coordinator, "get_store_provider", lambda: provider)

    offer = _offer()
    state = {"current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "t001"}
    authority = {"actor_id": "u001", "actor_role": "customer", "client_request_id": "client-1"}
    authority = coordinator.issue_grant_for_authority(state=state, offer=offer, authority=authority)
    reservation, started = coordinator.reserve_grant_and_start_attempt(state=state, offer=offer, authority=authority)
    assert reservation["reserved"] is True and started["created"] is True
    attempt = started["attempt"]
    assert provider.transactions.get_attempt(attempt["attempt_id"])["state"] == "STARTED"
    assert provider.idempotency.get(attempt["idempotency_key"]) is None
    assert provider.action_runs.find_by_idempotency_key(attempt["idempotency_key"]) is None
    assert provider.outbox.list_recent() == []

def test_receipt_reconciler_replays_same_idempotency_key_and_backfills_receipt(tmp_path: Path, monkeypatch):
    from agent_core.ledger import artifact_entry, find_handle
    from agent_core.composition import get_runtime_registry
    from agent_core.lifecycle import nodes

    get_runtime_registry()
    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"}
    order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘",
        facts={"order_id": "10002", "product_name": "机械键盘", "version": 1},
        scope=scope, turn=1, source="test", freshness_version=1,
    )
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _prepare_reconciliation_attempt(
        store, order=order, grant_id="grant-reconcile", attempt_id="attempt-reconcile",
        confirmation_id="confirm-reconcile", client_request_id="client-reconcile", idempotency_key="idem-reconcile",
    )

    calls: list[str] = []
    def replay(_state, envelope, *, idempotency_key=None):
        assert envelope["path"] == "/refunds"
        calls.append(str(idempotency_key))
        return {"success": True, "data": {"refund_id": "R-10002", "version": 1}}

    monkeypatch.setattr(nodes, "transaction_store", lambda: store)
    monkeypatch.setattr(nodes, "_execute_business_command_envelope", replay)
    update = nodes.reconcile_submission_node({
        "current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "t001",
        "turn_index": 2, "artifact_ledger": [order, offer],
    })
    assert update is not None
    assert calls == ["idem-reconcile"]
    updated_offer = find_handle(update["artifact_ledger"], offer["handle"], scope=scope, allowed_kinds={"offer"}, active_only=False)
    assert updated_offer and updated_offer["draft_state"] == "COMMITTED"
    receipts = [row for row in update["artifact_ledger"] if row.get("kind") == "receipt"]
    assert len(receipts) == 1 and receipts[0]["receipt_state"] == "SUCCESS"
    assert store.get_attempt("attempt-reconcile")["state"] == "ACKED"
    assert store.get_grant("grant-reconcile")["state"] == "CONSUMED"


def test_reconciler_keeps_unknown_submission_read_only_without_receipt(tmp_path: Path, monkeypatch):
    from agent_core.ledger import artifact_entry, find_handle
    from agent_core.lifecycle import nodes

    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"}
    order = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘",
        facts={"order_id": "10002", "product_name": "机械键盘", "version": 1},
        scope=scope, turn=1, source="test", freshness_version=1,
    )
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _prepare_reconciliation_attempt(
        store, order=order, grant_id="grant-unknown", attempt_id="attempt-unknown",
        confirmation_id="confirm-unknown", client_request_id="client-unknown", idempotency_key="idem-unknown",
    )

    monkeypatch.setattr(nodes, "transaction_store", lambda: store)
    monkeypatch.setattr(nodes, "_execute_business_command_envelope", lambda *_args, **_kwargs: {"success": False, "code": 504, "error": "network timeout"})
    update = nodes.reconcile_submission_node({
        "current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "t001",
        "turn_index": 2, "artifact_ledger": [order, offer],
    })
    assert update is not None
    row = find_handle(update["artifact_ledger"], offer["handle"], scope=scope, allowed_kinds={"offer"}, active_only=False)
    assert row and row["draft_state"] == "SUBMISSION_UNKNOWN"
    assert not [entry for entry in update["artifact_ledger"] if entry.get("kind") == "receipt"]
    assert store.get_attempt("attempt-unknown")["state"] == "SUBMISSION_UNKNOWN"


def test_sqlalchemy_transaction_lifecycle_store_supports_atomic_grant_attempt(tmp_path: Path):
    from agent_core.persistence.database_settings import DatabaseSettings
    from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider

    db_file = tmp_path / "agent-sqlalchemy.db"
    provider = build_sqlalchemy_store_provider(
        DatabaseSettings(
            backend="sqlite",
            database_url=f"sqlite:///{db_file}",
            sqlite_path=db_file,
            create_schema=True,
        )
    )
    try:
        _persist_authorization_draft(
            provider.transactions, draft_id="h_offer:1", command_digest="digest", confirmation_id="confirm-sql"
        )
        provider.transactions.issue_grant(
            grant_id="grant-sql", tenant_id="tenant-a", user_id="u001", thread_id="t001",
            draft_id="h_offer:1", draft_revision=1, command_digest="digest", confirmation_id="confirm-sql",
            client_request_id="client-sql", actor_id="u001", actor_role="customer",
        )
        first = provider.transactions.reserve_grant_and_start_attempt(
            grant_id="grant-sql", attempt_id="attempt-sql", tenant_id="tenant-a", user_id="u001", thread_id="t001",
            draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",
            idempotency_key="idem-sql", canonical_payload={"action": "create_refund"},
        )
        duplicate = provider.transactions.reserve_grant_and_start_attempt(
            grant_id="grant-sql", attempt_id="attempt-sql-2", tenant_id="tenant-a", user_id="u001", thread_id="t001",
            draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",
            idempotency_key="idem-sql", canonical_payload={"action": "create_refund"},
        )
        assert first["reserved"] is True and first["created"] is True
        assert duplicate["reserved"] is False and duplicate["created"] is False
        assert duplicate["attempt"]["attempt_id"] == "attempt-sql"
    finally:
        provider.close()
