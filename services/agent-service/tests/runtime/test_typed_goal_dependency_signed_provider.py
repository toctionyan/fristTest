from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os

import agent_core.runtime.dependency_authority_signed_provider as signed_provider
from agent_core.runtime.dependency_authority_control import (
    DisabledDependencyAuthorityControlProvider,
)
from agent_core.runtime.dependency_authority_signed_provider import (
    SignedRecordDependencyAuthorityControlProvider,
    build_dependency_authority_signed_record,
    dependency_authority_signed_record_integrity,
    dependency_authority_signed_record_signing_bytes,
)
from app.services.agent_service import AgentService
from app.services.dependency_authority_composition import (
    build_dependency_authority_control_composition,
)


_TEST_SECRET = b"stage4k1-test-only-signing-secret"


class _StaticSource:
    def __init__(self, record):
        self.record = record

    def load_signed_record(self):
        return self.record


class _RaisingSource:
    def load_signed_record(self):
        raise RuntimeError("source-secret-must-not-escape")


class _HmacVerifier:
    def __init__(self, *, accept: bool = True, raises: bool = False):
        self.accept = accept
        self.raises = raises

    def verify(self, *, signer_id, signature_scheme, message, signature):
        if self.raises:
            raise RuntimeError("verifier-secret-must-not-escape")
        if not self.accept:
            return False
        if signer_id != "release-governance":
            return False
        if signature_scheme != "hmac-sha256-test-only":
            return False
        expected = hmac.new(_TEST_SECRET, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def _record(**overrides):
    values = {
        "provider_id": "governance-control-plane",
        "revision": "rev-0002",
        "control_epoch": 7,
        "signer_id": "release-governance",
        "signature_scheme": "hmac-sha256-test-only",
        "issued_at": 900.0,
        "expires_at": 1200.0,
        "signature": "placeholder",
        "activation_preflight": {"preflight_digest": "b" * 64},
        "runtime_activation": {"activation_digest": "c" * 64},
        "rollback_requested": False,
    }
    values.update(overrides)
    unsigned = build_dependency_authority_signed_record(**values)
    values["signature"] = hmac.new(
        _TEST_SECRET,
        dependency_authority_signed_record_signing_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return build_dependency_authority_signed_record(**values)


def _provider(record, *, now=1000.0, verifier=None):
    return SignedRecordDependencyAuthorityControlProvider(
        source=_StaticSource(record),
        signature_verifier=verifier or _HmacVerifier(),
        evaluation_time_resolver=lambda: now,
    )


def test_stage4k1_module_owns_no_environment_model_or_signing_secret_configuration() -> None:
    source = inspect.getsource(signed_provider)
    assert "os.getenv" not in source
    assert "agent_core.config" not in source
    assert "OPENAI_API_KEY" not in source
    assert "OPENAI_MODEL" not in source
    assert "PRODUCTION_MODEL_API_KEY" not in source
    assert "_TEST_SECRET" not in source


def test_stage4k1_signed_provider_remains_indirect_and_default_disabled(monkeypatch) -> None:
    service_source = inspect.getsource(AgentService._compose_runtime_deps)
    assert "build_dependency_authority_control_composition" in service_source
    assert "SignedRecordDependencyAuthorityControlProvider" not in service_source

    monkeypatch.delenv("DEPENDENCY_AUTHORITY_CONTROL_MODE", raising=False)
    composition = build_dependency_authority_control_composition(
        store_provider=object()
    )
    assert isinstance(
        composition.provider,
        DisabledDependencyAuthorityControlProvider,
    )


def test_valid_cryptographically_verified_record_resolves_stage4f_control_and_head_identity() -> None:
    record = _record()
    resolved = _provider(record).resolve()
    assert resolved == {
        "activation_preflight": {"preflight_digest": "b" * 64},
        "runtime_activation": {"activation_digest": "c" * 64},
        "evaluation_time": 1000.0,
        "rollback_requested": False,
        "control_head_identity": {
            "control_epoch": 7,
            "revision": "rev-0002",
            "snapshot_digest": record["record_digest"],
        },
    }
    assert "signer_id" not in resolved
    assert "signature" not in resolved
    assert "provider_id" not in resolved


def test_provider_uses_trusted_clock_not_signed_record_for_evaluation_time() -> None:
    record = _record()
    assert "evaluation_time" not in record["control"]
    resolved = _provider(record, now=1100.0).resolve()
    assert resolved["evaluation_time"] == 1100.0


def test_signed_record_cannot_self_assert_signature_verification() -> None:
    record = _record()
    record["signature_verified"] = True
    integrity = dependency_authority_signed_record_integrity(record)
    assert integrity["ok"] is False
    assert "SIGNED_RECORD_UNKNOWN_FIELDS_FORBIDDEN" in integrity["errors"]
    assert _provider(record).resolve() is None


def test_recomputed_record_digests_do_not_bypass_cryptographic_signature() -> None:
    original = _record()
    tampered = build_dependency_authority_signed_record(
        provider_id=original["provider_id"],
        revision=original["revision"],
        control_epoch=original["control_epoch"],
        signer_id=original["signer_id"],
        signature_scheme=original["signature_scheme"],
        issued_at=original["issued_at"],
        expires_at=original["expires_at"],
        signature=original["signature"],
        activation_preflight={"preflight_digest": "tampered"},
        runtime_activation=original["control"]["runtime_activation"],
        rollback_requested=False,
    )
    assert dependency_authority_signed_record_integrity(tampered)["ok"] is True
    assert tampered["payload_digest"] != original["payload_digest"]
    assert tampered["record_digest"] != original["record_digest"]
    assert _provider(tampered).resolve() is None


def test_untrusted_signer_and_unsupported_scheme_fail_closed() -> None:
    assert _provider(_record(signer_id="attacker")).resolve() is None
    assert _provider(_record(signature_scheme="unknown-v9")).resolve() is None


def test_signature_rejection_and_verifier_error_fail_closed() -> None:
    record = _record()
    assert _provider(record, verifier=_HmacVerifier(accept=False)).resolve() is None
    assert _provider(record, verifier=_HmacVerifier(raises=True)).resolve() is None


def test_source_error_and_missing_record_fail_closed() -> None:
    raising = SignedRecordDependencyAuthorityControlProvider(
        source=_RaisingSource(),
        signature_verifier=_HmacVerifier(),
        evaluation_time_resolver=lambda: 1000.0,
    )
    missing = SignedRecordDependencyAuthorityControlProvider(
        source=_StaticSource(None),
        signature_verifier=_HmacVerifier(),
        evaluation_time_resolver=lambda: 1000.0,
    )
    assert raising.resolve() is None
    assert missing.resolve() is None


def test_expired_future_and_invalid_clock_records_fail_closed() -> None:
    record = _record()
    assert _provider(record, now=1200.0).resolve() is None
    assert _provider(record, now=899.0).resolve() is None

    broken_clock = SignedRecordDependencyAuthorityControlProvider(
        source=_StaticSource(record),
        signature_verifier=_HmacVerifier(),
        evaluation_time_resolver=lambda: "not-a-time",
    )
    nan_clock = SignedRecordDependencyAuthorityControlProvider(
        source=_StaticSource(record),
        signature_verifier=_HmacVerifier(),
        evaluation_time_resolver=lambda: float("nan"),
    )
    assert broken_clock.resolve() is None
    assert nan_clock.resolve() is None


def test_control_epoch_and_expiry_order_are_structural_requirements() -> None:
    record = _record()
    record["control_epoch"] = 0
    assert "SIGNED_RECORD_CONTROL_EPOCH_REQUIRED" in (
        dependency_authority_signed_record_integrity(record)["errors"]
    )

    invalid_expiry = build_dependency_authority_signed_record(
        provider_id="governance-control-plane",
        revision="rev-0002",
        control_epoch=7,
        signer_id="release-governance",
        signature_scheme="hmac-sha256-test-only",
        issued_at=1000.0,
        expires_at=999.0,
        signature="placeholder",
    )
    assert "SIGNED_RECORD_EXPIRY_ORDER_INVALID" in (
        dependency_authority_signed_record_integrity(invalid_expiry)["errors"]
    )


def test_json_roundtrip_preserves_verified_resolution() -> None:
    record = _record()
    restored = json.loads(json.dumps(record, sort_keys=True))
    assert dependency_authority_signed_record_integrity(restored)["ok"] is True
    assert _provider(record).resolve() == _provider(restored).resolve()


def test_model_environment_changes_do_not_change_signed_provider_resolution(monkeypatch) -> None:
    provider = _provider(_record())
    before = provider.resolve()
    monkeypatch.setenv("OPENAI_API_KEY", "different-key")
    monkeypatch.setenv("OPENAI_MODEL", "different-model")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid")
    after = provider.resolve()
    assert before == after
    assert os.environ["OPENAI_MODEL"] == "different-model"


def test_rollback_record_remains_control_only_and_preserves_verified_head_identity() -> None:
    record = _record(
        activation_preflight=None,
        runtime_activation=None,
        rollback_requested=True,
    )
    resolved = _provider(record).resolve()
    assert resolved == {
        "activation_preflight": None,
        "runtime_activation": None,
        "evaluation_time": 1000.0,
        "rollback_requested": True,
        "control_head_identity": {
            "control_epoch": 7,
            "revision": "rev-0002",
            "snapshot_digest": record["record_digest"],
        },
    }
