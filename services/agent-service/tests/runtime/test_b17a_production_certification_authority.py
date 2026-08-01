from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[4]

PGVECTOR_IMAGE_REFERENCE = "pgvector/pgvector@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
PGVECTOR_IMAGE_ID = "sha256:" + "9" * 64


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _contract():
    return _load("production_certification_contract_b17a", ROOT / "scripts" / "production_certification_contract.py")


def _controller():
    return _load("production_certification_controller_b17a", ROOT / "scripts" / "verify_production_certification_bundle.py")


def _quality_loop():
    return _load("quality_loop_b17a", ROOT / "scripts" / "quality_loop.py")


def _identity() -> dict[str, Any]:
    return {
        "contract": "real-model-identity@1",
        "provider": "openai",
        "endpoint": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "credential_fingerprint_sha256_16": "0123456789abcdef",
        "official_endpoint": True,
        "https": True,
    }


def _runtime_authority() -> dict[str, Any]:
    return {
        "contract": "protected-browser-runtime-authority@1",
        "runtime_profile": "preprod",
        "auth_provider": "jwt_hs256",
        "dev_login_enabled": False,
        "actor_signature_required": True,
        "agent_db_backend": "postgres",
        "checkpoint_backend": "postgres",
        "business_db_backend": "postgres",
        "rag_backend": "pgvector",
        "document_job_backend": "sqlalchemy",
        "document_object_store_backend": "shared_filesystem",
        "strict_persistence": True,
        "state_contract_mode": "strict",
        "single_postgres_authority": True,
        "verifier_modes": {
            "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
            "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
            "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
        },
    }


def _session(env: dict[str, str], component: str) -> dict[str, Any]:
    return {
        "contract": "production-certification-session@1",
        "mode": "bundle",
        "session_id": env["PRODUCTION_CERTIFICATION_SESSION_ID"],
        "workspace_fingerprint_sha256": env["PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT"],
        "toolchain_fingerprint_sha256": env["PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT"],
        "component": component,
        "started_at": env["PRODUCTION_CERTIFICATION_SESSION_STARTED_AT"],
        "emitted_at": datetime.now(timezone.utc).isoformat(),
    }


def _component_payload(component: str, env: dict[str, str]) -> dict[str, Any]:
    if component == "real_model":
        return {
            "contract": "production-real-model-certification@1",
            "status": "PASS",
            "production_session": _session(env, component),
            "real_model_bundle": {
                "contract": "real-model-certification-bundle@1",
                "status": "PASS",
                "session_id": "rmcert-" + "1" * 48,
                "workspace_fingerprint_sha256": env["PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT"],
                "identity": _identity(),
                "component_count": 3,
                "components": ["smoke", "semantic", "lifecycle"],
                "attested_model_calls_by_component": {"smoke": 1, "semantic": 12, "lifecycle": 2},
                "total_attested_model_calls": 15,
            },
        }
    if component == "postgres":
        return {
            "contract": "production-postgres-certification@1",
            "status": "PASS",
            "production_session": _session(env, component),
            "database_instance_fingerprint_sha256_16": "abcdef0123456789",
            "server_version_num": "160004",
            "pgvector_extension": True,
            "integration_test_file_count": 3,
            "integration_test_case_count": 3,
            "container_image_reference": PGVECTOR_IMAGE_REFERENCE,
            "container_image_id_sha256": PGVECTOR_IMAGE_ID,
            "recovery": {
                "contract": "managed-postgres-public-restart-recovery@1",
                "status": "PASS",
                "restart_count": 2,
                "agent_instance_count": 2,
                "concurrent_authority_attempts": 2,
                "idempotency_replay": True,
                "persistence": "owned_postgresql",
            },
        }
    runtime = _runtime_authority()
    database_fingerprint = "76543210abcdefab"
    journey_evidence = [
        {
            "journey": name,
            "contract": "protected-browser-journey-runtime@1",
            "status": "PASS",
            "runtime_authority": runtime,
            "database_instance_fingerprint_sha256_16": database_fingerprint,
            "e2e_stdout_sha256_16": "abcdef0123456789",
        }
        for name in ("configured-strong-context", "configured-strong-context-campaign")
    ]
    return {
        "contract": "production-browser-certification@1",
        "status": "PASS",
        "production_session": _session(env, component),
        "identity": _identity(),
        "journeys": ["configured-strong-context", "configured-strong-context-campaign"],
        "journey_count": 2,
        "protected_runtime_journey_count": 2,
        "journey_evidence": journey_evidence,
        "runtime_authority": runtime,
        "database_instance_fingerprint_sha256_16": database_fingerprint,
        "pgvector_extension": True,
        "container_image_reference": PGVECTOR_IMAGE_REFERENCE,
        "container_image_id_sha256": PGVECTOR_IMAGE_ID,
        "browser_version": "Chromium 149.0.7827.55",
        "browser_executable_sha256_16": "1234567890abcdef",
    }


