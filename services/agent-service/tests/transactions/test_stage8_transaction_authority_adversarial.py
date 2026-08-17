from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.chat_schema import ActionAuthorityRequest
from app.services.agent_service import AgentService
from agent_core.ledger import append_entries, artifact_entry, find_handle, offer_entry
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.persistence.database_settings import DatabaseSettings
from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider
from agent_core.runtime.outcomes import outcome
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction import transition_draft
from agent_core.transaction.coordinator import issue_grant_for_authority, reserve_grant_and_start_attempt
from agent_core.transaction.deps import TransactionExecutionDeps

SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "stage8-terminal"}


def _offer(*, state: str = "AWAITING_AUTHORIZATION") -> dict:
    row = offer_entry(
        action_id="create_refund", operation="APPLY_REFUND", target_handle="artifact:order:10002",
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}}, scope=SCOPE,
        turn=2, label="退款申请", handle="draft:refund:stage8-terminal",
    )
    row["confirmation_id"] = "confirm-stage8"
    row["confirmation_version"] = 1
    row["authority_revision"] = 2
    return transition_draft(row, state)


def _create(store, offer: dict, *, state: str | None = None) -> dict:
    return store.create_draft(
        draft_id=offer["draft_id"], tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_revision=offer["draft_revision"], draft_state=state or offer["draft_state"], action_id=offer["action_id"],
        command_digest=offer["command_digest"], command_envelope=offer.get("business_command_envelope"), projection=offer,
    )


def _start_transaction_attempt(store, offer: dict, *, client_request_id: str):
    state = {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "_transaction_repository": store,
    }
    authority = issue_grant_for_authority(
        state=state,
        offer=offer,
        authority={
            "actor_id": SCOPE["user_id"],
            "actor_role": "customer",
            "client_request_id": client_request_id,
            "authority_type": "ui_confirmed",
        },
    )
    reservation, started = reserve_grant_and_start_attempt(state=state, offer=offer, authority=authority)
    assert reservation["reserved"] is True
    assert started["created"] is True
    return state, authority, started["attempt"]


def test_sqlite_terminal_draft_rejects_stale_create_and_advance(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    _state, authority, attempt = _start_transaction_attempt(store, offer, client_request_id="terminal-sqlite")
    attempt_id = str(attempt["attempt_id"])
    result = {"success": True, "data": {"refund_id": "R-stage8"}}
    store.record_receipt(
        receipt_id="receipt:sqlite-stage8", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:sqlite-stage8", receipt_state="SUCCESS",
        business_result=result, business_resource_id="R-stage8",
    )
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"], current_attempt_id=attempt_id)
    store.transition_attempt(attempt_id, state="ACKED", business_result=result, receipt_handle="h_receipt:sqlite-stage8")
    store.consume_grant(str(authority["grant_id"]), attempt_id=attempt_id, receipt_handle="h_receipt:sqlite-stage8")
    _create(store, offer, state="AWAITING_AUTHORIZATION")
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    store.advance_draft(offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"])
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"


def test_sqlalchemy_terminal_draft_rejects_stale_create_and_advance(tmp_path: Path) -> None:
    db_file = tmp_path / "agent-sqlalchemy.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
    try:
        store = provider.transactions
        offer = _offer()
        _create(store, offer)
        store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])
        store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
        _create(store, offer, state="AWAITING_AUTHORIZATION")
        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
        store.advance_draft(offer["draft_id"], draft_state="SUBMISSION_UNKNOWN", draft_revision=offer["draft_revision"])
        assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    finally:
        provider.close()


def test_ledger_terminal_offer_cannot_be_reopened_by_stale_projection() -> None:
    committed = _offer(state="COMMITTED")
    stale = _offer(state="AWAITING_AUTHORIZATION")
    stale["updated_turn"] = int(committed.get("updated_turn") or 0) + 1
    ledger = append_entries([committed], [stale])
    row = find_handle(ledger, committed["handle"], scope=SCOPE, allowed_kinds={"offer"}, active_only=False)
    assert row is not None and row["draft_state"] == "COMMITTED"


