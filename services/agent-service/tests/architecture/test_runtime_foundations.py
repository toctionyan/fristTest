from __future__ import annotations

from pathlib import Path

import pytest

from agent_core.observability.correlation import get_correlation_id, reset_correlation_id, set_correlation_id
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.runtime.profile import RuntimeProfile, get_runtime_profile
from agent_core.storage.repositories.base import ActiveDraftValidationCode, TransactionScope
from agent_core.transaction.active_draft import active_draft_patch, get_active_draft_id


def _store(tmp_path: Path) -> TransactionLifecycleStore:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    store.init_db()
    return store


def _draft(store: TransactionLifecycleStore, *, draft_id: str = "offer:1", tenant: str = "t1", user: str = "u1", thread: str = "thread-1") -> None:
    store.create_draft(
        draft_id=draft_id,
        tenant_id=tenant,
        user_id=user,
        thread_id=thread,
        draft_revision=2,
        draft_state="AWAITING_AUTHORIZATION",
        action_id="create_refund",
        command_digest="digest-1",
        command_envelope={"method": "POST", "path": "/refunds", "body": {"order_id": "10001"}},
        projection={"label": "订单 10001 退款"},
    )






def test_profile_requires_explicit_current_selector(monkeypatch):
    monkeypatch.delenv("APP_PROFILE", raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("LOCAL_DEV", "true")
    assert get_runtime_profile(strict=False) is None
    with pytest.raises(RuntimeError, match="APP_PROFILE is required"):
        get_runtime_profile(strict=True)


def test_profile_accepts_declared_selector(monkeypatch):
    monkeypatch.setenv("APP_PROFILE", "local")
    assert get_runtime_profile(strict=True) is RuntimeProfile.LOCAL


def test_correlation_id_is_validated_and_reset():
    cid = set_correlation_id("too long / contains a forbidden space")
    try:
        assert cid != "too long / contains a forbidden space"
        assert len(cid) <= 64
        assert get_correlation_id() == cid
    finally:
        reset_correlation_id()
    assert get_correlation_id() is None


def test_scope_first_repository_and_receipt_cardinality(tmp_path: Path):
    store = _store(tmp_path)
    _draft(store)
    scope = TransactionScope("t1", "u1")
    assert store.get_draft_for_scope(scope=scope, draft_id="offer:1")
    mismatch = store.validate_active_draft(scope=TransactionScope("t1", "other"), draft_id="offer:1")
    assert mismatch.code is ActiveDraftValidationCode.SCOPE_MISMATCH
    stale = store.validate_active_draft(scope=scope, draft_id="offer:1", expected_revision=3)
    assert stale.code is ActiveDraftValidationCode.REVISION_MISMATCH

    for attempt_id in ("attempt:1", "attempt:2"):
        store.start_attempt(
            attempt_id=attempt_id,
            tenant_id="t1",
            user_id="u1",
            thread_id="thread-1",
            draft_id="offer:1",
            draft_revision=2,
            grant_id="grant:1",
            action_id="create_refund",
            command_digest="digest-1",
            idempotency_key=f"idem:{attempt_id}",
            canonical_payload={"order_id": "10001"},
        )
        store.record_receipt(
            receipt_id=f"receipt:{attempt_id}",
            tenant_id="t1",
            user_id="u1",
            thread_id="thread-1",
            draft_id="offer:1",
            attempt_id=attempt_id,
            receipt_handle=f"handle:{attempt_id}",
            receipt_state="SUCCESS",
            business_result={"success": True, "attempt": attempt_id},
        )

    attempts = store.list_attempts_for_draft(scope=scope, draft_id="offer:1")
    assert {item["attempt_id"] for item in attempts} == {"attempt:1", "attempt:2"}
    assert store.get_receipt_for_attempt(scope=scope, attempt_id="attempt:1")["receipt_id"] == "receipt:attempt:1"
    assert store.get_latest_receipt_for_draft(scope=scope, draft_id="offer:1")["receipt_id"] == "receipt:attempt:2"


def test_active_draft_runtime_writes_only_canonical_pointer():
    patch = active_draft_patch("offer:1")
    assert patch == {"focused_draft_id": "offer:1", "active_draft_id": "offer:1"}
    assert get_active_draft_id({"pending_offer_handle": "legacy:1"}) is None
    assert get_active_draft_id({"active_draft_id": "new:1", "pending_offer_handle": "legacy:1"}) == "new:1"