def _valid_assessment() -> dict[str, Any]:
    return {
        "contract": "production-certification-bundle@1",
        "status": "PASS",
        "session_id": "prodcert-" + "a" * 48,
        "workspace_fingerprint_sha256": "b" * 64,
        "toolchain_fingerprint_sha256": "c" * 64,
        "components": ["real_model", "postgres", "browser"],
        "component_count": 3,
        "real_model_identity": _identity(),
        "real_model_total_attested_calls": 15,
        "postgres_database_instance_fingerprint_sha256_16": "abcdef0123456789",
        "postgres_container_image_reference": PGVECTOR_IMAGE_REFERENCE,
        "postgres_container_image_id_sha256": PGVECTOR_IMAGE_ID,
        "postgres_restart_count": 2,
        "browser_version": "Chromium 149.0.7827.55",
        "browser_journey_count": 2,
        "evidence_scope": "single-live-production-certification-session",
    }


def test_release_policy_has_one_production_authority_and_no_independent_release_components() -> None:
    policy = json.loads((ROOT / "governance" / "quality-loop-policy.json").read_text(encoding="utf-8"))
    by_id = {step["id"]: step for step in policy["steps"]}
    assert "production-certification-bundle" in by_id
    assert by_id["production-certification-bundle"]["modes"] == ["release"]
    assert by_id["production-certification-bundle"]["argv"][-1] == "scripts/verify_production_certification_bundle.py"
    assert "preproduction-real-model-certification-bundle" not in by_id
    assert "configured-model-browser-conversation" not in by_id
    assert "configured-model-browser-campaign" not in by_id
    assert "production-certification-bundle" in by_id["clean-release-preflight"]["depends_on"]


def test_live_controller_closes_one_session_with_all_three_components(monkeypatch, tmp_path: Path) -> None:
    controller = _controller()
    monkeypatch.setattr(controller, "validate_runtime_evidence", lambda *args, **kwargs: {"status": "PASS"})
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    launched: list[str] = []

    def runner(*, component: str, env: dict[str, str], **_: Any) -> dict[str, Any]:
        launched.append(component)
        return _component_payload(component, env)

    result = controller.run_production_certification_bundle(
        workspace_root=tmp_path,
        env={
            "PRODUCTION_CERTIFICATION_TOOLCHAIN_EVIDENCE": str(tmp_path / "toolchain.json"),
            "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": "c" * 64,
        },
        component_runner=runner,
    )
    assert result["status"] == "PASS"
    assert result["contract"] == "production-certification-bundle@1"
    assert result["components"] == ["real_model", "postgres", "browser"]
    assert result["component_launch_count"] == 3
    assert launched == ["real_model", "postgres", "browser"]