def test_atomic_reserve_cannot_create_attempt_against_terminal_draft(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.issue_grant(
        grant_id="grant-stage8", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
        confirmation_id=offer["confirmation_id"], client_request_id="client-stage8", actor_id=SCOPE["user_id"], actor_role="customer",
    )
    store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
    result = store.reserve_grant_and_start_attempt(
        grant_id="grant-stage8", attempt_id="attempt-stage8-late", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"],
        thread_id=SCOPE["thread_id"], draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], action_id=offer["action_id"],
        command_digest=offer["command_digest"], idempotency_key="idem-stage8-late", canonical_payload={"action_id": offer["action_id"]},
        business_command_envelope=None, draft_projection=offer,
    )
    assert result["reserved"] is False and result["created"] is False and result["attempt"] == {}
    assert store.get_attempt("attempt-stage8-late") is None
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"
    assert store.get_grant("grant-stage8")["state"] == "REVOKED"


def test_internal_grant_minting_rejects_terminal_canonical_draft(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="REVOKED", draft_revision=offer["draft_revision"])
    state = {"current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"], "current_thread_id": SCOPE["thread_id"], "_transaction_repository": store}
    authority = {"actor_id": SCOPE["user_id"], "actor_role": "customer", "client_request_id": "late-authority-stage8", "authority_type": "ui_confirmed"}
    with pytest.raises(ValueError, match="no longer awaiting authority"):
        issue_grant_for_authority(state=state, offer=offer, authority=authority)
    assert store.list_grants_by_thread(**SCOPE) == []
    assert store.get_draft(offer["draft_id"])["draft_state"] == "REVOKED"


def test_stale_browser_authority_is_rejected_by_durable_terminal_state(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    _create(store, offer)
    store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])
    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])
    stale_values = {"turn_index": 2, "focused_draft_id": offer["draft_id"], "artifact_ledger": [offer]}

    class _Hydrator:
        def values(self, _graph, **_kwargs):
            return dict(stale_values)

    service = AgentService.__new__(AgentService)
    service.transactions = store
    service.checkpoint_hydrator = _Hydrator()
    service._config_for_request = lambda *_args, **_kwargs: {"configurable": {"thread_id": "ignored"}}
    request = ActionAuthorityRequest(
        thread_id=SCOPE["thread_id"], user_id=SCOPE["user_id"], role="customer", tenant_id=SCOPE["tenant_id"],
        decision="approved", authority_type="ui_confirmed", offer_handle=offer["draft_id"], action_id=offer["action_id"],
        target_handle=offer["target_handle"], confirmation_id=offer["confirmation_id"], confirmation_version=1,
        conversation_revision=2, client_request_id="late-browser-stage8",
    )
    assert service._validate_action_authority(object(), request) == "durable_draft_not_awaiting_authority"


