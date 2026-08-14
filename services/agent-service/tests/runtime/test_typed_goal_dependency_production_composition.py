from __future__ import annotations

import base64
import inspect
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import app.services.dependency_authority_composition as composition_module
from app.services.dependency_authority_composition import (
    DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME,
    DependencyAuthorityCompositionError,
    build_dependency_authority_control_composition,
    dependency_authority_composition_readiness,
)
from agent_core.runtime.dependency_authority_control import (
    DisabledDependencyAuthorityControlProvider,
)
from agent_core.runtime.dependency_authority_persistent_control import (
    PersistentDependencyAuthorityControlProvider,
    build_dependency_authority_rollback_directive,
    dependency_authority_rollback_directive_signing_bytes,
)
from agent_core.runtime.dependency_authority_signed_provider import (
    build_dependency_authority_signed_record,
    dependency_authority_signed_record_signing_bytes,
)


def _private(seed_start: int) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(
        bytes(range(seed_start, seed_start + 32))
    )


def _public_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        Encoding.Raw,
        PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _configure_persistent(monkeypatch, *, same_key: bool = False) -> tuple[Ed25519PrivateKey, Ed25519PrivateKey]:
    activation = _private(1)
    rollback = activation if same_key else _private(33)
    monkeypatch.setenv("DEPENDENCY_AUTHORITY_CONTROL_MODE", "persistent")
    monkeypatch.setenv(
        "DEPENDENCY_AUTHORITY_ACTIVATION_SIGNER_ID",
        "release-governance",
    )
    monkeypatch.setenv(
        "DEPENDENCY_AUTHORITY_ACTIVATION_PUBLIC_KEY_B64",
        _public_b64(activation),
    )
    monkeypatch.setenv(
        "DEPENDENCY_AUTHORITY_ROLLBACK_OPERATOR_ID",
        "emergency-operator",
    )
    monkeypatch.setenv(
        "DEPENDENCY_AUTHORITY_ROLLBACK_PUBLIC_KEY_B64",
        _public_b64(rollback),
    )
    return activation, rollback


def _engine():
    engine = sa.create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(sa.text("""
            CREATE TABLE agent_dependency_authority_control_records (
                control_epoch BIGINT PRIMARY KEY,
                revision VARCHAR(500) NOT NULL UNIQUE,
                snapshot_digest VARCHAR(128) NOT NULL UNIQUE,
                record_json TEXT NOT NULL,
                stored_at VARCHAR(64) NOT NULL
            )
        """))
        connection.execute(sa.text("""
            CREATE TABLE agent_dependency_authority_rollback_directives (
                rollback_epoch BIGINT PRIMARY KEY,
                revision VARCHAR(500) NOT NULL UNIQUE,
                snapshot_digest VARCHAR(128) NOT NULL UNIQUE,
                directive_json TEXT NOT NULL,
                stored_at VARCHAR(64) NOT NULL
            )
        """))
    return engine


def _activation_record(private_key: Ed25519PrivateKey):
    values = {
        "provider_id": "governance-control-plane",
        "revision": "control-rev-0007",
        "control_epoch": 7,
        "signer_id": "release-governance",
        "signature_scheme": DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME,
        "issued_at": 900.0,
        "expires_at": 1200.0,
        "signature": "placeholder",
        "activation_preflight": {"preflight_digest": "b" * 64},
        "runtime_activation": {"activation_digest": "c" * 64},
        "rollback_requested": False,
    }
    unsigned = build_dependency_authority_signed_record(**values)
    values["signature"] = base64.b64encode(
        private_key.sign(
            dependency_authority_signed_record_signing_bytes(unsigned)
        )
    ).decode("ascii")
    return build_dependency_authority_signed_record(**values)


