from __future__ import annotations

from pathlib import Path

helper = Path(__file__).with_name("stage8_round5_chain_patch.py")
namespace = {"__name__": "__main__", "__file__": str(helper)}
exec(compile(helper.read_text(encoding="utf-8"), str(helper), "exec"), namespace, namespace)

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} patch anchor mismatch: {count}")
    write(path, text.replace(old, new))


def replace_block(path: str, start: str, end: str | None, new_block: str, *, label: str) -> None:
    text = read(path)
    if text.count(start) != 1:
        raise SystemExit(f"{label} start anchor mismatch: {text.count(start)}")
    start_index = text.index(start)
    end_index = len(text) if end is None else text.index(end, start_index)
    write(path, text[:start_index] + new_block + text[end_index:])


# ---------------------------------------------------------------------------
# A Grant that was correctly bound to a Draft must be revoked if that canonical
# Draft later ceases to be an authorization candidate. Caller-side cross-binding
# errors remain fail-closed without mutating the otherwise valid Grant.
# ---------------------------------------------------------------------------
for repository_path in (
    "services/agent-service/src/agent_core/persistence/action_lifecycle_store.py",
    "services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py",
):
    replace_once(
        repository_path,
        'and "Draft_missing" in reservation_reason',
        'and reservation_reason.startswith("reservation_canonical_Draft_")',
        label=f"{repository_path} stale canonical Draft Grant revocation",
    )


# ---------------------------------------------------------------------------
# Existing Stage-8 tests that only need a closed Draft should use a legal
# non-business terminal state. COMMITTED is reserved for a real Attempt+Receipt
# chain after the Round-5 authority hardening.
# ---------------------------------------------------------------------------
stage8_test = "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
replace_once(
    stage8_test,
    '''        _create(store, offer)\n        store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])\n        store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])\n        _create(store, offer, state="AWAITING_AUTHORIZATION")\n        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"\n        store.advance_draft(offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"])\n        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"\n''',
    '''        _create(store, offer)\n        store.advance_draft(offer["draft_id"], draft_state="REVOKED", draft_revision=offer["draft_revision"])\n        _create(store, offer, state="AWAITING_AUTHORIZATION")\n        assert store.get_draft(offer["draft_id"])["draft_state"] == "REVOKED"\n        store.advance_draft(offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"])\n        assert store.get_draft(offer["draft_id"])["draft_state"] == "REVOKED"\n''',
    label="sqlalchemy terminal fixture uses legal non-business terminal",
)
replace_once(
    stage8_test,
    '''    store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])\n    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])\n    result = store.reserve_grant_and_start_attempt(\n''',
    '''    store.advance_draft(offer["draft_id"], draft_state="REVOKED", draft_revision=offer["draft_revision"])\n    result = store.reserve_grant_and_start_attempt(\n''',
    label="late atomic reserve fixture closes Draft by revocation",
)
replace_once(
    stage8_test,
    '''    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"\n    assert store.get_grant("grant-stage8")["state"] == "REVOKED"\n''',
    '''    assert store.get_draft(offer["draft_id"])["draft_state"] == "REVOKED"\n    assert store.get_grant("grant-stage8")["state"] == "REVOKED"\n''',
    label="late atomic reserve terminal expectation",
)


