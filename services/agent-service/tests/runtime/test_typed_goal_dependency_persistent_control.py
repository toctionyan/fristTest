from __future__ import annotations

import hashlib
import hmac
import inspect
import json

import pytest
import sqlalchemy as sa

import agent_core.runtime.dependency_authority_persistent_control as persistent_control
from agent_core.runtime.dependency_authority_persistent_control import (
    PersistentDependencyAuthorityControlProvider,
    SqlAlchemyDependencyAuthorityRollbackDirectiveSource,
    SqlAlchemyDependencyAuthoritySignedRecordSource,
    build_dependency_authority_rollback_directive,
    dependency_authority_rollback_directive_signing_bytes,
)
from agent_core.runtime.dependency_authority_signed_provider import (
    build_dependency_authority_signed_record,
    dependency_authority_signed_record_signing_bytes,
)
from app.services.agent_service import AgentService


_ACTIVATION_SECRET = b"stage4k2-activation-test-only"
_ROLLBACK_SECRET = b"stage4k2-rollback-test-only"


class _ActivationVerifier:
    def verify(self, *, signer_id, signature_scheme, message, signature):
        if signer_id != "release-governance":
            return False
        if signature_scheme != "hmac-sha256-test-only":
            return False
        expected = hmac.new(_ACTIVATION_SECRET, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class _RollbackVerifier:
    def verify(self, *, operator_id, signature_scheme, message, signature):
        if operator_id != "emergency-operator":
            return False
        if signature_scheme != "hmac-sha256-test-only":
            return False
        expected = hmac.new(_ROLLBACK_SECRET, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class _RejectRollbackVerifier:
    def verify(self, **kwargs):
        return False


class _RaisingActivationSource:
    def load_signed_record(self):
        raise RuntimeError("activation source unavailable")


class _RaisingRollbackSource:
    def load_current_rollback_directive(self):
        raise RuntimeError("rollback source unavailable")


def _activation_record(
    *,
    control_epoch=7,
    revision="control-rev-0007",
    activation_digest="c" * 64,
):
    values = {
        "provider_id": "governance-control-plane",
        "revision": revision,
        "control_epoch": control_epoch,
        "signer_id": "release-governance",
        "signature_scheme": "hmac-sha256-test-only",
        "issued_at": 900.0,
        "expires_at": 1200.0,
        "signature": "placeholder",
        "activation_preflight": {"preflight_digest": "b" * 64},
        "runtime_activation": {"activation_digest": activation_digest},
        "rollback_requested": False,
    }
    unsigned = build_dependency_authority_signed_record(**values)
    values["signature"] = hmac.new(
        _ACTIVATION_SECRET,
        dependency_authority_signed_record_signing_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return build_dependency_authority_signed_record(**values)


def _rollback_directive(
    *,
    rollback_epoch=1,
    revision="rollback-rev-0001",
    rollback_requested=True,
    reason_code="operator-emergency-stop",
):
    values = {
        "rollback_epoch": rollback_epoch,
        "revision": revision,
        "operator_id": "emergency-operator",
        "signature_scheme": "hmac-sha256-test-only",
        "issued_at": 950.0,
        "rollback_requested": rollback_requested,
        "reason_code": reason_code,
        "signature": "placeholder",
    }
    unsigned = build_dependency_authority_rollback_directive(**values)
    values["signature"] = hmac.new(
        _ROLLBACK_SECRET,
        dependency_authority_rollback_directive_signing_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return build_dependency_authority_rollback_directive(**values)


@pytest.fixture()
def engine(tmp_path):
    db_path = tmp_path / "stage4k2-control.sqlite3"
    engine = sa.create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE agent_dependency_authority_control_records (
                    control_epoch BIGINT PRIMARY KEY,
                    revision VARCHAR(500) NOT NULL UNIQUE,
                    snapshot_digest VARCHAR(128) NOT NULL UNIQUE,
                    record_json TEXT NOT NULL,
                    stored_at VARCHAR(64) NOT NULL,
                    CHECK (control_epoch > 0)
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                CREATE TABLE agent_dependency_authority_rollback_directives (
                    rollback_epoch BIGINT PRIMARY KEY,
                    revision VARCHAR(500) NOT NULL UNIQUE,
                    snapshot_digest VARCHAR(128) NOT NULL UNIQUE,
                    directive_json TEXT NOT NULL,
                    stored_at VARCHAR(64) NOT NULL,
                    CHECK (rollback_epoch > 0)
                )
                """
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()


def _insert_activation(engine, record, *, snapshot_digest=None):
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO agent_dependency_authority_control_records
                    (control_epoch, revision, snapshot_digest, record_json, stored_at)
                VALUES
                    (:control_epoch, :revision, :snapshot_digest, :record_json, :stored_at)
                """
            ),
            {
                "control_epoch": record["control_epoch"],
                "revision": record["revision"],
                "snapshot_digest": snapshot_digest or record["record_digest"],
                "record_json": json.dumps(record, sort_keys=True),
                "stored_at": "2026-08-14T16:47:00+08:00",
            },
        )


def _insert_rollback(engine, directive, *, snapshot_digest=None):
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO agent_dependency_authority_rollback_directives
                    (rollback_epoch, revision, snapshot_digest, directive_json, stored_at)
                VALUES
                    (:rollback_epoch, :revision, :snapshot_digest, :directive_json, :stored_at)
                """
            ),
            {
                "rollback_epoch": directive["rollback_epoch"],
                "revision": directive["revision"],
                "snapshot_digest": snapshot_digest or directive["directive_digest"],
                "directive_json": json.dumps(directive, sort_keys=True),
                "stored_at": "2026-08-14T16:47:00+08:00",
            },
        )


def _provider(engine, *, activation_source=None, rollback_source=None, rollback_verifier=None):
    return PersistentDependencyAuthorityControlProvider(
        activation_source=(
            activation_source
            or SqlAlchemyDependencyAuthoritySignedRecordSource(engine=engine, sa=sa)
        ),
        activation_signature_verifier=_ActivationVerifier(),
        rollback_source=(
            rollback_source
            or SqlAlchemyDependencyAuthorityRollbackDirectiveSource(engine=engine, sa=sa)
        ),
        rollback_signature_verifier=rollback_verifier or _RollbackVerifier(),
        evaluation_time_resolver=lambda: 1000.0,
    )


def test_stage4k2_is_not_wired_and_owns_no_environment_or_model_configuration():
    source = inspect.getsource(persistent_control)
    assert "os.getenv" not in source
    assert "get_database_settings" not in source
    assert "OPENAI_API_KEY" not in source
    assert "OPENAI_MODEL" not in source

    service_source = inspect.getsource(AgentService._compose_runtime_deps)
    assert "DisabledDependencyAuthorityControlProvider" in service_source
    assert "PersistentDependencyAuthorityControlProvider" not in service_source
    assert "SqlAlchemyDependencyAuthoritySignedRecordSource" not in service_source


def test_persisted_activation_resolves_only_after_k1_signature_verification(engine):
    record = _activation_record()
    _insert_activation(engine, record)

    source = SqlAlchemyDependencyAuthoritySignedRecordSource(engine=engine, sa=sa)
    assert source.load_head_identity() == {
        "control_epoch": 7,
        "revision": "control-rev-0007",
        "snapshot_digest": record["record_digest"],
    }
    assert _provider(engine).resolve() == {
        "activation_preflight": {"preflight_digest": "b" * 64},
        "runtime_activation": {"activation_digest": "c" * 64},
        "evaluation_time": 1000.0,
        "rollback_requested": False,
    }


def test_independent_verified_rollback_wins_without_touching_activation_source(engine):
    directive = _rollback_directive()
    _insert_rollback(engine, directive)

    provider = _provider(engine, activation_source=_RaisingActivationSource())
    assert provider.resolve() == {
        "activation_preflight": None,
        "runtime_activation": None,
        "evaluation_time": None,
        "rollback_requested": True,
    }


def test_rollback_source_failure_fails_closed_even_when_activation_is_valid(engine):
    _insert_activation(engine, _activation_record())
    provider = _provider(engine, rollback_source=_RaisingRollbackSource())
    assert provider.resolve() is None


def test_invalid_rollback_signature_fails_closed_instead_of_falling_through(engine):
    _insert_activation(engine, _activation_record())
    _insert_rollback(engine, _rollback_directive())
    provider = _provider(engine, rollback_verifier=_RejectRollbackVerifier())
    assert provider.resolve() is None


def test_signed_clear_rollback_directive_allows_verified_activation(engine):
    _insert_activation(engine, _activation_record())
    _insert_rollback(
        engine,
        _rollback_directive(
            rollback_requested=False,
            reason_code="operator-cleared-after-verification",
        ),
    )
    resolved = _provider(engine).resolve()
    assert resolved is not None
    assert resolved["rollback_requested"] is False
    assert resolved["runtime_activation"]["activation_digest"] == "c" * 64


def test_workers_and_restart_read_same_highest_epoch_and_ignore_late_stale_rows(engine):
    epoch7 = _activation_record()
    _insert_activation(engine, epoch7)

    worker_a = SqlAlchemyDependencyAuthoritySignedRecordSource(engine=engine, sa=sa)
    worker_b = SqlAlchemyDependencyAuthoritySignedRecordSource(engine=engine, sa=sa)
    assert worker_a.load_head_identity() == worker_b.load_head_identity()
    assert worker_a.load_signed_record()["control_epoch"] == 7

    epoch8 = _activation_record(
        control_epoch=8,
        revision="control-rev-0008",
        activation_digest="d" * 64,
    )
    _insert_activation(engine, epoch8)
    assert worker_a.load_signed_record()["control_epoch"] == 8
    assert worker_b.load_signed_record()["control_epoch"] == 8

    restarted_worker = SqlAlchemyDependencyAuthoritySignedRecordSource(
        engine=engine, sa=sa
    )
    assert restarted_worker.load_head_identity() == {
        "control_epoch": 8,
        "revision": "control-rev-0008",
        "snapshot_digest": epoch8["record_digest"],
    }

    stale = _activation_record(
        control_epoch=6,
        revision="control-rev-0006",
        activation_digest="e" * 64,
    )
    _insert_activation(engine, stale)
    assert worker_a.load_signed_record()["control_epoch"] == 8
    assert restarted_worker.load_signed_record()["control_epoch"] == 8


def test_conflicting_same_epoch_cannot_be_appended(engine):
    _insert_activation(engine, _activation_record())
    conflicting = _activation_record(
        control_epoch=7,
        revision="control-rev-conflict",
        activation_digest="f" * 64,
    )
    with pytest.raises(sa.exc.IntegrityError):
        _insert_activation(engine, conflicting)


def test_persistence_metadata_mismatch_fails_closed(engine):
    _insert_activation(
        engine,
        _activation_record(),
        snapshot_digest="0" * 64,
    )
    assert _provider(engine).resolve() is None


def test_rollback_workers_share_monotonic_persistent_head(engine):
    first = _rollback_directive(rollback_epoch=1, revision="rollback-rev-0001")
    _insert_rollback(engine, first)

    worker_a = SqlAlchemyDependencyAuthorityRollbackDirectiveSource(
        engine=engine, sa=sa
    )
    worker_b = SqlAlchemyDependencyAuthorityRollbackDirectiveSource(
        engine=engine, sa=sa
    )
    assert worker_a.load_head_identity() == worker_b.load_head_identity()

    second = _rollback_directive(
        rollback_epoch=2,
        revision="rollback-rev-0002",
        rollback_requested=False,
        reason_code="operator-clear",
    )
    _insert_rollback(engine, second)
    assert worker_a.load_current_rollback_directive()["rollback_epoch"] == 2
    assert worker_b.load_current_rollback_directive()["rollback_epoch"] == 2

    stale = _rollback_directive(
        rollback_epoch=1,
        revision="rollback-rev-conflict",
    )
    with pytest.raises(sa.exc.IntegrityError):
        _insert_rollback(engine, stale)


def test_stage4k2_migration_declares_separate_append_only_tables():
    migration_path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "migrations"
        / "agent_db"
        / "versions"
        / "0006_dependency_authority_control.py"
    )
    text = migration_path.read_text(encoding="utf-8")
    assert "agent_dependency_authority_control_records" in text
    assert "agent_dependency_authority_rollback_directives" in text
    assert "control_epoch BIGINT PRIMARY KEY" in text
    assert "rollback_epoch BIGINT PRIMARY KEY" in text
    assert "snapshot_digest VARCHAR(128) NOT NULL UNIQUE" in text


def test_stage4k2_readiness_contract_tracks_current_migration_head(monkeypatch):
    monkeypatch.delenv("AGENT_REQUIRED_ALEMBIC_REVISION", raising=False)
    from agent_core.runtime.migrations import required_agent_revision

    assert required_agent_revision() == "0006_dependency_auth_control"
    env_path = __import__("pathlib").Path(__file__).resolve().parents[2] / ".env.example"
    env_text = env_path.read_text(encoding="utf-8")
    assert "AGENT_REQUIRED_ALEMBIC_REVISION=0006_dependency_auth_control" in env_text