def _rollback_directive(private_key: Ed25519PrivateKey, *, requested: bool = True):
    values = {
        "rollback_epoch": 2,
        "revision": "rollback-rev-0002",
        "operator_id": "emergency-operator",
        "signature_scheme": DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME,
        "issued_at": 950.0,
        "rollback_requested": requested,
        "reason_code": (
            "operator-emergency-stop" if requested else "operator-clear"
        ),
        "signature": "placeholder",
    }
    unsigned = build_dependency_authority_rollback_directive(**values)
    values["signature"] = base64.b64encode(
        private_key.sign(
            dependency_authority_rollback_directive_signing_bytes(unsigned)
        )
    ).decode("ascii")
    return build_dependency_authority_rollback_directive(**values)


def _insert_activation(engine, record):
    with engine.begin() as connection:
        connection.execute(
            sa.text("""
                INSERT INTO agent_dependency_authority_control_records
                    (control_epoch, revision, snapshot_digest, record_json, stored_at)
                VALUES
                    (:control_epoch, :revision, :snapshot_digest, :record_json, :stored_at)
            """),
            {
                "control_epoch": record["control_epoch"],
                "revision": record["revision"],
                "snapshot_digest": record["record_digest"],
                "record_json": json.dumps(record, sort_keys=True),
                "stored_at": "2026-08-14T18:18:00+08:00",
            },
        )


def _insert_rollback(engine, directive):
    with engine.begin() as connection:
        connection.execute(
            sa.text("""
                INSERT INTO agent_dependency_authority_rollback_directives
                    (rollback_epoch, revision, snapshot_digest, directive_json, stored_at)
                VALUES
                    (:rollback_epoch, :revision, :snapshot_digest, :directive_json, :stored_at)
            """),
            {
                "rollback_epoch": directive["rollback_epoch"],
                "revision": directive["revision"],
                "snapshot_digest": directive["directive_digest"],
                "directive_json": json.dumps(directive, sort_keys=True),
                "stored_at": "2026-08-14T18:18:00+08:00",
            },
        )


def test_stage4k4_default_is_disabled_and_ignores_unused_trust_configuration(monkeypatch):
    monkeypatch.delenv("DEPENDENCY_AUTHORITY_CONTROL_MODE", raising=False)
    monkeypatch.setenv(
        "DEPENDENCY_AUTHORITY_ACTIVATION_PUBLIC_KEY_B64",
        "not-even-base64",
    )
    composition = build_dependency_authority_control_composition(
        store_provider=object()
    )
    assert composition.mode == "disabled"
    assert isinstance(
        composition.provider,
        DisabledDependencyAuthorityControlProvider,
    )
    assert composition.provider.resolve() is None
    readiness = dependency_authority_composition_readiness(composition)
    assert readiness["ready"] is True
    assert readiness["status"] == "DISABLED_FAIL_CLOSED"
    assert readiness["trusted_control_available"] is False


def test_stage4k4_mode_is_explicit_and_never_auto_selected_from_storage(monkeypatch):
    monkeypatch.setenv("DEPENDENCY_AUTHORITY_CONTROL_MODE", "auto")
    with pytest.raises(DependencyAuthorityCompositionError):
        build_dependency_authority_control_composition(
            store_provider=object()
        )


def test_stage4k4_persistent_mode_requires_shared_sqlalchemy_store(monkeypatch):
    _configure_persistent(monkeypatch)
    with pytest.raises(
        DependencyAuthorityCompositionError,
        match="shared SQLAlchemy StoreProvider",
    ):
        build_dependency_authority_control_composition(
            store_provider=object()
        )


def test_stage4k4_requires_independent_activation_and_rollback_trust_roots(monkeypatch):
    _configure_persistent(monkeypatch, same_key=True)
    engine = _engine()
    try:
        with pytest.raises(
            DependencyAuthorityCompositionError,
            match="trust roots must be distinct",
        ):
            build_dependency_authority_control_composition(
                store_provider=SimpleNamespace(engine=engine, sa=sa),
                evaluation_time_resolver=lambda: 1000.0,
            )
    finally:
        engine.dispose()