# ---------------------------------------------------------------------------
# Transaction protocol fixtures are upgraded to the actual authority chain:
# canonical AWAITING Draft -> Grant -> atomic Grant reservation + Attempt.
# Reconciliation fixtures then move the same chain to SUBMISSION_UNKNOWN.
# ---------------------------------------------------------------------------
protocol = "services/agent-service/tests/transactions/test_transaction_protocol.py"
insert_anchor = '''def _offer(*, reason: str = "质量问题", version: int = 1):\n    return offer_entry(\n        action_id="create_refund",\n        operation="APPLY_REFUND",\n        target_handle="h_order:10002",\n        input_values={"reason": reason, "expected_version": version},\n        preview={"decision": "ALLOWED", "snapshot": {"version": version}, "message": "display text"},\n        scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"},\n        turn=1,\n        label="退款申请",\n    )\n\n\n'''
helper_block = insert_anchor + '''def _persist_authorization_draft(\n    store,\n    *,\n    draft_id: str,\n    command_digest: str,\n    confirmation_id: str,\n    action_id: str = "create_refund",\n    projection: dict | None = None,\n):\n    durable_projection = dict(projection or {})\n    durable_projection.update({\n        "kind": "offer",\n        "handle": draft_id,\n        "draft_id": draft_id,\n        "draft_revision": 1,\n        "draft_state": "AWAITING_AUTHORIZATION",\n        "action_id": action_id,\n        "command_digest": command_digest,\n        "confirmation_id": confirmation_id,\n        "confirmation_version": int(durable_projection.get("confirmation_version") or 1),\n    })\n    return store.create_draft(\n        draft_id=draft_id,\n        tenant_id="tenant-a",\n        user_id="u001",\n        thread_id="t001",\n        draft_revision=1,\n        draft_state="AWAITING_AUTHORIZATION",\n        action_id=action_id,\n        command_digest=command_digest,\n        command_envelope=durable_projection.get("business_command_envelope"),\n        projection=durable_projection,\n    )\n\n\ndef _prepare_reconciliation_attempt(\n    store,\n    *,\n    order,\n    grant_id: str,\n    attempt_id: str,\n    confirmation_id: str,\n    client_request_id: str,\n    idempotency_key: str,\n):\n    offer = offer_entry(\n        action_id="create_refund", operation="APPLY_REFUND", target_handle=order["handle"],\n        input_values={"reason": "质量问题", "expected_version": 1},\n        preview={"decision": "ALLOWED", "snapshot": {"version": 1}},\n        scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"},\n        turn=1, label="退款申请",\n    )\n    offer["business_command_envelope"] = {\n        "contract": "business_adapter.commit@1", "method": "POST", "path": "/refunds",\n        "action_id": "create_refund", "operation": "APPLY_REFUND",\n        "target": {"resource_type": "order", "resource_id": "10002", "order_id": "10002"},\n        "payload": {"reason": "质量问题", "expected_version": 1},\n        "actor_scope": {"tenant_id": "tenant-a", "user_id": "u001"},\n    }\n    offer = transition_draft(offer, "AWAITING_AUTHORIZATION")\n    offer["confirmation_id"] = confirmation_id\n    offer["confirmation_version"] = 1\n    offer["authority_revision"] = 1\n    offer["authority_protocol"] = "ui-authority@1"\n    offer = ensure_transaction_draft(offer)\n    _persist_authorization_draft(\n        store,\n        draft_id=offer["draft_id"],\n        command_digest=offer["command_digest"],\n        confirmation_id=confirmation_id,\n        projection=offer,\n    )\n    store.issue_grant(\n        grant_id=grant_id, tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],\n        confirmation_id=confirmation_id, client_request_id=client_request_id, actor_id="u001", actor_role="customer",\n    )\n    started = store.reserve_grant_and_start_attempt(\n        grant_id=grant_id, attempt_id=attempt_id, tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], action_id="create_refund",\n        command_digest=offer["command_digest"], idempotency_key=idempotency_key, canonical_payload=offer["command_payload"],\n        business_command_envelope=offer["business_command_envelope"], draft_projection=offer,\n    )\n    assert started["reserved"] is True and started["created"] is True\n    store.transition_attempt(attempt_id, state="SUBMISSION_UNKNOWN", error="timeout")\n    store.advance_draft(\n        offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"],\n        current_attempt_id=attempt_id,\n    )\n    projected = transition_draft(offer, "SUBMISSION_UNKNOWN")\n    projected["active_grant_id"] = grant_id\n    projected["commit_attempt_id"] = attempt_id\n    return projected\n\n\n'''
replace_once(protocol, insert_anchor, helper_block, label="protocol canonical Draft helpers")

replace_block(
    protocol,
    "def test_lifecycle_store_reserves_only_one_grant_and_persists_attempt(tmp_path: Path):\n",
    "def test_atomic_grant_reservation_creates_recoverable_attempt_and_rejects_expiry(tmp_path: Path):\n",
    '''def test_lifecycle_store_reserves_only_one_grant_and_persists_attempt(tmp_path: Path):\n    store = TransactionLifecycleStore(tmp_path / "agent.db")\n    _persist_authorization_draft(\n        store, draft_id="h_offer:1", command_digest="digest", confirmation_id="confirm-1"\n    )\n    grant = store.issue_grant(\n        grant_id="grant-1", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:1", draft_revision=1, command_digest="digest", confirmation_id="confirm-1",\n        client_request_id="client-1", actor_id="u001", actor_role="customer",\n    )\n    assert grant["state"] == "ISSUED"\n    first = store.reserve_grant_and_start_attempt(\n        grant_id="grant-1", attempt_id="attempt-1", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",\n        idempotency_key="idem-1", canonical_payload={"action": "create_refund"},\n    )\n    assert first["reserved"] is True and first["created"] is True\n    duplicate = store.reserve_grant_and_start_attempt(\n        grant_id="grant-1", attempt_id="attempt-2", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",\n        idempotency_key="idem-1", canonical_payload={"action": "create_refund"},\n    )\n    assert duplicate["reserved"] is False and duplicate["created"] is False\n    assert duplicate["attempt"]["attempt_id"] == "attempt-1"\n    assert store.list_reconcilable_attempts(tenant_id="tenant-a", user_id="u001", thread_id="t001")[0]["attempt_id"] == "attempt-1"\n\n\n''',
    label="protocol atomic-only lifecycle test",
)

