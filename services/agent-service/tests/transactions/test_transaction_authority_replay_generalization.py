from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from agent_core.persistence.database_settings import DatabaseSettings
from agent_core.persistence.store_provider import build_sqlite_store_provider


BASE_SCOPE = {
    "tenant_id": "tenant-a",
    "user_id": "u001",
    "thread_id": "thread-a",
}


def _build_provider(tmp_path: Path, backend: str):
    if backend == "sqlite":
        return build_sqlite_store_provider(
            DatabaseSettings(
                backend="sqlite",
                database_url=f"sqlite:///{tmp_path / 'authority-replay.sqlite3'}",
                sqlite_path=tmp_path / "authority-replay.sqlite3",
            )
        )

    from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider

    return build_sqlalchemy_store_provider(
        DatabaseSettings(
            backend="sqlalchemy",
            database_url=f"sqlite:///{tmp_path / 'authority-replay-sqlalchemy.sqlite3'}",
            sqlite_path=tmp_path / "unused.sqlite3",
            create_schema=True,
        )
    )


def _attempt(
    provider,
    *,
    grant_id: str,
    attempt_id: str,
    idempotency_key: str,
    bindings: dict[str, object],
    action_id: str = "cancel_order",
):
    return provider.transactions.reserve_grant_and_start_attempt(
        grant_id=grant_id,
        attempt_id=attempt_id,
        tenant_id=str(bindings["tenant_id"]),
        user_id=str(bindings["user_id"]),
        thread_id=str(bindings["thread_id"]),
        draft_id=str(bindings["draft_id"]),
        draft_revision=int(bindings["draft_revision"]),
        action_id=action_id,
        command_digest=str(bindings["command_digest"]),
        idempotency_key=idempotency_key,
        canonical_payload={"action_id": action_id, "order_id": "10003"},
    )


def _issue_authorized_grant(provider, *, suffix: str, bindings: dict[str, object]) -> str:
    confirmation_id = f"confirm:{suffix}"
    draft = provider.transactions.create_draft(
        draft_id=str(bindings["draft_id"]),
        tenant_id=str(bindings["tenant_id"]),
        user_id=str(bindings["user_id"]),
        thread_id=str(bindings["thread_id"]),
        draft_revision=int(bindings["draft_revision"]),
        draft_state="AWAITING_AUTHORIZATION",
        action_id="cancel_order",
        command_digest=str(bindings["command_digest"]),
        command_envelope=None,
        projection={"confirmation_id": confirmation_id},
    )
    assert draft["draft_state"] == "AWAITING_AUTHORIZATION"

    grant_id = f"grant:{suffix}"
    grant = provider.transactions.issue_grant(
        grant_id=grant_id,
        tenant_id=str(bindings["tenant_id"]),
        user_id=str(bindings["user_id"]),
        thread_id=str(bindings["thread_id"]),
        draft_id=str(bindings["draft_id"]),
        draft_revision=int(bindings["draft_revision"]),
        command_digest=str(bindings["command_digest"]),
        confirmation_id=confirmation_id,
        client_request_id=f"client:{suffix}",
        actor_id="u001",
        actor_role="customer",
        expires_at="2999-01-01T00:00:00+00:00",
    )
    assert grant["state"] == "ISSUED"
    return grant_id


@pytest.mark.parametrize("backend", ["sqlite", "sqlalchemy"])
@pytest.mark.parametrize(
    ("binding", "replacement"),
    [
        ("draft_id", "draft:replay-other"),
        ("draft_revision", 2),
        ("command_digest", "digest:replay-other"),
        ("tenant_id", "tenant-replay-other"),
        ("user_id", "u-replay-other"),
        ("thread_id", "thread-replay-other"),
        ("action_id", "create_refund"),
    ],
)
def test_idempotent_replay_cannot_cross_attempt_authority_binding(
    tmp_path: Path,
    backend: str,
    binding: str,
    replacement: object,
):
    """An idempotency key may replay only the exact persisted Attempt identity.

    The first request creates the authoritative Attempt. An exact replay must
    resolve to that Attempt, but the same key with a different scope, Draft
    snapshot, command digest, or action must fail closed rather than disclose
    or reuse the accepted Attempt across an authority boundary.
    """

    provider = _build_provider(tmp_path, backend)
    suffix = uuid.uuid4().hex
    bindings: dict[str, object] = {
        **BASE_SCOPE,
        "draft_id": f"draft:{suffix}",
        "draft_revision": 1,
        "command_digest": f"digest:{suffix}",
    }
    idempotency_key = f"idem:replay:{suffix}"

    try:
        grant_id = _issue_authorized_grant(provider, suffix=suffix, bindings=bindings)
        first_attempt_id = f"attempt:first:{suffix}"
        first = _attempt(
            provider,
            grant_id=grant_id,
            attempt_id=first_attempt_id,
            idempotency_key=idempotency_key,
            bindings=bindings,
        )
        assert first["reserved"] is True
        assert first["created"] is True
        assert first["attempt"]["attempt_id"] == first_attempt_id

        exact_replay = _attempt(
            provider,
            grant_id=grant_id,
            attempt_id=f"attempt:exact-replay:{suffix}",
            idempotency_key=idempotency_key,
            bindings=bindings,
        )
        assert exact_replay["reserved"] is False
        assert exact_replay["created"] is False
        assert exact_replay["attempt"]["attempt_id"] == first_attempt_id

        replay_bindings = dict(bindings)
        replay_action_id = "cancel_order"
        if binding == "action_id":
            replay_action_id = str(replacement)
        else:
            replay_bindings[binding] = replacement

        rejected = _attempt(
            provider,
            grant_id=grant_id,
            attempt_id=f"attempt:rejected-replay:{suffix}:{binding}",
            idempotency_key=idempotency_key,
            bindings=replay_bindings,
            action_id=replay_action_id,
        )
        assert rejected["reserved"] is False
        assert rejected["created"] is False
        assert rejected["attempt"] == {}

        persisted = provider.transactions.get_attempt_by_idempotency_key(idempotency_key)
        assert persisted is not None
        assert persisted["attempt_id"] == first_attempt_id
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
