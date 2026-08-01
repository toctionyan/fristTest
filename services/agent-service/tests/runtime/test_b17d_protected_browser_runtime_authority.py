from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _harness_module():
    return _load("verify_full_lifecycle_canary_b17d", ROOT / "scripts" / "verify_full_lifecycle_canary.py")


def _browser_bundle():
    return _load("verify_production_browser_bundle_b17d", ROOT / "scripts" / "verify_production_browser_bundle.py")


def _configure_external_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "chat-key-for-b17d-tests")
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key-for-b17d-tests")
    monkeypatch.setenv("EMBEDDING_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("EMBEDDING_DIM", "1024")


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


def test_protected_harness_closes_every_local_runtime_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_external_environment(monkeypatch)
    module = _harness_module()
    url = "postgresql+psycopg://user:password@127.0.0.1:5432/runtime"
    harness = module.ProductRuntimeHarness(
        deterministic_model=False,
        persistence_url=url,
        protected_preprod=True,
        allowed_origins="http://127.0.0.1:4173",
    )
    try:
        evidence = harness.runtime_authority_evidence()
        assert evidence == _runtime_authority()
        assert harness.env["APP_PROFILE"] == "preprod"
        assert harness.env["WEB_CONSOLE_DEV_LOGIN"] == "false"
        assert harness.env["AGENT_AUTH_PROVIDER"] == "jwt_hs256"
        assert harness.env["BUSINESS_REQUIRE_ACTOR_SIGNATURE"] == "true"
        assert harness.env["BUSINESS_SEED_DEMO_DATA"] == "false"
        assert harness.browser_auth_token.count(".") == 2
        authorities = {
            harness.env[name]
            for name in (
                "AGENT_DATABASE_URL",
                "CHECKPOINT_DATABASE_URL",
                "BUSINESS_DATABASE_URL",
                "RAG_DATABASE_URL",
                "DOCUMENT_JOB_DATABASE_URL",
            )
        }
        assert authorities == {url}
        assert "SQLITE_DB_PATH" not in harness.env
        assert "BUSINESS_DB_PATH" not in harness.env
    finally:
        harness.stop()


def test_protected_runtime_prepares_migrations_and_explicit_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_external_environment(monkeypatch)
    module = _harness_module()
    harness = module.ProductRuntimeHarness(
        deterministic_model=False,
        persistence_url="postgresql+psycopg://user:password@127.0.0.1:5432/runtime",
        protected_preprod=True,
        allowed_origins="http://127.0.0.1:4173",
    )
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def record(
        command: list[str],
        *,
        cwd: Path,
        env_override=None,  # noqa: ANN001
        timeout=300,
        environment_sensitive=False,
    ) -> None:
        calls.append((command, cwd, {**dict(env_override or {}), "environment_sensitive": environment_sensitive}))

    monkeypatch.setattr(harness, "_run_management", record)
    try:
        harness._prepare_protected_runtime()  # noqa: SLF001 - certification contract test
    finally:
        harness.stop()
    assert len(calls) == 3
    assert calls[0][0][-5:] == ["-m", "alembic", "-c", "migrations/alembic.ini", "upgrade", "head"][-5:]
    assert calls[0][2]["environment_sensitive"] is False
    assert calls[1][0][-1].endswith("seed_ephemeral_fixture.py")
    assert calls[1][2] == {"BUSINESS_EPHEMERAL_FIXTURE": "true", "environment_sensitive": False}
    assert calls[2][0][-1].endswith("seed_ephemeral_rag_fixture.py")
    assert calls[2][2] == {"AGENT_EPHEMERAL_RAG_FIXTURE": "true", "environment_sensitive": True}



def test_embedding_seed_provider_outage_remains_environment_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_external_environment(monkeypatch)
    module = _harness_module()
    harness = module.ProductRuntimeHarness(
        deterministic_model=False,
        persistence_url="postgresql+psycopg://user:password@127.0.0.1:5432/runtime",
        protected_preprod=True,
        allowed_origins="http://127.0.0.1:4173",
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="embedding request failed: HTTP 429 rate limit",
            stderr="",
        ),
    )
    try:
        with pytest.raises(module.ProductRuntimeEnvironmentBlocked) as caught:
            harness._run_management(  # noqa: SLF001 - environment classification contract
                ["python", "seed_ephemeral_rag_fixture.py"],
                cwd=ROOT,
                environment_sensitive=True,
            )
        assert caught.value.reason == "protected_embedding_environment_unavailable"
        assert caught.value.diagnostics["signal"] in {"http 429", "rate limit"}
        assert "embedding request" not in json.dumps(caught.value.diagnostics)
    finally:
        harness.stop()