def test_controller_stops_on_environment_block_and_never_upgrades_partial_evidence(monkeypatch, tmp_path: Path) -> None:
    controller = _controller()
    monkeypatch.setattr(controller, "validate_runtime_evidence", lambda *args, **kwargs: {"status": "PASS"})
    (tmp_path / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    launched: list[str] = []

    def runner(*, component: str, env: dict[str, str], **_: Any) -> dict[str, Any]:
        launched.append(component)
        payload = _component_payload(component, env)
        if component == "postgres":
            payload["status"] = "BLOCKED_BY_ENVIRONMENT"
            payload["reason"] = "docker_unavailable"
        return payload

    result = controller.run_production_certification_bundle(
        workspace_root=tmp_path,
        env={
            "PRODUCTION_CERTIFICATION_TOOLCHAIN_EVIDENCE": str(tmp_path / "toolchain.json"),
            "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": "c" * 64,
        },
        component_runner=runner,
    )
    assert result["status"] == "BLOCKED_BY_ENVIRONMENT"
    assert result["blocked_component"] == "postgres"
    assert launched == ["real_model", "postgres"]


def test_controller_rejects_workspace_mutation_during_live_session(monkeypatch, tmp_path: Path) -> None:
    controller = _controller()
    monkeypatch.setattr(controller, "validate_runtime_evidence", lambda *args, **kwargs: {"status": "PASS"})
    source = tmp_path / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    def runner(*, component: str, env: dict[str, str], **_: Any) -> dict[str, Any]:
        if component == "browser":
            source.write_text("VALUE = 2\n", encoding="utf-8")
        return _component_payload(component, env)

    with pytest.raises(Exception, match="workspace changed"):
        controller.run_production_certification_bundle(
            workspace_root=tmp_path,
            env={
                "PRODUCTION_CERTIFICATION_TOOLCHAIN_EVIDENCE": str(tmp_path / "toolchain.json"),
                "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": "c" * 64,
            },
            component_runner=runner,
        )


def test_validator_rejects_replayed_session_and_cross_workspace_component() -> None:
    contract = _contract()
    now = datetime.now(timezone.utc)
    session_id = "prodcert-" + "c" * 48
    fingerprint = "d" * 64
    env = {
        "PRODUCTION_CERTIFICATION_SESSION_ID": session_id,
        "PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT": fingerprint,
        "PRODUCTION_CERTIFICATION_SESSION_STARTED_AT": now.isoformat(),
        "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": "a" * 64,
    }
    components = {}
    for name in ("real_model", "postgres", "browser"):
        component_env = {**env, "PRODUCTION_CERTIFICATION_COMPONENT": name}
        components[name] = _component_payload(name, component_env)
    components["browser"]["production_session"]["session_id"] = "prodcert-" + "e" * 48
    with pytest.raises(Exception, match="another session"):
        contract.validate_production_components(
            components=components,
            session_id=session_id,
            workspace_fingerprint_sha256=fingerprint,
            toolchain_fingerprint_sha256="a" * 64,
            started_at=now,
            completed_workspace_fingerprint_sha256=fingerprint,
        )


def test_validator_rejects_browser_model_identity_mismatch() -> None:
    contract = _contract()
    now = datetime.now(timezone.utc)
    session_id = "prodcert-" + "f" * 48
    fingerprint = "1" * 64
    env = {
        "PRODUCTION_CERTIFICATION_SESSION_ID": session_id,
        "PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT": fingerprint,
        "PRODUCTION_CERTIFICATION_SESSION_STARTED_AT": now.isoformat(),
        "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": "a" * 64,
    }
    components = {
        name: _component_payload(name, {**env, "PRODUCTION_CERTIFICATION_COMPONENT": name})
        for name in ("real_model", "postgres", "browser")
    }
    components["browser"]["identity"]["model"] = "different-model"
    with pytest.raises(Exception, match="identities differ"):
        contract.validate_production_components(
            components=components,
            session_id=session_id,
            workspace_fingerprint_sha256=fingerprint,
            toolchain_fingerprint_sha256="a" * 64,
            started_at=now,
            completed_workspace_fingerprint_sha256=fingerprint,
        )


def test_validator_rejects_postgres_without_pgvector_or_recovery_authority() -> None:
    contract = _contract()
    now = datetime.now(timezone.utc)
    session_id = "prodcert-" + "2" * 48
    fingerprint = "3" * 64
    env = {
        "PRODUCTION_CERTIFICATION_SESSION_ID": session_id,
        "PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT": fingerprint,
        "PRODUCTION_CERTIFICATION_SESSION_STARTED_AT": now.isoformat(),
        "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": "a" * 64,
    }
    components = {
        name: _component_payload(name, {**env, "PRODUCTION_CERTIFICATION_COMPONENT": name})
        for name in ("real_model", "postgres", "browser")
    }
    components["postgres"]["pgvector_extension"] = False
    with pytest.raises(Exception, match="integration evidence is incomplete"):
        contract.validate_production_components(
            components=components,
            session_id=session_id,
            workspace_fingerprint_sha256=fingerprint,
            toolchain_fingerprint_sha256="a" * 64,
            started_at=now,
            completed_workspace_fingerprint_sha256=fingerprint,
        )


def test_release_dimensions_are_derived_from_one_production_bundle() -> None:
    quality_loop = _quality_loop()
    results = [{
        "id": "production-certification-bundle",
        "status": "PASS",
        "category": "preproduction",
        "metadata": {"structured_assessment": _valid_assessment()},
    }]
    dimensions = quality_loop._quality_dimensions(results, mode="release")
    assert dimensions["production_certification"]["status"] == "PASS"
    assert dimensions["real_model_certification"]["status"] == "PASS"
    assert dimensions["real_model_certification"]["bundle_contract"] == "production-certification-bundle@1"


def test_three_independent_green_environment_results_cannot_form_production_bundle() -> None:
    quality_loop = _quality_loop()
    results = [
        {"id": "preproduction-real-model-certification-bundle", "status": "PASS", "category": "preproduction"},
        {"id": "configured-model-browser-conversation", "status": "PASS", "category": "integration"},
        {"id": "configured-model-browser-campaign", "status": "PASS", "category": "integration"},
        {"id": "managed-postgres-recovery", "status": "PASS", "category": "integration"},
    ]
    dimensions = quality_loop._quality_dimensions(results, mode="release")
    assert dimensions["production_certification"]["status"] == "FAIL"
    assert dimensions["production_certification"]["reason"] == "required_production_bundle_gate_missing"
