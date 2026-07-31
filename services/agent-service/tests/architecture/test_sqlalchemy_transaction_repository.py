from __future__ import annotations

from agent_core.persistence.database_settings import DatabaseSettings
from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider


def test_sqlalchemy_repository_serializes_domain_payloads_into_declared_columns(
    tmp_path,
) -> None:
    provider = build_sqlalchemy_store_provider(
        DatabaseSettings(
            backend="sqlalchemy",
            database_url=f"sqlite:///{tmp_path / 'repository.db'}",
            sqlite_path=tmp_path / "unused.db",
            create_schema=True,
        )
    )
    try:
        draft = provider.transactions.create_draft(
            draft_id="draft-1",
            tenant_id="tenant-a",
            user_id="u001",
            thread_id="thread-1",
            draft_revision=1,
            draft_state="READY",
            action_id="cancel_order",
            command_digest="digest",
            command_envelope={"command_id": "command-1"},
            projection={"label": "取消订单"},
            active_grant_id=None,
            current_attempt_id=None,
        )
        assert draft["command_envelope"] == {"command_id": "command-1"}
        assert draft["projection"] == {"label": "取消订单"}

        receipt = provider.transactions.record_receipt(
            receipt_id="receipt-1",
            tenant_id="tenant-a",
            user_id="u001",
            thread_id="thread-1",
            draft_id="draft-1",
            attempt_id="attempt-1",
            receipt_handle="receipt:1",
            receipt_state="SUCCESS",
            business_result={"success": True},
            business_resource_id="order-1",
        )
        assert receipt["business_result"] == {"success": True}
    finally:
        provider.close()