def test_stage4k4_persistent_composition_verifies_ed25519_activation_and_sanitizes_readiness(monkeypatch):
    activation_key, _rollback_key = _configure_persistent(monkeypatch)
    engine = _engine()
    try:
        record = _activation_record(activation_key)
        _insert_activation(engine, record)
        composition = build_dependency_authority_control_composition(
            store_provider=SimpleNamespace(engine=engine, sa=sa),
            evaluation_time_resolver=lambda: 1000.0,
        )
        assert composition.mode == "persistent"
        assert isinstance(
            composition.provider,
            PersistentDependencyAuthorityControlProvider,
        )
        resolved = composition.provider.resolve()
        assert resolved == {
            "activation_preflight": {"preflight_digest": "b" * 64},
            "runtime_activation": {"activation_digest": "c" * 64},
            "evaluation_time": 1000.0,
            "rollback_requested": False,
            "control_head_identity": {
                "control_epoch": 7,
                "revision": "control-rev-0007",
                "snapshot_digest": record["record_digest"],
            },
            "rollback_head_identity": None,
        }
        readiness = dependency_authority_composition_readiness(composition)
        assert readiness["ready"] is True
        assert readiness["status"] == "PERSISTENT_CONTROL_VERIFIED"
        assert readiness["has_activation_preflight"] is True
        assert readiness["has_runtime_activation"] is True
        assert "activation_preflight" not in readiness
        assert "runtime_activation" not in readiness
        assert "signature" not in readiness
        assert readiness["signature_scheme"] == DEPENDENCY_AUTHORITY_ED25519_SIGNATURE_SCHEME
        assert len(readiness["activation_public_key_fingerprint"]) == 64
        assert len(readiness["rollback_public_key_fingerprint"]) == 64
        assert readiness["trust_roots_independent"] is True
    finally:
        engine.dispose()


def test_stage4k4_verified_independent_rollback_composes_without_activation_record(monkeypatch):
    _activation_key, rollback_key = _configure_persistent(monkeypatch)
    engine = _engine()
    try:
        directive = _rollback_directive(rollback_key, requested=True)
        _insert_rollback(engine, directive)
        composition = build_dependency_authority_control_composition(
            store_provider=SimpleNamespace(engine=engine, sa=sa),
            evaluation_time_resolver=lambda: 1000.0,
        )
        resolved = composition.provider.resolve()
        assert resolved == {
            "activation_preflight": None,
            "runtime_activation": None,
            "evaluation_time": None,
            "rollback_requested": True,
            "control_head_identity": None,
            "rollback_head_identity": {
                "rollback_epoch": 2,
                "revision": "rollback-rev-0002",
                "snapshot_digest": directive["directive_digest"],
            },
        }
        readiness = dependency_authority_composition_readiness(composition)
        assert readiness["ready"] is True
        assert readiness["status"] == "PERSISTENT_ROLLBACK_ACTIVE"
        assert readiness["rollback_requested"] is True
    finally:
        engine.dispose()


def test_stage4k4_invalid_or_untrusted_persistent_control_fails_readiness_closed(monkeypatch):
    _configure_persistent(monkeypatch)
    engine = _engine()
    try:
        composition = build_dependency_authority_control_composition(
            store_provider=SimpleNamespace(engine=engine, sa=sa),
            evaluation_time_resolver=lambda: 1000.0,
        )
        readiness = dependency_authority_composition_readiness(composition)
        assert readiness["ready"] is False
        assert readiness["status"] == "PERSISTENT_CONTROL_UNAVAILABLE_FAIL_CLOSED"
        assert readiness["trusted_control_available"] is False
    finally:
        engine.dispose()


def test_stage4k4_composition_owns_no_model_configuration_or_signing_private_keys() -> None:
    source = inspect.getsource(composition_module)
    assert "OPENAI_API_KEY" not in source
    assert "OPENAI_MODEL" not in source
    assert "PRIVATE_KEY" not in source
    assert "SIGNING_SECRET" not in source
    assert "ed25519-v1" in source
