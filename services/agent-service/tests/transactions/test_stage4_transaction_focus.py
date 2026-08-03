from __future__ import annotations

from pathlib import Path

from agent_core.ledger import offer_entry
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.focus import (
    focused_draft_patch,
    get_focused_draft_id,
    list_open_drafts,
    next_focus_after_terminal,
)
from agent_core.transaction.lifecycle_query import ConversationContinuationResolver
from agent_core.transaction.reconciliation import reconcile_attempts


def _create_draft(store: TransactionLifecycleStore, draft_id: str, state: str = "AWAITING_AUTHORIZATION") -> dict:
    return store.create_draft(
        draft_id=draft_id,
        tenant_id="tenant-a",
        user_id="u001",
        thread_id="thread-1",
        draft_revision=1,
        draft_state=state,
        action_id="create_refund",
        command_digest=f"digest:{draft_id}",
        command_envelope={"contract": "business.operation.command@1", "action_id": "create_refund"},
        projection={"kind": "offer", "handle": draft_id, "draft_id": draft_id, "draft_state": state, "label": draft_id},
    )


def test_focused_draft_is_authoritative_and_legacy_active_is_projection_only():
    assert get_focused_draft_id({"focused_draft_id": "draft:new", "active_draft_id": "draft:old"}) == "draft:new"
    assert get_focused_draft_id({"focused_draft_id": None, "active_draft_id": "draft:stale"}) is None
    assert get_focused_draft_id({"active_draft_id": "draft:legacy"}) == "draft:legacy"
    assert focused_draft_patch("draft:2") == {"focused_draft_id": "draft:2", "active_draft_id": "draft:2"}


def test_multiple_durable_open_drafts_do_not_overwrite_each_other(tmp_path: Path):
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    _create_draft(store, "draft:1", "NEEDS_INPUT")
    _create_draft(store, "draft:2", "AWAITING_AUTHORIZATION")
    scope = TransactionScope("tenant-a", "u001", "thread-1")
    rows = list_open_drafts(store, scope=scope)
    assert {row["draft_id"] for row in rows} == {"draft:1", "draft:2"}

    resolver = ConversationContinuationResolver(store)
    unresolved = resolver.resolve(state={}, scope=scope)
    assert unresolved.mode == "need_selection"
    focused = resolver.resolve(state={"focused_draft_id": "draft:1", "active_draft_id": "draft:2"}, scope=scope)
    assert focused.draft_id == "draft:1"
    assert focused.mode == "focused_draft"


def test_terminal_focus_moves_only_when_one_other_open_draft_exists(tmp_path: Path):
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    _create_draft(store, "draft:done", "COMMITTED")
    _create_draft(store, "draft:next", "NEEDS_INPUT")
    scope = TransactionScope("tenant-a", "u001", "thread-1")
    assert next_focus_after_terminal(store, scope=scope, terminal_draft_id="draft:done") == "draft:next"
    _create_draft(store, "draft:third", "READY")
    assert next_focus_after_terminal(store, scope=scope, terminal_draft_id="draft:done") is None


def test_reconciliation_of_one_draft_preserves_unrelated_focused_draft(tmp_path: Path):
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    _create_draft(store, "draft:pending", "NEEDS_INPUT")
    _create_draft(store, "draft:reconcile", "COMMITTING")
    store.start_attempt(
        attempt_id="attempt:1", tenant_id="tenant-a", user_id="u001", thread_id="thread-1",
        draft_id="draft:reconcile", draft_revision=1, grant_id="grant:1", action_id="create_refund",
        command_digest="digest:draft:reconcile", idempotency_key="idem:1", canonical_payload={"action": "create_refund"},
    )
    # Durable command envelope is deliberately absent: reconciliation must mark
    # only this draft unknown and must not clear the other UI focus.
    result = reconcile_attempts(
        {
            "current_tenant_id": "tenant-a", "current_user_id": "u001", "current_thread_id": "thread-1",
            "focused_draft_id": "draft:pending", "active_draft_id": "draft:pending", "artifact_ledger": [],
        },
        store=store,
        execute_envelope=lambda *_: {"success": True},
        new_resource_artifacts=lambda *_: [],
        record_transaction_receipt_fn=lambda **_: None,
    )
    assert result is None or result.get("focused_draft_id", "draft:pending") == "draft:pending"
    assert store.get_draft("draft:reconcile")["draft_state"] == "SUBMISSION_UNKNOWN"


