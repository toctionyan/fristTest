from __future__ import annotations

import pytest

from agent_core.config import validate_production_security
from agent_core.lifecycle.goal_planning import _goal_alignment_mode
from agent_core.runtime.answer_release_alignment import _mode as answer_alignment_mode
from agent_core.runtime.semantic_capability_verifier import _profile_mode as capability_alignment_mode


def _protected_agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "APP_PROFILE": "production",
        "AGENT_REQUIRE_AUTH": "true",
        "AGENT_AUTH_PROVIDER": "jwt_hs256",
        "AGENT_JWT_SECRET": "a-strong-production-jwt-secret-1234567890",
        "BUSINESS_SERVICE_TOKEN": "a-strong-business-service-token-1234567890",
        "BUSINESS_REQUIRE_ACTOR_SIGNATURE": "true",
        "BUSINESS_ACTOR_SIGNING_SECRET": "a-strong-actor-signing-secret-1234567890",
        "AGENT_ALLOWED_ORIGINS": "https://console.example.com",
        "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
        "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
        "CHECKPOINT_BACKEND": "postgres",
        "STRICT_PERSISTENCE": "true",
        "STATE_CONTRACT_MODE": "strict",
        "TRACE_REDACTION_MODE": "standard",
        "TRACE_RETENTION_DAYS": "30",
        "AGENT_DB_BACKEND": "postgres",
        "RAG_BACKEND": "pgvector",
        "DOCUMENT_JOB_BACKEND": "sqlalchemy",
        "DOCUMENT_JOB_DATABASE_URL": "postgresql+psycopg://agent:secret@db.example/agent",
        "DOCUMENT_OBJECT_STORE_BACKEND": "shared_filesystem",
        "DOCUMENT_OBJECT_STORE_ROOT": "/mnt/shared/document-objects",
        "CONVERSATION_LOCK_TTL_SECONDS": "300",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize(
    "name",
    [
        "CAPABILITY_SEMANTIC_VERIFIER_MODE",
        "GOAL_ALIGNMENT_VERIFIER_MODE",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE",
    ],
)
@pytest.mark.parametrize("unsafe_mode", ["disabled", "candidate"])
def test_protected_profile_rejects_verifier_downgrade(
    monkeypatch: pytest.MonkeyPatch, name: str, unsafe_mode: str
) -> None:
    _protected_agent_env(monkeypatch)
    monkeypatch.setenv(name, unsafe_mode)
    with pytest.raises(RuntimeError, match=name):
        validate_production_security()


def test_protected_profile_accepts_only_complete_security_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _protected_agent_env(monkeypatch)
    validate_production_security()


@pytest.mark.parametrize(
    "resolver",
    [capability_alignment_mode, _goal_alignment_mode, answer_alignment_mode],
)
def test_runtime_verifier_resolution_is_fail_closed_even_without_startup_validation(
    monkeypatch: pytest.MonkeyPatch, resolver
) -> None:
    monkeypatch.setenv("APP_PROFILE", "production")
    env_name = {
        capability_alignment_mode: "CAPABILITY_SEMANTIC_VERIFIER_MODE",
        _goal_alignment_mode: "GOAL_ALIGNMENT_VERIFIER_MODE",
        answer_alignment_mode: "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE",
    }[resolver]
    monkeypatch.setenv(env_name, "disabled")
    with pytest.raises(RuntimeError, match=env_name):
        resolver()


def test_protected_agent_requires_signed_business_actor_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _protected_agent_env(monkeypatch)
    monkeypatch.setenv("BUSINESS_REQUIRE_ACTOR_SIGNATURE", "false")
    with pytest.raises(RuntimeError, match="BUSINESS_REQUIRE_ACTOR_SIGNATURE"):
        validate_production_security()


def test_protected_agent_rejects_sqlalchemy_document_queue_backed_by_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _protected_agent_env(monkeypatch)
    monkeypatch.setenv("DOCUMENT_JOB_DATABASE_URL", "sqlite:////tmp/document-jobs.db")
    with pytest.raises(RuntimeError, match="DOCUMENT_JOB_DATABASE_URL must use PostgreSQL"):
        validate_production_security()
