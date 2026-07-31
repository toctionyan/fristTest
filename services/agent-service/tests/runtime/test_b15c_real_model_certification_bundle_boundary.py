from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _identity(*, model: str = "gpt-4o-mini", fingerprint: str = "a" * 16) -> dict:
    return {
        "contract": "real-model-identity@1",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "endpoint_host": "api.openai.com",
        "model": model,
        "credential_fingerprint_sha256_16": fingerprint,
        "official_endpoint": True,
        "https": True,
    }


def _session(component: str, *, session_id: str = "session-1234567890abcdef", workspace: str = "b" * 64) -> dict:
    import hashlib
    identity = _identity()
    projected = {key: identity[key] for key in ("provider", "endpoint", "model", "credential_fingerprint_sha256_16")}
    encoded = json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "contract": "real-model-certification-session@1",
        "mode": "bundle",
        "session_id": session_id,
        "workspace_fingerprint_sha256": workspace,
        "component": component,
        "identity_fingerprint_sha256": hashlib.sha256(encoded).hexdigest(),
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }


def _components() -> dict[str, dict]:
    identity = _identity()
    return {
        "smoke": {
            "status": "PASS",
            "identity": identity,
            "certification_session": _session("smoke"),
            "attestation": {"contract": "real-model-response-attestation@1"},
            "calls": {"total_calls": 1, "successful_calls": 1},
        },
        "semantic": {
            "status": "PASS",
            "identity": identity,
            "certification_session": _session("semantic"),
            "prototype_count": 12,
            "calls": {"total_calls": 12, "successful_calls": 12},
            "cases": [
                {"case_id": f"case-{index}", "provider_attestation": {"contract": "real-model-metadata-attestation@1"}}
                for index in range(12)
            ],
        },
        "lifecycle": {
            "status": "PASS",
            "identity": identity,
            "certification_session": _session("lifecycle"),
            "turns": 2,
            "model_attestations": [
                {"turn": 1, "call_count": 2, "total_tokens": 20},
                {"turn": 2, "call_count": 2, "total_tokens": 24},
            ],
            "transaction_delta": 0,
        },
    }


def test_bundle_accepts_only_matching_live_three_component_evidence() -> None:
    from agent_core.model_calls.real_model_certification_bundle import validate_certification_components

    result = validate_certification_components(
        components=_components(),
        session_id="session-1234567890abcdef",
        workspace_fingerprint="b" * 64,
    )
    assert result["status"] == "PASS"
    assert result["component_count"] == 3
    assert result["total_attested_model_calls"] >= 17


def test_bundle_rejects_mismatched_provider_identity() -> None:
    from agent_core.model_calls.real_model_certification_bundle import (
        RealModelBundleError,
        validate_certification_components,
    )

    components = _components()
    components["semantic"]["identity"] = _identity(model="gpt-4.1-mini")
    with pytest.raises(RealModelBundleError) as captured:
        validate_certification_components(
            components=components,
            session_id="session-1234567890abcdef",
            workspace_fingerprint="b" * 64,
        )
    assert captured.value.code == "component_identity_mismatch"


def test_bundle_rejects_replayed_or_mismatched_session() -> None:
    from agent_core.model_calls.real_model_certification_bundle import (
        RealModelBundleError,
        validate_certification_components,
    )

    components = _components()
    components["lifecycle"]["certification_session"] = _session(
        "lifecycle", session_id="old-replayed-session-0001"
    )
    with pytest.raises(RealModelBundleError) as captured:
        validate_certification_components(
            components=components,
            session_id="session-1234567890abcdef",
            workspace_fingerprint="b" * 64,
        )
    assert captured.value.code == "component_session_mismatch"


def test_bundle_rejects_workspace_fingerprint_mismatch() -> None:
    from agent_core.model_calls.real_model_certification_bundle import (
        RealModelBundleError,
        validate_certification_components,
    )

    components = _components()
    components["smoke"]["certification_session"] = _session("smoke", workspace="c" * 64)
    with pytest.raises(RealModelBundleError) as captured:
        validate_certification_components(
            components=components,
            session_id="session-1234567890abcdef",
            workspace_fingerprint="b" * 64,
        )
    assert captured.value.code == "component_workspace_mismatch"