replace_block(
    protocol,
    "def test_atomic_grant_reservation_creates_recoverable_attempt_and_rejects_expiry(tmp_path: Path):\n",
    "def test_transaction_attempt_does_not_write_legacy_lifecycle_mirrors(tmp_path: Path, monkeypatch):\n",
    '''def test_atomic_grant_reservation_creates_recoverable_attempt_and_rejects_expiry(tmp_path: Path):\n    store = TransactionLifecycleStore(tmp_path / "agent.db")\n    _persist_authorization_draft(\n        store, draft_id="h_offer:1", command_digest="digest", confirmation_id="confirm-atomic"\n    )\n    store.issue_grant(\n        grant_id="grant-atomic", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:1", draft_revision=1, command_digest="digest", confirmation_id="confirm-atomic",\n        client_request_id="client-atomic", actor_id="u001", actor_role="customer",\n    )\n    first = store.reserve_grant_and_start_attempt(\n        grant_id="grant-atomic", attempt_id="attempt-atomic", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",\n        idempotency_key="idem-atomic", canonical_payload={"action": "create_refund"},\n    )\n    assert first["reserved"] is True and first["created"] is True\n    assert first["attempt"]["state"] == "STARTED"\n\n    duplicate = store.reserve_grant_and_start_attempt(\n        grant_id="grant-atomic", attempt_id="attempt-duplicate", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",\n        idempotency_key="idem-atomic", canonical_payload={"action": "create_refund"},\n    )\n    assert duplicate["reserved"] is False and duplicate["created"] is False\n    assert duplicate["attempt"]["attempt_id"] == "attempt-atomic"\n\n    _persist_authorization_draft(\n        store, draft_id="h_offer:2", command_digest="digest-2", confirmation_id="confirm-expired"\n    )\n    store.issue_grant(\n        grant_id="grant-expired", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:2", draft_revision=1, command_digest="digest-2", confirmation_id="confirm-expired",\n        client_request_id="client-expired", actor_id="u001", actor_role="customer",\n        expires_at="2000-01-01T00:00:00+00:00",\n    )\n    expired = store.reserve_grant_and_start_attempt(\n        grant_id="grant-expired", attempt_id="attempt-expired", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n        draft_id="h_offer:2", draft_revision=1, action_id="create_refund", command_digest="digest-2",\n        idempotency_key="idem-expired", canonical_payload={"action": "create_refund"},\n    )\n    assert expired["reserved"] is False and expired["created"] is False\n    assert expired["grant"]["state"] == "EXPIRED"\n    assert store.get_attempt("attempt-expired") is None\n\n\n''',
    label="protocol atomic expiry test canonical Drafts",
)

replace_block(
    protocol,
    "def test_receipt_reconciler_replays_same_idempotency_key_and_backfills_receipt(tmp_path: Path, monkeypatch):\n",
    "def test_reconciler_keeps_unknown_submission_read_only_without_receipt(tmp_path: Path, monkeypatch):\n",
    '''def test_receipt_reconciler_replays_same_idempotency_key_and_backfills_receipt(tmp_path: Path, monkeypatch):\n    from agent_core.ledger import artifact_entry, find_handle\n    from agent_core.composition import get_runtime_registry\n    from agent_core.lifecycle import nodes\n\n    get_runtime_registry()\n    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"}\n    order = artifact_entry(\n        resource_type="order", resource_id="10002", label="机械键盘",\n        facts={"order_id": "10002", "product_name": "机械键盘", "version": 1},\n        scope=scope, turn=1, source="test", freshness_version=1,\n    )\n    store = TransactionLifecycleStore(tmp_path / "agent.db")\n    offer = _prepare_reconciliation_attempt(\n        store, order=order, grant_id="grant-reconcile", attempt_id="attempt-reconcile",\n        confirmation_id="confirm-reconcile", client_request_id="client-reconcile", idempotency_key="idem-reconcile",\n    )\n\n    calls: list[str] = []\n    def replay(_state, envelope, *, idempotency_key=None):\n        assert envelope["path"] == "/refunds"\n        calls.append(str(idempotency_key))\n        return {"success": True, "data": {"refund_id": "R-10002", "version": 1}}\n\n    monkeypatch.setattr(nodes, "transaction_store", lambda: store)\n    monkeypatch.setattr(nodes, "_execute_business_command_envelope", replay)\n    update = nodes.reconcile_submission_node({\n        "current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "t001",\n        "turn_index": 2, "artifact_ledger": [order, offer],\n    })\n    assert update is not None\n    assert calls == ["idem-reconcile"]\n    updated_offer = find_handle(update["artifact_ledger"], offer["handle"], scope=scope, allowed_kinds={"offer"}, active_only=False)\n    assert updated_offer and updated_offer["draft_state"] == "COMMITTED"\n    receipts = [row for row in update["artifact_ledger"] if row.get("kind") == "receipt"]\n    assert len(receipts) == 1 and receipts[0]["receipt_state"] == "SUCCESS"\n    assert store.get_attempt("attempt-reconcile")["state"] == "ACKED"\n    assert store.get_grant("grant-reconcile")["state"] == "CONSUMED"\n\n\n''',
    label="protocol reconciliation success follows historical authority chain",
)