def test_duplicate_after_success_receipt_projects_committed_without_business_write(tmp_path: Path, monkeypatch) -> None:
    import agent_core.transaction.commit_runtime as runtime

    store = TransactionLifecycleStore(tmp_path / "agent.db")
    target = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002", "status": "已签收", "version": 1}, scope=SCOPE,
        turn=2, source="test", freshness_version=1, handle="artifact:order:10002",
    )
    envelope = {
        "contract": "business_adapter.commit@1", "method": "POST", "path": "/refunds",
        "action_id": "create_refund", "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002"},
        "input": {"reason": "质量问题", "expected_version": 1},
        "actor_scope": {"tenant_id": SCOPE["tenant_id"], "user_id": SCOPE["user_id"]},
    }
    pending = _offer(state="AWAITING_AUTHORIZATION")
    pending["business_command_envelope"] = envelope
    _create(store, pending, state="AWAITING_AUTHORIZATION")
    _state, authority, attempt = _start_transaction_attempt(store, pending, client_request_id="duplicate-known")
    attempt_id = str(attempt["attempt_id"])
    known_result = {"success": True, "data": {"refund_id": "R-known", "version": 1}}
    store.record_receipt(
        receipt_id="receipt-known", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=pending["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:known", receipt_state="SUCCESS",
        business_result=known_result, business_resource_id="R-known",
    )
    store.advance_draft(pending["draft_id"], draft_state="COMMITTED", draft_revision=pending["draft_revision"], current_attempt_id=attempt_id)
    store.transition_attempt(attempt_id, state="ACKED", business_result=known_result, receipt_handle="h_receipt:known")
    store.consume_grant(str(authority["grant_id"]), attempt_id=attempt_id, receipt_handle="h_receipt:known")
    offer = transition_draft(pending, "AUTHORIZED")
    offer["active_grant_id"] = authority["grant_id"]

    monkeypatch.setattr(runtime, "snapshot_matches_registry", lambda _offer: True)
    monkeypatch.setattr(runtime, "validate_ui_authority", lambda **_kwargs: (True, "ok"))
    monkeypatch.setattr(runtime, "_refresh_offer_preflight", lambda *_args, **_kwargs: ({"success": True}, {"decision": "ALLOWED", "snapshot": {"version": 1}}, []))
    monkeypatch.setattr(runtime, "_build_business_command_envelope", lambda *_args, **_kwargs: dict(envelope))
    monkeypatch.setattr(runtime, "reserve_grant_and_start_attempt", lambda **_kwargs: (
        {"reserved": False, "grant": {"state": "CONSUMED"}},
        {"created": False, "attempt": {"attempt_id": attempt_id, "state": "ACKED", "idempotency_key": str(attempt.get("idempotency_key") or "")}},
    ))
    monkeypatch.setattr(runtime, "_new_resource_artifacts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runtime, "_execute_business_command_envelope", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate must not call Business Service")))

    state = {
        "current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"], "current_thread_id": SCOPE["thread_id"],
        "current_role": "customer", "turn_index": 2, "artifact_ledger": [target, offer], "focused_draft_id": offer["draft_id"],
        "commit_authority": {"grant_id": authority["grant_id"], "command_digest": offer["command_digest"]},
        "action_queue": [], "tool_trace": [], "_transaction_repository": store,
    }
    patch = runtime.commit_action_node(state, deps=TransactionExecutionDeps(business_port=SimpleNamespace(), outcome_factory=outcome))
    assert patch["status"] == "ActionAlreadyCommitted"
    row = find_handle(patch["artifact_ledger"], offer["handle"], scope=SCOPE, allowed_kinds={"offer"}, active_only=False)
    assert row is not None and row["draft_state"] == "COMMITTED"
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTED"



def _challenge_offer(*, confirmation_id: str, confirmation_version: int, authority_revision: int) -> dict:
    row = offer_entry(
        action_id="create_refund",
        operation="APPLY_REFUND",
        target_handle="artifact:order:10002",
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}},
        scope=SCOPE,
        turn=authority_revision,
        label="退款申请",
        handle="draft:refund:stage8-challenge",
    )
    row = transition_draft(row, "AWAITING_AUTHORIZATION")
    row["authority_protocol"] = "ui-authority@1"
    row["authority_requirement"] = "ui_action_authority"
    row["authority_revision"] = authority_revision
    row["confirmation_id"] = confirmation_id
    row["confirmation_version"] = confirmation_version
    row["updated_turn"] = authority_revision
    return row