def test_bundle_rejects_missing_component_and_weak_semantic_evidence() -> None:
    from agent_core.model_calls.real_model_certification_bundle import (
        RealModelBundleError,
        validate_certification_components,
    )

    components = _components()
    components.pop("lifecycle")
    with pytest.raises(RealModelBundleError) as captured:
        validate_certification_components(
            components=components,
            session_id="session-1234567890abcdef",
            workspace_fingerprint="b" * 64,
        )
    assert captured.value.code == "required_component_missing"

    components = _components()
    components["semantic"]["prototype_count"] = 1
    components["semantic"]["cases"] = components["semantic"]["cases"][:1]
    with pytest.raises(RealModelBundleError) as captured:
        validate_certification_components(
            components=components,
            session_id="session-1234567890abcdef",
            workspace_fingerprint="b" * 64,
        )
    assert captured.value.code == "semantic_coverage_insufficient"


def test_controller_missing_key_blocks_before_component_launch(monkeypatch, tmp_path: Path) -> None:
    from agent_core.model_calls.real_model_certification_bundle import run_certification_bundle

    launches: list[str] = []

    def forbidden_runner(*_args, **_kwargs):
        launches.append("launched")
        raise AssertionError("components must not launch without a real provider key")

    env = {"OPENAI_MODEL": "gpt-4o-mini"}
    result = run_certification_bundle(
        workspace_root=tmp_path,
        env=env,
        component_runner=forbidden_runner,
    )
    assert result["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert result["reason"] == "real_model_environment_unavailable"
    assert result["error_code"] == "api_key_missing"
    assert launches == []


def test_bundle_controller_runs_three_components_under_one_generated_session(tmp_path: Path) -> None:
    from agent_core.model_calls.real_model_certification_bundle import run_certification_bundle

    scripts = tmp_path / "services" / "agent-service" / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "verify_model_smoke.py",
        "verify_preprod_conversation_smoke.py",
        "verify_preprod_full_lifecycle.py",
    ):
        (scripts / name).write_text("# controlled test component\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("20.17-test\n", encoding="utf-8")

    observed: list[tuple[str, str, str]] = []
    provider_env = {
        "OPENAI_API_KEY": "sk-" + "z" * 48,
        "OPENAI_API_BASE": "https://api.openai.com/v1",
        "OPENAI_MODEL": "gpt-4o-mini",
    }
    from agent_core.model_calls.real_model_identity import resolve_real_model_identity
    identity = resolve_real_model_identity(provider_env)

    def runner(*, component: str, script_path: Path, env: dict, workspace_root: Path) -> dict:
        assert script_path.is_file()
        assert workspace_root == tmp_path
        session_id = env["REAL_MODEL_CERTIFICATION_SESSION_ID"]
        workspace = env["REAL_MODEL_CERTIFICATION_WORKSPACE_FINGERPRINT"]
        observed.append((component, session_id, workspace))
        session = {
            "contract": "real-model-certification-session@1",
            "mode": "bundle",
            "session_id": session_id,
            "workspace_fingerprint_sha256": workspace,
            "component": component,
            "started_at": env["REAL_MODEL_CERTIFICATION_SESSION_STARTED_AT"],
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        }
        if component == "smoke":
            return {
                "status": "PASS", "identity": identity, "certification_session": session,
                "attestation": {"contract": "real-model-response-attestation@1"},
                "calls": {"used_calls": 1},
            }
        if component == "semantic":
            return {
                "status": "PASS", "identity": identity, "certification_session": session,
                "prototype_count": 12, "calls": {"used_calls": 12},
                "cases": [
                    {"provider_attestation": {"contract": "real-model-metadata-attestation@1"}}
                    for _ in range(12)
                ],
            }
        return {
            "status": "PASS", "identity": identity, "certification_session": session,
            "turns": 2, "transaction_delta": 0,
            "model_attestations": [
                {"turn": 1, "call_count": 2, "total_tokens": 20},
                {"turn": 2, "call_count": 2, "total_tokens": 24},
            ],
        }

    result = run_certification_bundle(
        workspace_root=tmp_path,
        env=provider_env,
        component_runner=runner,
    )
    assert result["status"] == "PASS"
    assert result["components_started"] == 3
    assert [row[0] for row in observed] == ["smoke", "semantic", "lifecycle"]
    assert len({row[1] for row in observed}) == 1
    assert len({row[2] for row in observed}) == 1
    assert "sk-" not in json.dumps(result)


def test_bundle_rejects_standalone_component_evidence() -> None:
    from agent_core.model_calls.real_model_certification_bundle import (
        RealModelBundleError,
        validate_certification_components,
    )

    components = _components()
    components["smoke"]["certification_session"]["mode"] = "standalone"
    with pytest.raises(RealModelBundleError) as captured:
        validate_certification_components(
            components=components,
            session_id="session-1234567890abcdef",
            workspace_fingerprint="b" * 64,
        )
    assert captured.value.code == "component_session_mode_invalid"
