from __future__ import annotations

import inspect
import json
import os

import agent_core.runtime.dependency_authority_control as control
from agent_core.runtime.dependency_authority_control import (
    DisabledDependencyAuthorityControlProvider,
    StaticVerifiedDependencyAuthorityControlProvider,
    build_dependency_authority_control_snapshot,
    dependency_authority_control_resolver,
    dependency_authority_control_snapshot_integrity,
)
from app.services.agent_service import AgentService


def _snapshot(**overrides):
    values = {
        "provider_id": "governance-control-plane",
        "revision": "rev-0001",
        "signer_id": "release-governance",
        "signature_scheme": "external-verifier-v1",
        "signed_record_digest": "a" * 64,
        "signature_verified": True,
        "activation_preflight": {"preflight_digest": "b" * 64},
        "runtime_activation": {"activation_digest": "c" * 64},
        "evaluation_time": 1000.0,
        "rollback_requested": False,
    }
    values.update(overrides)
    return build_dependency_authority_control_snapshot(**values)


def test_stage4h_contract_does_not_own_main_environment_or_model_configuration() -> None:
    source = inspect.getsource(control)
    assert "os.getenv" not in source
    assert "agent_core.config" not in source
    assert "OPENAI_API_KEY" not in source
    assert "OPENAI_MODEL" not in source
    snapshot = _snapshot()
    assert snapshot["owns_environment_configuration"] is False
    assert snapshot["owns_model_configuration"] is False
    assert "dependency_authority_control_resolver=" not in inspect.getsource(
        AgentService.__init__
    )


def test_disabled_provider_is_explicitly_fail_closed() -> None:
    provider = DisabledDependencyAuthorityControlProvider()
    assert provider.resolve() is None
    assert dependency_authority_control_resolver(provider)() is None


def test_verified_snapshot_resolves_only_stage4f_control_fields() -> None:
    snapshot = _snapshot()
    resolved = StaticVerifiedDependencyAuthorityControlProvider(snapshot).resolve()
    assert resolved == {
        "activation_preflight": {"preflight_digest": "b" * 64},
        "runtime_activation": {"activation_digest": "c" * 64},
        "evaluation_time": 1000.0,
        "rollback_requested": False,
    }
    assert "provider_id" not in resolved
    assert "signed_record_digest" not in resolved
    assert "signature_verified" not in resolved


def test_signature_verification_is_required_even_with_a_valid_snapshot_digest() -> None:
    snapshot = _snapshot(signature_verified=False)
    integrity = dependency_authority_control_snapshot_integrity(snapshot)
    assert integrity["ok"] is False
    assert "CONTROL_PROVIDER_SIGNATURE_VERIFICATION_REQUIRED" in integrity["errors"]
    assert StaticVerifiedDependencyAuthorityControlProvider(snapshot).resolve() is None


def test_tampering_provider_identity_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot["provider_id"] = "attacker"
    integrity = dependency_authority_control_snapshot_integrity(snapshot)
    assert integrity["ok"] is False
    assert "CONTROL_SNAPSHOT_DIGEST_INVALID" in integrity["errors"]
    assert StaticVerifiedDependencyAuthorityControlProvider(snapshot).resolve() is None


def test_activation_records_require_deterministic_evaluation_time() -> None:
    snapshot = _snapshot(evaluation_time=None)
    integrity = dependency_authority_control_snapshot_integrity(snapshot)
    assert integrity["ok"] is False
    assert "CONTROL_PROVIDER_EVALUATION_TIME_REQUIRED" in integrity["errors"]


def test_rollback_channel_can_exist_without_activation_records() -> None:
    snapshot = _snapshot(
        activation_preflight=None,
        runtime_activation=None,
        evaluation_time=None,
        rollback_requested=True,
    )
    integrity = dependency_authority_control_snapshot_integrity(snapshot)
    assert integrity["ok"] is True
    resolved = StaticVerifiedDependencyAuthorityControlProvider(snapshot).resolve()
    assert resolved == {
        "activation_preflight": None,
        "runtime_activation": None,
        "evaluation_time": None,
        "rollback_requested": True,
    }


def test_json_roundtrip_preserves_worker_restart_resolution() -> None:
    snapshot = _snapshot()
    restored = json.loads(json.dumps(snapshot, sort_keys=True))
    first = StaticVerifiedDependencyAuthorityControlProvider(snapshot).resolve()
    second = StaticVerifiedDependencyAuthorityControlProvider(restored).resolve()
    assert first == second
    assert dependency_authority_control_snapshot_integrity(restored)["ok"] is True


def test_model_environment_changes_do_not_change_control_resolution(monkeypatch) -> None:
    provider = StaticVerifiedDependencyAuthorityControlProvider(_snapshot())
    before = provider.resolve()
    monkeypatch.setenv("OPENAI_API_KEY", "different-key")
    monkeypatch.setenv("OPENAI_MODEL", "different-model")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid")
    after = provider.resolve()
    assert before == after
    assert os.environ["OPENAI_MODEL"] == "different-model"