def test_checkpoint_migration_promotes_focus_once_and_null_blocks_stale_legacy_pointer():
    from agent_core.lifecycle.state_schema import migrate_checkpoint_state

    migrated, report = migrate_checkpoint_state({"active_draft_id": "draft:legacy"})
    assert migrated["focused_draft_id"] == "draft:legacy"
    assert migrated["active_draft_id"] == "draft:legacy"
    assert "active_draft_id->focused_draft_id" in report["migrated_fields"]

    cleared, cleared_report = migrate_checkpoint_state(
        {"focused_draft_id": None, "active_draft_id": "draft:stale"}
    )
    assert cleared["focused_draft_id"] is None
    assert cleared["active_draft_id"] is None
    assert "active_draft_id:compatibility_projection_from_focused_draft_id" in cleared_report["rederived_fields"]


def test_operation_envelope_separates_actor_subject_and_resource_scope():
    from agent_core.business import ActorContext
    from agent_core.operations.base import DeclarativeOperationPlugin

    plugin = DeclarativeOperationPlugin(
        action_id="create_refund",
        business_code="refund",
        business_operation="APPLY_REFUND",
        label="申请退款",
        risk_level="high",
        input_schema=(),
        target_resource_type="order",
    )
    actor = ActorContext(
        user_id="operator-1",
        role="operator",
        tenant_id="tenant-a",
        subject_user_id="customer-1",
    )
    envelope = plugin.build_business_command_envelope(
        actor=actor,
        target={"resource_type": "order", "resource_id": "10002"},
        input_values={"reason": "broken", "expected_version": 3},
        preview=None,
    )
    assert envelope["actor_scope"] == {
        "actor_user_id": "operator-1", "actor_role": "operator", "tenant_id": "tenant-a"
    }
    assert envelope["subject_scope"] == {
        "subject_user_id": "customer-1", "tenant_id": "tenant-a"
    }
    assert envelope["resource_scope"] == {
        "resource_type": "order", "resource_id": "10002", "expected_version": 3,
        "subject_user_id": "customer-1",
    }
    assert envelope["input"]["subject_user_id"] == "customer-1"


def test_stale_control_expires_old_interaction_and_returns_current_focus_without_langgraph():
    from app.services.stale_interaction import build_stale_interaction_response

    response = build_stale_interaction_response(
        "thread-1",
        include_debug=True,
        reason="offer_handle_mismatch",
        interaction_id="draft:old",
        latest_state={
            "current_tenant_id": "tenant-a",
            "current_user_id": "u001",
            "current_thread_id": "thread-1",
            "focused_draft_id": "draft:new",
            "active_draft_id": "draft:new",
            "artifact_ledger": [],
        },
    )
    assert response.error == "STALE_INTERACTION"
    assert response.state["focused_draft_id"] == "draft:new"
    assert response.interaction_update["interaction_id"] == "draft:old"
    assert response.interaction_update["lifecycle"] == "expired"
    assert response.state["debug_confirmation_error"]["reason"] == "offer_handle_mismatch"


def test_authenticated_subject_overwrites_client_payload_and_reaches_runtime_context():
    from agent_core.security.auth_provider import AuthenticatedActor
    from agent_core.transaction.gateway_runtime import _actor_context_from_state
    from app.schemas.chat_schema import ChatRequest
    from app.security import apply_actor_to_payload

    client = ChatRequest(
        thread_id="thread-1", user_id="attacker", role="admin",
        tenant_id="tenant-b", subject="forged-subject", message="hello",
    )
    actor = AuthenticatedActor(
        user_id="operator-1", role="operator", tenant_id="tenant-a",
        permissions=("chat:use",), subject="customer-1",
    )
    trusted = apply_actor_to_payload(client, actor)
    assert trusted.user_id == "operator-1"
    assert trusted.role == "operator"
    assert trusted.tenant_id == "tenant-a"
    assert trusted.subject == "customer-1"

    runtime_actor = _actor_context_from_state({
        "current_user_id": trusted.user_id,
        "current_role": trusted.role,
        "current_tenant_id": trusted.tenant_id,
        "current_subject": trusted.subject,
        "actor_permissions": trusted.actor_permissions,
    })
    assert runtime_actor.user_id == "operator-1"
    assert runtime_actor.resolved_subject_user_id == "customer-1"


def test_ledger_scope_records_actor_and_subject_without_changing_actor_ownership_key():
    from agent_core.ledger import scope_for_state

    scope = scope_for_state({
        "current_tenant_id": "tenant-a",
        "current_user_id": "operator-1",
        "current_subject": "customer-1",
        "current_thread_id": "thread-1",
    })
    assert scope["user_id"] == "operator-1"
    assert scope["actor_user_id"] == "operator-1"
    assert scope["subject_user_id"] == "customer-1"
