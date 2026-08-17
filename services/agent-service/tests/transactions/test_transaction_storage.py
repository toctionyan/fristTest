from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from agent_core.persistence.store_provider import build_sqlite_store_provider
from agent_core.persistence.database_settings import DatabaseSettings


def _exercise_store_provider(provider):
    suffix = uuid.uuid4().hex
    thread_id = f"thread-{suffix}"
    grant_id = f"grant-{suffix}"
    attempt_id = f"attempt-{suffix}"
    draft_id = f"h_offer:{suffix}"
    confirmation_id = f"confirm-{suffix}"
    client_request_id = f"client-{suffix}"
    idempotency_key = f"idem-{suffix}"
    lock_key = f"tenant-a:u001:{thread_id}"
    try:
        provider.threads.claim_or_validate_thread(thread_id, "u001", "tenant-a")
        provider.threads.upsert_thread(thread_id, "u001", summary="订单咨询", tenant_id="tenant-a")
        assert provider.threads.assert_thread_owner(thread_id, "u001", "tenant-a")["thread_id"] == thread_id
        assert provider.threads.list_threads(user_id="u001", tenant_id="tenant-a")

        provider.messages.add_message(
            thread_id,
            "assistant",
            "请确认提交。",
            message_type="interaction_required",
            presentation=[{"type": "text", "content": "请确认提交。"}],
            interaction={"interaction_id": "h_offer:1", "lifecycle": "awaiting_authority"},
        )
        message = provider.messages.list_messages(thread_id)[0]
        assert message["presentation"][0]["type"] == "text"
        assert message["interaction"]["lifecycle"] == "awaiting_authority"

        trace_id = provider.traces.log_event(
            thread_id,
            "u001",
            "test_event",
            input_data={"password": "secret", "invoice_title": "北京示例科技有限公司"},
        )
        trace = provider.traces.list_by_thread(thread_id)[0]
        assert trace_id
        assert "REDACTED" in str(trace.get("input_json"))

        first_lock = provider.locks.acquire(lock_key, owner="test")
        assert first_lock["acquired"] is True
        first_token = int(first_lock["fencing_token"])
        assert first_token > 0
        assert provider.locks.validate(lock_key, owner="test", fencing_token=first_token) is True
        assert provider.locks.renew(lock_key, owner="test", fencing_token=first_token, ttl_seconds=120)["renewed"] is True
        provider.locks.release(lock_key, owner="test", fencing_token=first_token + 1)
        assert provider.locks.validate(lock_key, owner="test", fencing_token=first_token) is True
        provider.locks.release(lock_key, owner="test", fencing_token=first_token)
        second_lock = provider.locks.acquire(lock_key, owner="test-2")
        assert second_lock["acquired"] is True
        assert int(second_lock["fencing_token"]) > first_token
        provider.locks.release(lock_key, owner="test-2", fencing_token=int(second_lock["fencing_token"]))

        provider.transactions.create_draft(
            draft_id=draft_id,
            tenant_id="tenant-a",
            user_id="u001",
            thread_id=thread_id,
            draft_revision=1,
            draft_state="AWAITING_AUTHORIZATION",
            action_id="create_invoice",
            command_digest="digest",
            command_envelope=None,
            projection={
                "kind": "offer", "handle": draft_id, "draft_id": draft_id,
                "draft_revision": 1, "draft_state": "AWAITING_AUTHORIZATION",
                "action_id": "create_invoice", "command_digest": "digest",
                "confirmation_id": confirmation_id, "confirmation_version": 1,
            },
        )
        grant = provider.transactions.issue_grant(
            grant_id=grant_id,
            tenant_id="tenant-a",
            user_id="u001",
            thread_id=thread_id,
            draft_id=draft_id,
            draft_revision=1,
            command_digest="digest",
            confirmation_id=confirmation_id,
            client_request_id=client_request_id,
            actor_id="u001",
            actor_role="customer",
        )
        assert grant["state"] == "ISSUED"
        started = provider.transactions.reserve_grant_and_start_attempt(
            grant_id=grant_id,
            attempt_id=attempt_id,
            tenant_id="tenant-a",
            user_id="u001",
            thread_id=thread_id,
            draft_id=draft_id,
            draft_revision=1,
            action_id="create_invoice",
            command_digest="digest",
            idempotency_key=idempotency_key,
            canonical_payload={"action": "create_invoice"},
        )
        assert started["reserved"] is True
        business_result = {"success": True, "data": {"resource_id": f"resource-{suffix}"}}
        receipt = provider.transactions.record_receipt(
            receipt_id=f"receipt-{suffix}",
            tenant_id="tenant-a",
            user_id="u001",
            thread_id=thread_id,
            draft_id=draft_id,
            attempt_id=attempt_id,
            receipt_handle=f"h_receipt:{suffix}",
            receipt_state="SUCCESS",
            business_result=business_result,
            business_resource_id=f"resource-{suffix}",
        )
        assert receipt["attempt_id"] == attempt_id
        provider.transactions.transition_attempt(
            attempt_id,
            state="ACKED",
            business_result=business_result,
            receipt_handle=f"h_receipt:{suffix}",
        )
        assert provider.transactions.get_attempt(attempt_id)["state"] == "ACKED"
        provider.transactions.consume_grant(
            grant_id,
            attempt_id=attempt_id,
            receipt_handle=f"h_receipt:{suffix}",
        )
        assert provider.transactions.get_grant(grant_id)["state"] == "CONSUMED"
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


def test_sqlite_store_provider_contract(tmp_path: Path):
    provider = build_sqlite_store_provider(
        DatabaseSettings(
            backend="sqlite",
            database_url=f"sqlite:///{tmp_path / 'agent.db'}",
            sqlite_path=tmp_path / "agent.db",
        )
    )
    _exercise_store_provider(provider)


def test_sqlalchemy_store_provider_contract_with_sqlite_url(tmp_path: Path):
    from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider

    provider = build_sqlalchemy_store_provider(
        DatabaseSettings(
            backend="sqlalchemy",
            database_url=f"sqlite:///{tmp_path / 'agent_sqlalchemy.db'}",
            sqlite_path=tmp_path / "unused.db",
            create_schema=True,
        )
    )
    _exercise_store_provider(provider)


def test_sqlalchemy_provider_requires_migrated_schema_when_create_schema_false(tmp_path: Path):
    from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider

    with pytest.raises(RuntimeError, match="Run Alembic migrations"):
        build_sqlalchemy_store_provider(
            DatabaseSettings(
                backend="sqlalchemy",
                database_url=f"sqlite:///{tmp_path / 'empty.db'}",
                sqlite_path=tmp_path / "unused.db",
                create_schema=False,
            )
        )


@pytest.mark.integration
def test_sqlalchemy_store_provider_contract_with_postgres():
    from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider

    provider = build_sqlalchemy_store_provider(
        DatabaseSettings(
            backend="postgres",
            database_url=os.environ["AGENT_TEST_POSTGRES_URL"],
            sqlite_path=Path("unused.db"),
            create_schema=True,
        )
    )
    _exercise_store_provider(provider)
