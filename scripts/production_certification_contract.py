#!/usr/bin/env python3
"""Shared fail-closed contract for the final production certification bundle."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

SESSION_CONTRACT = "production-certification-session@1"
BUNDLE_CONTRACT = "production-certification-bundle@1"
COMPONENTS = ("real_model", "postgres", "browser")
SESSION_ENV = "PRODUCTION_CERTIFICATION_SESSION_ID"
WORKSPACE_ENV = "PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT"
STARTED_ENV = "PRODUCTION_CERTIFICATION_SESSION_STARTED_AT"
COMPONENT_ENV = "PRODUCTION_CERTIFICATION_COMPONENT"
TOOLCHAIN_ENV = "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT"
_SESSION_RE = re.compile(r"^prodcert-[0-9a-f]{32,96}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PGVECTOR_IMAGE_REFERENCE_RE = re.compile(r"^pgvector/pgvector@sha256:[0-9a-f]{64}$")
_CONTAINER_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def workspace_fingerprint(workspace_root: Path) -> str:
    """Fingerprint certifiable source without importing the Agent runtime."""
    root = Path(workspace_root).resolve()
    excluded_parts = {
        ".git", ".quality", ".pytest_cache", "__pycache__", "node_modules",
        ".venv", "venv", "dist", "build", "coverage", "runtime",
    }
    allowed_suffixes = {
        ".py", ".json", ".md", ".toml", ".yaml", ".yml", ".js", ".jsx",
        ".ts", ".tsx", ".css", ".html", ".sh", ".txt", ".lock",
    }
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or any(part in excluded_parts for part in path.relative_to(root).parts):
            continue
        if path.name not in {"VERSION", "Dockerfile", "Makefile"} and path.suffix.casefold() not in allowed_suffixes:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    if count == 0:
        raise ProductionCertificationError("workspace_empty", "workspace has no certifiable source files")
    return digest.hexdigest()


class ProductionCertificationError(RuntimeError):
    def __init__(self, code: str, message: str, *, environment_blocked: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.environment_blocked = bool(environment_blocked)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value: Any, *, code: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ProductionCertificationError(code, "certification timestamp is missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionCertificationError(code, "certification timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ProductionCertificationError(code, "certification timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def production_session_from_environment(*, component: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    values = {
        "session_id": str(source.get(SESSION_ENV) or "").strip(),
        "workspace_fingerprint_sha256": str(source.get(WORKSPACE_ENV) or "").strip().casefold(),
        "started_at": str(source.get(STARTED_ENV) or "").strip(),
        "component": str(source.get(COMPONENT_ENV) or "").strip(),
        "toolchain_fingerprint_sha256": str(source.get(TOOLCHAIN_ENV) or "").strip().casefold(),
    }
    if not all(values.values()):
        raise ProductionCertificationError(
            "production_session_incomplete",
            "all production certification session variables are required",
        )
    if component not in COMPONENTS or values["component"] != component:
        raise ProductionCertificationError(
            "production_component_mismatch",
            "production certification component does not match the parent assignment",
        )
    if not _SESSION_RE.fullmatch(values["session_id"]):
        raise ProductionCertificationError("production_session_id_invalid", "production session id is invalid")
    if not _SHA256_RE.fullmatch(values["workspace_fingerprint_sha256"]):
        raise ProductionCertificationError(
            "production_workspace_fingerprint_invalid",
            "production workspace fingerprint must be lowercase SHA-256",
        )
    if not _SHA256_RE.fullmatch(values["toolchain_fingerprint_sha256"]):
        raise ProductionCertificationError(
            "production_toolchain_fingerprint_invalid",
            "production toolchain fingerprint must be lowercase SHA-256",
        )
    started_at = parse_time(values["started_at"], code="production_session_started_at_invalid")
    now = utc_now()
    if started_at > now + timedelta(minutes=5) or started_at < now - timedelta(hours=8):
        raise ProductionCertificationError("production_session_stale", "production certification session is stale")
    return {
        "contract": SESSION_CONTRACT,
        "mode": "bundle",
        "session_id": values["session_id"],
        "workspace_fingerprint_sha256": values["workspace_fingerprint_sha256"],
        "toolchain_fingerprint_sha256": values["toolchain_fingerprint_sha256"],
        "component": component,
        "started_at": iso(started_at),
        "emitted_at": iso(now),
    }


def production_session_evidence(*, component: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    return production_session_from_environment(component=component, env=env)


def safe_file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]



def postgres_database_identity(url: str, *, instance_nonce: str) -> dict[str, Any]:
    """Attest one owned PostgreSQL/pgvector instance without exposing its URL."""
    try:
        import psycopg
    except ImportError as exc:
        raise ProductionCertificationError(
            "psycopg_dependency_missing",
            "PostgreSQL certification requires psycopg",
            environment_blocked=True,
        ) from exc
    safe_url = str(url).replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(safe_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            cursor.execute(
                "SELECT current_database(), current_user, current_setting('server_version_num'), "
                "pg_postmaster_start_time()::text, EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')"
            )
            database, user, server_version_num, postmaster_started_at, vector_enabled = cursor.fetchone()
    safe = {
        "database": str(database),
        "user": str(user),
        "server_version_num": str(server_version_num),
        "postmaster_started_at": str(postmaster_started_at),
        "instance_nonce": str(instance_nonce),
    }
    fingerprint = hashlib.sha256(
        __import__("json").dumps(safe, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "database_instance_fingerprint_sha256_16": fingerprint,
        "server_version_num": str(server_version_num),
        "pgvector_extension": bool(vector_enabled),
    }

def _identity_tuple(identity: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(identity.get(field) or "").strip()
        for field in ("provider", "endpoint", "model", "credential_fingerprint_sha256_16")
    )


def _validate_session(
    payload: Mapping[str, Any],
    *,
    component: str,
    session_id: str,
    workspace_fingerprint_sha256: str,
    toolchain_fingerprint_sha256: str,
    now: datetime,
) -> None:
    session = payload.get("production_session")
    if not isinstance(session, Mapping) or session.get("contract") != SESSION_CONTRACT:
        raise ProductionCertificationError("component_session_missing", f"{component} session evidence is missing")
    if session.get("mode") != "bundle" or session.get("component") != component:
        raise ProductionCertificationError("component_session_invalid", f"{component} session evidence is invalid")
    if session.get("session_id") != session_id:
        raise ProductionCertificationError("component_session_mismatch", f"{component} came from another session")
    if str(session.get("workspace_fingerprint_sha256") or "").casefold() != workspace_fingerprint_sha256:
        raise ProductionCertificationError("component_workspace_mismatch", f"{component} came from another workspace")
    if str(session.get("toolchain_fingerprint_sha256") or "").casefold() != toolchain_fingerprint_sha256:
        raise ProductionCertificationError("component_toolchain_mismatch", f"{component} came from another release toolchain")
    emitted = parse_time(session.get("emitted_at"), code="component_emitted_at_invalid")
    if emitted > now + timedelta(minutes=5) or emitted < now - timedelta(hours=8):
        raise ProductionCertificationError("component_evidence_stale", f"{component} evidence is stale")


def validate_production_components(
    *,
    components: Mapping[str, Mapping[str, Any]],
    session_id: str,
    workspace_fingerprint_sha256: str,
    toolchain_fingerprint_sha256: str,
    started_at: datetime,
    completed_workspace_fingerprint_sha256: str,
) -> dict[str, Any]:
    if set(components) != set(COMPONENTS):
        raise ProductionCertificationError("component_set_invalid", "all three production components are required")
    if not _SESSION_RE.fullmatch(session_id):
        raise ProductionCertificationError("production_session_id_invalid", "production session id is invalid")
    fingerprint = str(workspace_fingerprint_sha256 or "").strip().casefold()
    if not _SHA256_RE.fullmatch(fingerprint):
        raise ProductionCertificationError("production_workspace_fingerprint_invalid", "workspace fingerprint is invalid")
    toolchain_fingerprint = str(toolchain_fingerprint_sha256 or "").strip().casefold()
    if not _SHA256_RE.fullmatch(toolchain_fingerprint):
        raise ProductionCertificationError("production_toolchain_fingerprint_invalid", "toolchain fingerprint is invalid")
    if str(completed_workspace_fingerprint_sha256 or "").casefold() != fingerprint:
        raise ProductionCertificationError(
            "production_workspace_mutated",
            "workspace changed while production certification was running",
        )
    now = utc_now()
    if started_at > now + timedelta(minutes=5) or started_at < now - timedelta(hours=8):
        raise ProductionCertificationError("production_session_stale", "production certification session is stale")

    for component in COMPONENTS:
        payload = components[component]
        status = str(payload.get("status") or "")
        if status != "PASS":
            raise ProductionCertificationError(
                "component_not_passed",
                f"production component {component} did not pass",
                environment_blocked=status == "BLOCKED_BY_ENVIRONMENT",
            )
        _validate_session(
            payload,
            component=component,
            session_id=session_id,
            workspace_fingerprint_sha256=fingerprint,
            toolchain_fingerprint_sha256=toolchain_fingerprint,
            now=now,
        )

    model = components["real_model"]
    if model.get("contract") != "production-real-model-certification@1":
        raise ProductionCertificationError("real_model_contract_invalid", "real-model production contract is invalid")
    model_bundle = model.get("real_model_bundle")
    if not isinstance(model_bundle, Mapping) or model_bundle.get("contract") != "real-model-certification-bundle@1":
        raise ProductionCertificationError("real_model_bundle_invalid", "real-model bundle evidence is invalid")
    if model_bundle.get("status") != "PASS" or int(model_bundle.get("total_attested_model_calls") or 0) < 15:
        raise ProductionCertificationError("real_model_bundle_incomplete", "real-model bundle evidence is incomplete")
    model_identity = model_bundle.get("identity")
    if not isinstance(model_identity, Mapping) or not all(_identity_tuple(model_identity)):
        raise ProductionCertificationError("real_model_identity_invalid", "real-model identity is incomplete")
    if model_identity.get("official_endpoint") is not True or model_identity.get("https") is not True:
        raise ProductionCertificationError("real_model_identity_unofficial", "real-model identity is not official HTTPS")

    postgres = components["postgres"]
    if postgres.get("contract") != "production-postgres-certification@1":
        raise ProductionCertificationError("postgres_contract_invalid", "PostgreSQL production contract is invalid")
    recovery = postgres.get("recovery")
    if not isinstance(recovery, Mapping) or recovery.get("contract") != "managed-postgres-public-restart-recovery@1":
        raise ProductionCertificationError("postgres_recovery_invalid", "PostgreSQL recovery evidence is invalid")
    if any(int(recovery.get(field) or 0) < minimum for field, minimum in (
        ("restart_count", 2), ("agent_instance_count", 2), ("concurrent_authority_attempts", 2)
    )):
        raise ProductionCertificationError("postgres_recovery_incomplete", "PostgreSQL recovery evidence is incomplete")
    if recovery.get("idempotency_replay") is not True or recovery.get("persistence") != "owned_postgresql":
        raise ProductionCertificationError("postgres_recovery_incomplete", "PostgreSQL authority evidence is incomplete")
    if postgres.get("pgvector_extension") is not True or int(postgres.get("integration_test_file_count") or 0) < 3:
        raise ProductionCertificationError("postgres_integration_incomplete", "PostgreSQL/pgvector integration evidence is incomplete")
    if not re.fullmatch(r"[0-9a-f]{16}", str(postgres.get("database_instance_fingerprint_sha256_16") or "")):
        raise ProductionCertificationError("postgres_identity_invalid", "PostgreSQL instance identity is missing")
    postgres_image_reference = str(postgres.get("container_image_reference") or "").strip().casefold()
    postgres_image_id = str(postgres.get("container_image_id_sha256") or "").strip().casefold()
    if not _PGVECTOR_IMAGE_REFERENCE_RE.fullmatch(postgres_image_reference):
        raise ProductionCertificationError(
            "postgres_container_image_reference_invalid",
            "PostgreSQL certification must use an immutable pgvector manifest digest",
        )
    if not _CONTAINER_IMAGE_ID_RE.fullmatch(postgres_image_id):
        raise ProductionCertificationError(
            "postgres_container_image_identity_invalid",
            "PostgreSQL certification container image identity is missing",
        )

    browser = components["browser"]
    if browser.get("contract") != "production-browser-certification@1":
        raise ProductionCertificationError("browser_contract_invalid", "browser production contract is invalid")
    journeys = list(browser.get("journeys") or [])
    required_journeys = ["configured-strong-context", "configured-strong-context-campaign"]
    if journeys != required_journeys or int(browser.get("journey_count") or 0) != 2:
        raise ProductionCertificationError("browser_coverage_incomplete", "browser certification journeys are incomplete")
    browser_identity = browser.get("identity")
    if not isinstance(browser_identity, Mapping) or _identity_tuple(browser_identity) != _identity_tuple(model_identity):
        raise ProductionCertificationError("browser_model_identity_mismatch", "browser and real-model identities differ")
    if not str(browser.get("browser_version") or "").strip():
        raise ProductionCertificationError("browser_identity_invalid", "browser version is missing")
    if not re.fullmatch(r"[0-9a-f]{16}", str(browser.get("browser_executable_sha256_16") or "")):
        raise ProductionCertificationError("browser_identity_invalid", "browser executable fingerprint is missing")
    runtime = browser.get("runtime_authority")
    if not isinstance(runtime, Mapping) or runtime.get("contract") != "protected-browser-runtime-authority@1":
        raise ProductionCertificationError(
            "browser_runtime_authority_missing",
            "browser protected runtime authority evidence is missing",
        )
    expected_runtime = {
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
    }
    mismatches = {
        key: {"expected": expected, "actual": runtime.get(key)}
        for key, expected in expected_runtime.items()
        if runtime.get(key) != expected
    }
    required_verifiers = {
        "CAPABILITY_SEMANTIC_VERIFIER_MODE": "model",
        "GOAL_ALIGNMENT_VERIFIER_MODE": "model",
        "ANSWER_RELEASE_ALIGNMENT_VERIFIER_MODE": "model",
    }
    runtime_verifiers = runtime.get("verifier_modes")
    if not isinstance(runtime_verifiers, Mapping) or any(
        runtime_verifiers.get(key) != value for key, value in required_verifiers.items()
    ):
        mismatches["verifier_modes"] = {
            "expected": required_verifiers,
            "actual": runtime_verifiers,
        }
    if mismatches:
        raise ProductionCertificationError(
            "browser_runtime_authority_invalid",
            f"browser protected runtime authority is invalid: {mismatches}",
        )
    browser_database_fingerprint = str(browser.get("database_instance_fingerprint_sha256_16") or "")
    if not re.fullmatch(r"[0-9a-f]{16}", browser_database_fingerprint):
        raise ProductionCertificationError(
            "browser_database_identity_invalid",
            "browser PostgreSQL instance identity is missing",
        )
    if browser.get("pgvector_extension") is not True:
        raise ProductionCertificationError(
            "browser_pgvector_authority_invalid",
            "browser protected runtime did not attest pgvector",
        )
    browser_image_reference = str(browser.get("container_image_reference") or "").strip().casefold()
    browser_image_id = str(browser.get("container_image_id_sha256") or "").strip().casefold()
    if not _PGVECTOR_IMAGE_REFERENCE_RE.fullmatch(browser_image_reference):
        raise ProductionCertificationError(
            "browser_container_image_reference_invalid",
            "browser certification must use an immutable pgvector manifest digest",
        )
    if not _CONTAINER_IMAGE_ID_RE.fullmatch(browser_image_id):
        raise ProductionCertificationError(
            "browser_container_image_identity_invalid",
            "browser certification container image identity is missing",
        )
    if browser_image_reference != postgres_image_reference or browser_image_id != postgres_image_id:
        raise ProductionCertificationError(
            "production_container_image_mismatch",
            "PostgreSQL and browser certifications did not use the same immutable PostgreSQL image",
        )
    if int(browser.get("protected_runtime_journey_count") or 0) != 2:
        raise ProductionCertificationError(
            "browser_protected_coverage_incomplete",
            "both browser journeys must run under protected authority",
        )
    journey_evidence = list(browser.get("journey_evidence") or [])
    if len(journey_evidence) != 2:
        raise ProductionCertificationError(
            "browser_journey_evidence_incomplete",
            "browser protected journey evidence is incomplete",
        )
    for journey in journey_evidence:
        if not isinstance(journey, Mapping) or journey.get("contract") != "protected-browser-journey-runtime@1":
            raise ProductionCertificationError(
                "browser_journey_attestation_invalid",
                "browser journey protected runtime attestation is invalid",
            )
        if journey.get("status") != "PASS":
            raise ProductionCertificationError(
                "browser_journey_attestation_invalid",
                "browser journey protected runtime did not pass",
            )
        if str(journey.get("database_instance_fingerprint_sha256_16") or "") != browser_database_fingerprint:
            raise ProductionCertificationError(
                "browser_journey_database_mismatch",
                "browser journeys did not use the same PostgreSQL authority",
            )
        if journey.get("runtime_authority") != runtime:
            raise ProductionCertificationError(
                "browser_journey_runtime_mismatch",
                "browser journeys did not use the same protected runtime authority",
            )

    return {
        "contract": BUNDLE_CONTRACT,
        "status": "PASS",
        "session_id": session_id,
        "workspace_fingerprint_sha256": fingerprint,
        "toolchain_fingerprint_sha256": toolchain_fingerprint,
        "started_at": iso(started_at),
        "completed_at": iso(now),
        "components": list(COMPONENTS),
        "component_count": 3,
        "real_model_identity": {
            field: model_identity.get(field)
            for field in (
                "provider", "endpoint", "model", "credential_fingerprint_sha256_16",
                "official_endpoint", "https",
            )
        },
        "real_model_total_attested_calls": int(model_bundle.get("total_attested_model_calls") or 0),
        "postgres_database_instance_fingerprint_sha256_16": postgres["database_instance_fingerprint_sha256_16"],
        "postgres_container_image_reference": postgres_image_reference,
        "postgres_container_image_id_sha256": postgres_image_id,
        "postgres_restart_count": int(recovery.get("restart_count") or 0),
        "browser_version": str(browser.get("browser_version") or ""),
        "browser_journey_count": int(browser.get("journey_count") or 0),
        "browser_database_instance_fingerprint_sha256_16": browser_database_fingerprint,
        "browser_runtime_profile": str(runtime.get("runtime_profile") or ""),
        "evidence_scope": "single-live-production-certification-session",
    }


__all__ = [
    "BUNDLE_CONTRACT",
    "COMPONENTS",
    "COMPONENT_ENV",
    "ProductionCertificationError",
    "SESSION_ENV",
    "STARTED_ENV",
    "TOOLCHAIN_ENV",
    "WORKSPACE_ENV",
    "iso",
    "postgres_database_identity",
    "production_session_evidence",
    "safe_file_fingerprint",
    "utc_now",
    "validate_production_components",
    "workspace_fingerprint",
]