replace_block(
    protocol,
    "def test_reconciler_keeps_unknown_submission_read_only_without_receipt(tmp_path: Path, monkeypatch):\n",
    "def test_sqlalchemy_transaction_lifecycle_store_supports_atomic_grant_attempt(tmp_path: Path):\n",
    '''def test_reconciler_keeps_unknown_submission_read_only_without_receipt(tmp_path: Path, monkeypatch):\n    from agent_core.ledger import artifact_entry, find_handle\n    from agent_core.lifecycle import nodes\n\n    scope = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"}\n    order = artifact_entry(\n        resource_type="order", resource_id="10002", label="机械键盘",\n        facts={"order_id": "10002", "product_name": "机械键盘", "version": 1},\n        scope=scope, turn=1, source="test", freshness_version=1,\n    )\n    store = TransactionLifecycleStore(tmp_path / "agent.db")\n    offer = _prepare_reconciliation_attempt(\n        store, order=order, grant_id="grant-unknown", attempt_id="attempt-unknown",\n        confirmation_id="confirm-unknown", client_request_id="client-unknown", idempotency_key="idem-unknown",\n    )\n\n    monkeypatch.setattr(nodes, "transaction_store", lambda: store)\n    monkeypatch.setattr(nodes, "_execute_business_command_envelope", lambda *_args, **_kwargs: {"success": False, "code": 504, "error": "network timeout"})\n    update = nodes.reconcile_submission_node({\n        "current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "t001",\n        "turn_index": 2, "artifact_ledger": [order, offer],\n    })\n    assert update is not None\n    row = find_handle(update["artifact_ledger"], offer["handle"], scope=scope, allowed_kinds={"offer"}, active_only=False)\n    assert row and row["draft_state"] == "SUBMISSION_UNKNOWN"\n    assert not [entry for entry in update["artifact_ledger"] if entry.get("kind") == "receipt"]\n    assert store.get_attempt("attempt-unknown")["state"] == "SUBMISSION_UNKNOWN"\n\n\n''',
    label="protocol reconciliation unknown follows historical authority chain",
)

replace_block(
    protocol,
    "def test_sqlalchemy_transaction_lifecycle_store_supports_atomic_grant_attempt(tmp_path: Path):\n",
    None,
    '''def test_sqlalchemy_transaction_lifecycle_store_supports_atomic_grant_attempt(tmp_path: Path):\n    from agent_core.persistence.database_settings import DatabaseSettings\n    from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider\n\n    db_file = tmp_path / "agent-sqlalchemy.db"\n    provider = build_sqlalchemy_store_provider(\n        DatabaseSettings(\n            backend="sqlite",\n            database_url=f"sqlite:///{db_file}",\n            sqlite_path=db_file,\n            create_schema=True,\n        )\n    )\n    try:\n        _persist_authorization_draft(\n            provider.transactions, draft_id="h_offer:1", command_digest="digest", confirmation_id="confirm-sql"\n        )\n        provider.transactions.issue_grant(\n            grant_id="grant-sql", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n            draft_id="h_offer:1", draft_revision=1, command_digest="digest", confirmation_id="confirm-sql",\n            client_request_id="client-sql", actor_id="u001", actor_role="customer",\n        )\n        first = provider.transactions.reserve_grant_and_start_attempt(\n            grant_id="grant-sql", attempt_id="attempt-sql", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n            draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",\n            idempotency_key="idem-sql", canonical_payload={"action": "create_refund"},\n        )\n        duplicate = provider.transactions.reserve_grant_and_start_attempt(\n            grant_id="grant-sql", attempt_id="attempt-sql-2", tenant_id="tenant-a", user_id="u001", thread_id="t001",\n            draft_id="h_offer:1", draft_revision=1, action_id="create_refund", command_digest="digest",\n            idempotency_key="idem-sql", canonical_payload={"action": "create_refund"},\n        )\n        assert first["reserved"] is True and first["created"] is True\n        assert duplicate["reserved"] is False and duplicate["created"] is False\n        assert duplicate["attempt"]["attempt_id"] == "attempt-sql"\n    finally:\n        provider.close()\n''',
    label="protocol sqlalchemy atomic path canonical Draft",
)
