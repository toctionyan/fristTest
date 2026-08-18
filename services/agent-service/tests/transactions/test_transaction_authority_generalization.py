from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from agent_core.persistence.database_settings import DatabaseSettings
from agent_core.persistence.store_provider import build_sqlite_store_provider


BRANCH_SCOPE = {
    "tenant_id": "tenant-a",
    "user_id": "u001",
    "thread_id": "thread-a",
}


def _build_provider(tmp_path: Path, backend: str):
    if backend == "sqlite":
        return build_sqlite_store_provider(
            DatabaseSettings(
                backend="sqlite",
                database_url=f"sqlite:///{tmp_path / 'authority.sqlite3'}",
                sqlite_path=tmp_path / "authority.sqlite3",
            )
        )

    from agent_core.persistence.sqlalchemy_provider import build_sqlalchemy_store_provider

    return build_sqlalchemy_store_provider(
        DatabaseSettings(
            backend="sqlalchemy",
            database_url=f"sqlite:///{tmp_path / 'authority-sqlalchemy.sqlite3'}",
            sqlite_path=tmp_path / "unused.sqlite3",
            create_schema=True,
        )
    )


def _attempt(provider, *, grant_id: str, attempt_id: str, idempotency_key: str, bindings: dict[str, object]):
    return provider.transactions.reserve_grant_and_start_attempt(
        grant_id=grant_id,
        attempt_id=attempt_id,
        tenant_id=str(bindings["tenant_id"]),
        user_id=str(bindings["user_id"]),
        thread_id=str(bindings["thread_id"]),
        draft_id=str(bindings["draft_id"]),
        draft_revision=int(bindings["draft_revision"]),
        action_id="cancel_order",
        command_digest=str(bindings["command_digest"]),
        idempotency_key=idempotency_key,
        canonical_payload={"action_id": "cancel_order", "order_id": "10003"},
    )


def _persist_authorized_draft(provider, *, bindings: dict[str, object], confirmation_id: str):
    """Enter the authority gate through the canonical Draft lifecycle.

    Grant issuance is intentionally downstream of a durable Draft in
    AWAITING_AUTHORIZATION.  The generalization counterexample must therefore
    establish that prerequisite instead of bypassing the product contract.
    """

    return provider.transactions.create_draft(
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


@pytest.mark.parametrize("backend", ["sqlite", "sqlalchemy"])
@pytest.mark.parametrize(
    ("binding", "replacement"),
    [
        ("draft_id", "draft:other"),
        ("draft_revision", 2),
        ("command_digest", "digest:other"),
        ("tenant_id", "tenant-b"),
        ("user_id", "u999"),
        ("thread_id", "thread-b"),
    ],
)
def test_unexpired_grant_is_bound_to_exact_draft_scope_and_digest(
    tmp_path: Path,
    backend: str,
    binding: str,
    replacement: object,
):
    """A valid Grant is unusable once any authority binding changes.

    The rejection must happen before an Attempt is created and must not consume
    or reserve the original Grant, so the exact authorized command can still
    use it afterwards.
    """

    provider = _build_provider(tmp_path, backend)
    suffix = uuid.uuid4().hex
    grant_id = f"grant:{suffix}"
    confirmation_id = f"confirm:{suffix}"
    bindings: dict[str, object] = {
        **BRANCH_SCOPE,
        "draft_id": f"draft:{suffix}",
        "draft_revision": 1,
        "command_digest": f"digest:{suffix}",
    }

    try:
        draft = _persist_authorized_draft(
            provider,
            bindings=bindings,
            confirmation_id=confirmation_id,
        )
        assert draft["draft_state"] == "AWAITING_AUTHORIZATION"
        assert draft["draft_id"] == bindings["draft_id"]
        assert int(draft["draft_revision"]) == int(bindings["draft_revision"])
        assert draft["command_digest"] == bindings["command_digest"]

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

        mismatched = dict(bindings)
        mismatched[binding] = replacement
        rejected_attempt_id = f"attempt:rejected:{suffix}:{binding}"
        rejected_idempotency_key = f"idem:rejected:{suffix}:{binding}"

        rejected = _attempt(
            provider,
            grant_id=grant_id,
            attempt_id=rejected_attempt_id,
            idempotency_key=rejected_idempotency_key,
            bindings=mismatched,
        )

        assert rejected["reserved"] is False
        assert rejected["created"] is False
        assert rejected["attempt"] == {}
        assert provider.transactions.get_attempt(rejected_attempt_id) is None
        assert provider.transactions.get_attempt_by_idempotency_key(rejected_idempotency_key) is None
        assert provider.transactions.get_grant(grant_id)["state"] == "ISSUED"

        accepted = _attempt(
            provider,
            grant_id=grant_id,
            attempt_id=f"attempt:accepted:{suffix}:{binding}",
            idempotency_key=f"idem:accepted:{suffix}:{binding}",
            bindings=bindings,
        )
        assert accepted["reserved"] is True
        assert accepted["created"] is True
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