def test_same_revision_old_authority_challenge_cannot_replace_newer_one(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    newest = _challenge_offer(confirmation_id="confirm-new", confirmation_version=2, authority_revision=10)
    stale = _challenge_offer(confirmation_id="confirm-old", confirmation_version=1, authority_revision=9)
    _create(store, newest)
    _create(store, stale)
    durable = store.get_draft(newest["draft_id"])
    assert durable is not None
    assert durable["projection"]["confirmation_id"] == "confirm-new"
    assert durable["projection"]["confirmation_version"] == 2
    assert durable["projection"]["authority_revision"] == 10


def test_same_revision_committing_cannot_regress_to_awaiting_or_ready(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    _create(store, card)
    store.advance_draft(card["draft_id"], draft_state="COMMITTING", draft_revision=card["draft_revision"], current_attempt_id="attempt-round2")
    assert store.get_draft(card["draft_id"])["draft_state"] == "COMMITTING"
    _create(store, card, state="AWAITING_AUTHORIZATION")
    store.advance_draft(card["draft_id"], draft_state="READY", draft_revision=card["draft_revision"])
    durable = store.get_draft(card["draft_id"])
    assert durable is not None
    assert durable["draft_state"] == "COMMITTING"
    assert durable["current_attempt_id"] == "attempt-round2"


def test_same_revision_effect_digest_change_is_rejected(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    _create(store, card)
    store.advance_draft(card["draft_id"], draft_state="READY", draft_revision=card["draft_revision"], command_digest="tampered-digest")
    durable = store.get_draft(card["draft_id"])
    assert durable is not None
    assert durable["command_digest"] == card["command_digest"]
    assert durable["draft_state"] == "AWAITING_AUTHORIZATION"


def test_new_revision_cannot_replace_inflight_attempt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    _create(store, card)
    store.advance_draft(card["draft_id"], draft_state="COMMITTING", draft_revision=card["draft_revision"], current_attempt_id="attempt-round2")
    newer = dict(card)
    newer["draft_revision"] = int(card["draft_revision"]) + 1
    newer["command_digest"] = "new-effect-digest"
    newer["draft_state"] = "AWAITING_AUTHORIZATION"
    store.create_draft(
        draft_id=newer["draft_id"], tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_revision=newer["draft_revision"], draft_state=newer["draft_state"], action_id=newer["action_id"],
        command_digest=newer["command_digest"], command_envelope=None, projection=newer,
    )
    durable = store.get_draft(card["draft_id"])
    assert durable is not None
    assert durable["draft_revision"] == card["draft_revision"]
    assert durable["draft_state"] == "COMMITTING"
    assert durable["current_attempt_id"] == "attempt-round2"


def test_newer_needs_input_form_cannot_be_replaced_by_stale_form(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    base = offer_entry(
        action_id="create_refund", operation="APPLY_REFUND", target_handle="artifact:order:10002",
        input_values={}, preview={"decision": "NEEDS_INPUT"}, scope=SCOPE, turn=9,
        label="退款申请", handle="draft:refund:stage8-form",
    )
    newer = transition_draft(base, "NEEDS_INPUT")
    newer.update({"input_form_id": "form-new", "input_form_version": 3, "interaction_revision": 9, "updated_turn": 9})
    stale = dict(newer)
    stale.update({"input_form_id": "form-old", "input_form_version": 2, "interaction_revision": 8, "updated_turn": 8})
    _create(store, newer)
    _create(store, stale)
    durable = store.get_draft(newer["draft_id"])
    assert durable is not None
    assert durable["projection"]["input_form_id"] == "form-new"
    assert durable["projection"]["input_form_version"] == 3


def test_requires_review_can_be_explicitly_revoked(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    card = _challenge_offer(confirmation_id="confirm-a", confirmation_version=1, authority_revision=5)
    card = transition_draft(card, "REQUIRES_REVIEW")
    _create(store, card, state="REQUIRES_REVIEW")
    store.advance_draft(card["draft_id"], draft_state="REVOKED", draft_revision=card["draft_revision"])
    assert store.get_draft(card["draft_id"])["draft_state"] == "REVOKED"


def test_sqlalchemy_same_revision_nonterminal_regressions_are_rejected(tmp_path: Path) -> None:
    db_file = tmp_path / "stage8-round2-sqla.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
    try:
        store = provider.transactions
        newest = _challenge_offer(confirmation_id="confirm-new", confirmation_version=2, authority_revision=10)
        stale = _challenge_offer(confirmation_id="confirm-old", confirmation_version=1, authority_revision=9)
        _create(store, newest)
        _create(store, stale)
        assert store.get_draft(newest["draft_id"])["projection"]["confirmation_id"] == "confirm-new"
        store.advance_draft(newest["draft_id"], draft_state="COMMITTING", draft_revision=newest["draft_revision"], current_attempt_id="attempt-sqla")
        _create(store, stale, state="AWAITING_AUTHORIZATION")
        store.advance_draft(newest["draft_id"], draft_state="READY", draft_revision=newest["draft_revision"])
        durable = store.get_draft(newest["draft_id"])
        assert durable["draft_state"] == "COMMITTING"
        assert durable["current_attempt_id"] == "attempt-sqla"
    finally:
        provider.close()



def test_receipt_requires_exact_persisted_attempt_and_grant(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "receipt-binding.db")
    offer = _offer()
    _create(store, offer)
    with pytest.raises(ValueError, match="attempt"):
        store.record_receipt(
            receipt_id="receipt-orphan", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
            draft_id=offer["draft_id"], attempt_id="attempt-missing", receipt_handle="h_receipt:orphan", receipt_state="SUCCESS",
            business_result={"success": True, "data": {"refund_id": "R-orphan"}},
        )
    assert store.get_receipt("receipt-orphan") is None

    _state, _authority, attempt = _start_transaction_attempt(store, offer, client_request_id="receipt-binding")
    attempt_id = str(attempt["attempt_id"])
    with pytest.raises(ValueError, match="attempt"):
        store.record_receipt(
            receipt_id="receipt-wrong-scope", tenant_id="tenant-b", user_id="u999", thread_id="other-thread",
            draft_id="draft:other", attempt_id=attempt_id, receipt_handle="h_receipt:wrong", receipt_state="SUCCESS",
            business_result={"success": True, "data": {"refund_id": "R-wrong"}},
        )
    assert store.get_receipt_by_attempt(attempt_id) is None


def test_acked_attempt_cannot_regress_after_success_receipt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "attempt-monotonic.db")
    offer = _offer()
    _create(store, offer)
    _state, _authority, attempt = _start_transaction_attempt(store, offer, client_request_id="attempt-monotonic")
    attempt_id = str(attempt["attempt_id"])
    result = {"success": True, "data": {"refund_id": "R-acked"}}
    store.record_receipt(
        receipt_id="receipt-acked", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:acked", receipt_state="SUCCESS", business_result=result,
    )
    store.transition_attempt(attempt_id, state="ACKED", business_result=result, receipt_handle="h_receipt:acked")
    store.transition_attempt(attempt_id, state="STARTED", error="late stale worker")
    durable = store.get_attempt(attempt_id)
    assert durable is not None
    assert durable["state"] == "ACKED"
    assert durable["business_result"] == result
    assert durable["receipt_handle"] == "h_receipt:acked"


def test_success_receipt_crash_window_blocks_new_grant_and_attempt(tmp_path: Path) -> None:
    store = TransactionLifecycleStore(tmp_path / "receipt-crash-window.db")
    offer = _offer()
    _create(store, offer)
    state, authority, attempt = _start_transaction_attempt(store, offer, client_request_id="receipt-crash-window")
    attempt_id = str(attempt["attempt_id"])
    assert store.get_draft(offer["draft_id"])["draft_state"] == "COMMITTING"
    store.record_receipt(
        receipt_id="receipt-crash-window", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
        draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:crash-window", receipt_state="SUCCESS",
        business_result={"success": True, "data": {"refund_id": "R-crash-window"}},
    )
    late = dict(authority)
    late["client_request_id"] = "late-replay"
    with pytest.raises(ValueError, match="no longer awaiting authority"):
        issue_grant_for_authority(state=state, offer=offer, authority=late)
    assert len(store.list_grants_by_thread(**SCOPE)) == 1
    assert len(store.list_attempts_for_draft(scope=TransactionScope(**SCOPE), draft_id=offer["draft_id"])) == 1


def test_sqlalchemy_receipt_attempt_binding_and_monotonicity(tmp_path: Path) -> None:
    db_file = tmp_path / "receipt-binding-sqla.db"
    provider = build_sqlalchemy_store_provider(DatabaseSettings(backend="sqlite", database_url=f"sqlite:///{db_file}", sqlite_path=db_file, create_schema=True))
    try:
        store = provider.transactions
        offer = _offer()
        _create(store, offer)
        with pytest.raises(ValueError, match="attempt"):
            store.record_receipt(
                receipt_id="receipt-orphan-sqla", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
                draft_id=offer["draft_id"], attempt_id="attempt-missing", receipt_handle="h_receipt:orphan", receipt_state="SUCCESS",
                business_result={"success": True, "data": {"refund_id": "R-orphan"}},
            )
        _state, _authority, attempt = _start_transaction_attempt(store, offer, client_request_id="sqla-receipt-binding")
        attempt_id = str(attempt["attempt_id"])
        result = {"success": True, "data": {"refund_id": "R-sqla"}}
        store.record_receipt(
            receipt_id="receipt-sqla", tenant_id=SCOPE["tenant_id"], user_id=SCOPE["user_id"], thread_id=SCOPE["thread_id"],
            draft_id=offer["draft_id"], attempt_id=attempt_id, receipt_handle="h_receipt:sqla", receipt_state="SUCCESS", business_result=result,
        )
        store.transition_attempt(attempt_id, state="ACKED", business_result=result, receipt_handle="h_receipt:sqla")
        store.transition_attempt(attempt_id, state="STARTED", error="late stale worker")
        durable = store.get_attempt(attempt_id)
        assert durable is not None and durable["state"] == "ACKED"
    finally:
        provider.close()