def test_browser_success_without_protected_attestation_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _browser_bundle()
    monkeypatch.setattr(
        bundle.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"status":"PASS"}\n', stderr=""),
    )
    exit_code, evidence = bundle._run_journey(  # noqa: SLF001 - fail-closed contract test
        ["--journey", "strong-context"],
        env_override={"PRODUCT_BROWSER_DATABASE_INSTANCE_FINGERPRINT_SHA256_16": "abcdef0123456789"},
    )
    assert exit_code == 1
    assert evidence["reason"] == "browser_journey_attestation_missing"


def test_browser_accepts_only_same_protected_postgres_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _browser_bundle()
    payload = {
        "contract": "protected-browser-journey-runtime@1",
        "status": "PASS",
        "runtime_authority": _runtime_authority(),
        "database_instance_fingerprint_sha256_16": "abcdef0123456789",
        "e2e_stdout_sha256_16": "0123456789abcdef",
    }
    monkeypatch.setattr(
        bundle.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload) + "\n", stderr=""),
    )
    exit_code, evidence = bundle._run_journey(  # noqa: SLF001 - fail-closed contract test
        ["--journey", "strong-context"],
        env_override={"PRODUCT_BROWSER_DATABASE_INSTANCE_FINGERPRINT_SHA256_16": "abcdef0123456789"},
    )
    assert exit_code == 0
    assert evidence["status"] == "PASS"
    assert evidence["runtime_authority"]["runtime_profile"] == "preprod"


def test_local_b17c_runtime_authority_is_a_negative_case() -> None:
    bundle = _browser_bundle()
    local = _runtime_authority()
    local.update({
        "runtime_profile": "local",
        "auth_provider": "dev_token",
        "dev_login_enabled": True,
        "actor_signature_required": False,
        "agent_db_backend": "sqlite",
        "checkpoint_backend": "sqlite",
        "business_db_backend": "sqlite",
        "rag_backend": "local_sparse",
        "strict_persistence": False,
        "state_contract_mode": "audit",
        "single_postgres_authority": False,
    })
    with pytest.raises(Exception, match="protected authority"):
        bundle._validate_runtime_authority(local)  # noqa: SLF001


def test_release_workflow_requires_separate_embedding_authority() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "PRODUCTION_EMBEDDING_API_KEY" in workflow
    assert "EMBEDDING_PROVIDER: openai_compatible" in workflow
    assert "EMBEDDING_MODEL: ${{ inputs.embedding_model }}" in workflow
    assert "EMBEDDING_DIM: ${{ inputs.embedding_dimension }}" in workflow
    assert "default: deepseek-v4-flash" in workflow
    assert "default: text-embedding-v4" in workflow
    assert "default: '1024'" in workflow
    assert "https://dashscope.aliyuncs.com/compatible-mode/v1" in workflow
    assert "EMBEDDING_BATCH_SIZE: '10'" in workflow
    assert "Validate protected runtime prerequisites" in workflow
    assert "Start actual protected-profile services" not in workflow


def test_all_browser_journeys_support_injected_jwt_without_dev_login_dependency() -> None:
    for name in ("product_journey.mjs", "strong_context_journey.mjs", "strong_context_campaign_journey.mjs"):
        text = (ROOT / "services" / "agent-service" / "frontend" / "e2e" / name).read_text(encoding="utf-8")
        assert "PRODUCT_BROWSER_AUTH_TOKEN" in text
        assert 'localStorage.setItem(key, token)' in text
        assert 'agent.product.token' in text
        assert "if (await" in text and ".count() === 1" in text
