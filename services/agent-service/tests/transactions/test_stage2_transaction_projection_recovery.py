from __future__ import annotations

from langchain_core.messages import HumanMessage

from agent_core.ledger import artifact_entry, offer_entry
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.runtime.outcomes import outcome
from agent_core.storage.repositories.base import TransactionScope
from agent_core.transaction.deps import TransactionExecutionDeps
from agent_core.transaction.gateway_runtime import _mark_offer_awaiting_authority, advance_transaction_gateway


SCOPE = {"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "thread-refund-resume"}


class _NoBusinessCalls:
    def __getattr__(self, name: str):
        raise AssertionError(f"Workflow recovery must not call Business Service: {name}")


def test_resume_projects_existing_refund_draft_without_new_draft_grant_or_attempt(tmp_path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    order = artifact_entry(
        resource_type="order",
        resource_id="10002",
        label="机械键盘（订单 10002）",
        facts={"order_id": "10002", "status": "已签收", "version": 1},
        scope=SCOPE,
        turn=7,
        source="test",
        freshness_version=1,
        handle="artifact:order:10002",
    )
    offer = offer_entry(
        action_id="create_refund",
        operation="APPLY_REFUND",
        target_handle=order["handle"],
        input_values={"reason": "质量问题", "expected_version": 1},
        preview={"decision": "ALLOWED", "snapshot": {"version": 1}, "message": "可申请退款"},
        scope=SCOPE,
        turn=7,
        label="退款申请",
        handle="draft:refund:10002",
    )
    creating_state = {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "turn_index": 7,
        "artifact_ledger": [order, offer],
        "_transaction_repository": store,
    }
    _ledger, pending = _mark_offer_awaiting_authority(creating_state, offer, [order, offer])
    draft_id = str(pending["draft_id"])
    original_confirmation_id = str(pending["confirmation_id"])
    original_revision = int(pending["draft_revision"])

    durable_before = store.get_draft(draft_id)
    assert durable_before is not None
    assert durable_before["draft_state"] == "AWAITING_AUTHORIZATION"
    assert durable_before["projection"]["confirmation_id"] == original_confirmation_id
    target_reference = durable_before["projection"]["target_reference"]
    assert target_reference["handle"] == order["handle"]
    assert target_reference["resource_type"] == "order"
    assert target_reference["resource_id"] == "10002"
    assert "facts" not in target_reference

    resumed_state = {
        "current_tenant_id": SCOPE["tenant_id"],
        "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"],
        "current_role": "customer",
        "turn_index": 8,
        "messages": [HumanMessage(content="继续")],
        # Harder crash/restart case: both the ephemeral Draft/card and target
        # artifact are gone from Workflow state. Only Transaction Repository
        # survives.
        "artifact_ledger": [],
        "action_queue": [],
        "_transaction_repository": store,
    }
    patch = advance_transaction_gateway(
        resumed_state,
        deps=TransactionExecutionDeps(business_port=_NoBusinessCalls(), outcome_factory=outcome),
    )

    assert patch["status"] == "TransactionInteractionRestored"
    assert patch["focused_draft_id"] == draft_id
    assert patch["active_draft_id"] == draft_id
    assert patch["commit_authority"] is None
    contract = patch["response_contract"]
    assert contract["source"] == "transaction_repository_projection"
    interaction = contract["interaction"]
    assert interaction["interaction_id"] == draft_id
    assert interaction["lifecycle"] == "awaiting_authority"
    assert interaction["target"] == "机械键盘（订单 10002）"
    assert interaction["control"]["confirmation_id"] == original_confirmation_id
    assert interaction["control"]["authority_type_required"] == "ui_confirmed"

    recovered_target = next(
        row for row in patch["artifact_ledger"] if row.get("handle") == order["handle"]
    )
    assert recovered_target["resource_type"] == "order"
    assert recovered_target["resource_id"] == "10002"
    assert recovered_target["facts"] == {}
    assert recovered_target["source"] == "transaction_repository_target_reference"

    tx_scope = TransactionScope(**SCOPE)
    drafts = store.list_drafts_for_scope(scope=tx_scope, states=None, limit=20)
    assert len(drafts) == 1
    assert drafts[0]["draft_id"] == draft_id
    assert drafts[0]["draft_revision"] == original_revision
    assert drafts[0]["draft_state"] == "AWAITING_AUTHORIZATION"
    assert store.list_grants_by_thread(**SCOPE) == []
    assert store.list_attempts_for_draft(scope=tx_scope, draft_id=draft_id) == []


def test_recovery_never_guesses_between_two_pending_authority_drafts(tmp_path) -> None:
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    order_a = artifact_entry(
        resource_type="order", resource_id="10002", label="机械键盘（订单 10002）",
        facts={"order_id": "10002", "version": 1}, scope=SCOPE, turn=7, source="test",
        freshness_version=1, handle="artifact:order:10002",
    )
    order_b = artifact_entry(
        resource_type="order", resource_id="10004", label="定制马克杯（订单 10004）",
        facts={"order_id": "10004", "version": 1}, scope=SCOPE, turn=7, source="test",
        freshness_version=1, handle="artifact:order:10004",
    )
    offers = [
        offer_entry(
            action_id="create_refund", operation="APPLY_REFUND", target_handle=order_a["handle"],
            input_values={"reason": "质量问题", "expected_version": 1},
            preview={"decision": "ALLOWED", "snapshot": {"version": 1}},
            scope=SCOPE, turn=7, label="退款申请 A", handle="draft:refund:10002",
        ),
        offer_entry(
            action_id="create_refund", operation="APPLY_REFUND", target_handle=order_b["handle"],
            input_values={"reason": "质量问题", "expected_version": 1},
            preview={"decision": "ALLOWED", "snapshot": {"version": 1}},
            scope=SCOPE, turn=7, label="退款申请 B", handle="draft:refund:10004",
        ),
    ]
    creating_state = {
        "current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"], "turn_index": 7,
        "artifact_ledger": [order_a, order_b, *offers], "_transaction_repository": store,
    }
    ledger = list(creating_state["artifact_ledger"])
    ledger, _ = _mark_offer_awaiting_authority(creating_state, offers[0], ledger)
    ledger, _ = _mark_offer_awaiting_authority(creating_state, offers[1], ledger)

    resumed_state = {
        "current_tenant_id": SCOPE["tenant_id"], "current_user_id": SCOPE["user_id"],
        "current_thread_id": SCOPE["thread_id"], "current_role": "customer",
        "turn_index": 8, "messages": [HumanMessage(content="继续")],
        "artifact_ledger": [], "action_queue": [], "_transaction_repository": store,
    }
    patch = advance_transaction_gateway(
        resumed_state,
        deps=TransactionExecutionDeps(business_port=_NoBusinessCalls(), outcome_factory=outcome),
    )

    assert patch["status"] == "NoActionProposal"
    assert patch["response_contract"] is None
    tx_scope = TransactionScope(**SCOPE)
    drafts = store.list_drafts_for_scope(scope=tx_scope, states={"AWAITING_AUTHORIZATION"}, limit=20)
    assert len(drafts) == 2
    assert store.list_grants_by_thread(**SCOPE) == []
    for draft in drafts:
        assert store.list_attempts_for_draft(scope=tx_scope, draft_id=draft["draft_id"]) == []
