from __future__ import annotations

from tests.support.paths import agent_root

from pathlib import Path

from agent_core.ledger import offer_entry
from agent_core.persistence.action_lifecycle_store import TransactionLifecycleStore
from agent_core.rag.access import normalize_scope, scope_filter
from agent_core.rag.vector_store import LocalVectorStore
from agent_core.transaction import ensure_transaction_draft


def _envelope() -> dict:
    return {
        "contract": "business_adapter.commit@1",
        "method": "POST",
        "path": "/refunds",
        "action_id": "create_refund",
        "operation": "APPLY_REFUND",
        "target": {"resource_type": "order", "resource_id": "10002", "order_id": "10002"},
        "payload": {"reason": "质量问题", "expected_version": 7},
        "actor_scope": {"tenant_id": "tenant-a", "user_id": "u001"},
    }


def _offer() -> dict:
    row = offer_entry(
        action_id="create_refund", operation="APPLY_REFUND", target_handle="h_order:10002",
        input_values={"reason": "质量问题", "expected_version": 7},
        preview={"decision": "ALLOWED", "snapshot": {"version": 7}},
        scope={"tenant_id": "tenant-a", "user_id": "u001", "thread_id": "t001"},
        turn=1, label="退款申请",
    )
    row["business_command_envelope"] = _envelope()
    return ensure_transaction_draft(row)


def test_durable_draft_attempt_and_receipt_do_not_require_checkpoint(tmp_path: Path):
    store = TransactionLifecycleStore(tmp_path / "agent.db")
    offer = _offer()
    store.create_draft(
        draft_id=offer["draft_id"], tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_revision=offer["draft_revision"], draft_state="AWAITING_AUTHORIZATION",
        action_id=offer["action_id"], command_digest=offer["command_digest"],
        command_envelope=offer["business_command_envelope"], projection={"label": offer["label"]},
    )
    store.issue_grant(
        grant_id="grant-durable", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], command_digest=offer["command_digest"],
        confirmation_id="confirm-durable", client_request_id="client-durable", actor_id="u001", actor_role="customer",
    )
    started = store.reserve_grant_and_start_attempt(
        grant_id="grant-durable", attempt_id="attempt-durable", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id=offer["draft_id"], draft_revision=offer["draft_revision"], action_id=offer["action_id"],
        command_digest=offer["command_digest"], idempotency_key="idem-durable", canonical_payload=offer["command_payload"],
        business_command_envelope=offer["business_command_envelope"], draft_projection={"label": offer["label"]},
    )
    assert started["reserved"] and started["created"]
    # Simulate complete checkpoint/Ledger loss: only lifecycle records remain.
    durable = store.get_draft(offer["draft_id"])
    attempt = store.get_attempt("attempt-durable")
    assert durable and durable["command_envelope"] == _envelope()
    assert attempt and attempt["business_command_envelope"] == _envelope()
    receipt = store.record_receipt(
        receipt_id="receipt-durable", tenant_id="tenant-a", user_id="u001", thread_id="t001",
        draft_id=offer["draft_id"], attempt_id="attempt-durable", receipt_handle="h_receipt:1",
        receipt_state="SUCCESS", business_result={"success": True, "data": {"refund_id": "R-1"}}, business_resource_id="R-1",
    )
    assert receipt["receipt_state"] == "SUCCESS"
    assert store.get_receipt_by_attempt("attempt-durable")["business_resource_id"] == "R-1"


def test_local_rag_applies_scope_filter_before_global_top_k(tmp_path: Path):
    store = LocalVectorStore(tmp_path / "vector.db")
    store.add_document("public", "公共政策", "seed", ["refund policy common"], {"visibility": "public", "builtin": True, "status": "published"})
    store.add_document("tenant-a", "A知识", "a", ["refund policy tenant-a"], {"visibility": "tenant", "tenant_id": "tenant-a", "owner_id": "u001", "status": "published"})
    # This document has the strongest lexical match but belongs to another tenant.
    store.add_document("tenant-b", "B机密", "b", ["refund policy tenant-b tenant-b tenant-b"], {"visibility": "tenant", "tenant_id": "tenant-b", "owner_id": "u009", "status": "published"})
    rows = store.search("refund policy tenant-b", top_k=2, filters=scope_filter(normalize_scope(tenant_id="tenant-a", user_id="u001")))
    assert rows
    assert {row["doc_id"] for row in rows}.issubset({"public", "tenant-a"})
    assert "tenant-b" not in {row["doc_id"] for row in rows}


def test_nonlocal_default_requires_auth_and_disables_console_login(monkeypatch):
    from agent_core.security import auth_provider
    monkeypatch.setenv("APP_PROFILE", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOCAL_DEV", raising=False)
    monkeypatch.delenv("AGENT_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("WEB_CONSOLE_DEV_LOGIN", raising=False)
    assert auth_provider.auth_required() is True
    assert auth_provider.console_dev_login_enabled() is False


def test_legacy_and_staff_browser_routes_are_removed_from_customer_service():
    root = agent_root(__file__)
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    assert "legacy_api_enabled" not in main
    assert "staff_api_enabled" not in main
    for name in ("chat_api.py", "resume_api.py", "console_api.py", "debug_api.py", "operator_api.py"):
        assert not (root / "app" / "api" / name).exists()
