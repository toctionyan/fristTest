#!/usr/bin/env python3
"""Run one immutable production certification and close one protected artifact.

The release controller is deliberately independent from the Agent runtime import
surface. It executes the live production-certification Bundle through the
Release Quality Loop, validates the exact CI summary contract, then accepts only
content-addressed artifacts produced from that same signed evidence set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import zipfile
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "services" / "agent-service"
SCRIPTS_ROOT = ROOT / "scripts"
for path in (SCRIPTS_ROOT,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Importing agent_core.model_calls.real_model_identity normally executes the
# model_calls package __init__, which loads LangChain/LangGraph. Production
# release preflight must remain independently loadable so it can diagnose a
# broken or incomplete Agent environment instead of crashing before reporting.
_IDENTITY_NAMESPACE = runpy.run_path(
    str(AGENT_ROOT / "src" / "agent_core" / "model_calls" / "real_model_identity.py")
)
RealModelCertificationError = _IDENTITY_NAMESPACE["RealModelCertificationError"]
resolve_real_model_identity = _IDENTITY_NAMESPACE["resolve_real_model_identity"]

from release_toolchain_contract import (  # noqa: E402
    EVIDENCE_ENV as TOOLCHAIN_EVIDENCE_ENV,
    FINGERPRINT_ENV as TOOLCHAIN_FINGERPRINT_ENV,
    ReleaseToolchainError,
    validate_runtime_evidence,
)
from release_run_identity import FINGERPRINT_ENV as RUN_IDENTITY_FINGERPRINT_ENV  # noqa: E402
from locked_python import locked_project_python  # noqa: E402

CONTRACT = "production-release-execution@2"
AUTHORITY_GATE = "production-certification-bundle"
EXPECTED_PRODUCTION_DIMENSION_CONTRACT = "production-certification-dimension@1"
EXPECTED_BUNDLE_CONTRACT = "production-certification-bundle@1"
PASS_LOOP_STATUSES = frozenset({"CONVERGED", "CI_VERIFIED"})
IDENTITY_FIELDS = (
    "provider",
    "endpoint",
    "model",
    "credential_fingerprint_sha256_16",
)


class ProductionReleaseExecutionError(RuntimeError):
    """Structured fail-closed release-control error."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        stage: str = "preflight",
        environment_blocked: bool = False,
    ) -> None:
        super().__init__(detail)
        self.code = str(code)
        self.detail = str(detail)
        self.stage = str(stage)
        self.environment_blocked = bool(environment_blocked)

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _raise(
    code: str,
    detail: str,
    *,
    stage: str = "preflight",
    environment_blocked: bool = False,
) -> None:
    raise ProductionReleaseExecutionError(
        code,
        detail,
        stage=stage,
        environment_blocked=environment_blocked,
    )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_external_path(path: Path, *, workspace: Path, label: str) -> None:
    resolved = path.resolve()
    if resolved == workspace or _is_within(resolved, workspace):
        _raise(
            "release_path_inside_workspace",
            f"{label} must be outside the workspace so release outputs cannot mutate the certified source fingerprint",
        )


def _require_empty_directory(path: Path, *, label: str) -> None:
    if path.exists() and not path.is_dir():
        _raise("release_directory_invalid", f"{label} must be a directory")
    if path.exists() and any(path.iterdir()):
        _raise(
            "release_directory_not_empty",
            f"{label} must be empty before production certification",
        )
    path.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_execution_environment(
    *,
    env: Mapping[str, str] | None = None,
    require_ci: bool = True,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    signing_key = str(source.get("QUALITY_EVIDENCE_SIGNING_KEY") or "")
    if len(signing_key.encode("utf-8")) < 32:
        _raise(
            "quality_evidence_signing_key_invalid",
            "QUALITY_EVIDENCE_SIGNING_KEY must contain at least 32 bytes",
            environment_blocked=True,
        )

    commit_sha = str(source.get("GITHUB_SHA") or "").strip()
    workflow_run_id = str(source.get("GITHUB_RUN_ID") or "").strip()
    workflow_run_attempt = str(source.get("GITHUB_RUN_ATTEMPT") or "1").strip()
    if require_ci:
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit_sha):
            _raise(
                "github_sha_invalid",
                "GITHUB_SHA is required and must be a 40-64 character hexadecimal commit identity",
                environment_blocked=True,
            )
        if not workflow_run_id.isdigit():
            _raise(
                "github_run_id_invalid",
                "GITHUB_RUN_ID is required and must be numeric",
                environment_blocked=True,
            )
        if not workflow_run_attempt.isdigit() or int(workflow_run_attempt) < 1:
            _raise(
                "github_run_attempt_invalid",
                "GITHUB_RUN_ATTEMPT must be a positive integer",
                environment_blocked=True,
            )

    toolchain_fingerprint = str(source.get(TOOLCHAIN_FINGERPRINT_ENV) or "").strip().casefold()
    toolchain_evidence = str(source.get(TOOLCHAIN_EVIDENCE_ENV) or "").strip()
    if require_ci or toolchain_fingerprint or toolchain_evidence:
        if workspace_root is None:
            _raise(
                "release_toolchain_workspace_missing",
                "workspace root is required to validate release toolchain evidence",
                environment_blocked=require_ci,
            )
        if not toolchain_fingerprint or not toolchain_evidence:
            _raise(
                "release_toolchain_evidence_missing",
                "protected release toolchain evidence and fingerprint are required",
                environment_blocked=True,
            )
        try:
            toolchain_payload = validate_runtime_evidence(
                Path(workspace_root),
                Path(toolchain_evidence),
                expected_fingerprint=toolchain_fingerprint,
            )
        except ReleaseToolchainError as exc:
            _raise(
                exc.code,
                str(exc),
                environment_blocked=exc.environment_blocked,
            )
        run_identity = toolchain_payload.get("ci_run_identity")
        run_identity = run_identity if isinstance(run_identity, Mapping) else {}
        recorded_run_fingerprint = str(run_identity.get("run_identity_fingerprint_sha256") or "").strip().casefold()
        injected_run_fingerprint = str(source.get(RUN_IDENTITY_FINGERPRINT_ENV) or "").strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", injected_run_fingerprint):
            _raise(
                "release_run_identity_fingerprint_missing",
                "protected release run identity fingerprint is required",
                environment_blocked=True,
            )
        if recorded_run_fingerprint != injected_run_fingerprint:
            _raise(
                "release_run_identity_fingerprint_mismatch",
                "injected release run identity does not match toolchain provenance",
            )
        expected_run_fields = {
            "commit_sha": commit_sha.casefold(),
            "run_id": workflow_run_id,
            "run_attempt": workflow_run_attempt,
            "workflow_ref": str(source.get("GITHUB_WORKFLOW_REF") or ""),
            "repository": str(source.get("GITHUB_REPOSITORY") or ""),
            "git_ref": str(source.get("GITHUB_REF") or ""),
        }
        mismatched_run_fields = [
            field for field, expected in expected_run_fields.items()
            if str(run_identity.get(field) or "").casefold() != str(expected or "").casefold()
        ]
        if mismatched_run_fields:
            _raise(
                "release_run_identity_environment_mismatch",
                "current GitHub run does not match captured toolchain identity: " + ", ".join(mismatched_run_fields),
            )

    missing = [name for name in ("docker", "node", "npm") if shutil.which(name) is None]
    if missing:
        _raise(
            "production_commands_unavailable",
            "production certification commands are unavailable: " + ", ".join(missing),
            environment_blocked=True,
        )

    try:
        identity = resolve_real_model_identity(source)
    except RealModelCertificationError as exc:
        _raise(
            str(getattr(exc, "code", "real_model_identity_invalid")),
            str(getattr(exc, "detail", str(exc))),
            environment_blocked=bool(getattr(exc, "environment_blocked", False)),
        )

    embedding_provider = str(source.get("EMBEDDING_PROVIDER") or "").strip().lower()
    if embedding_provider not in {"openai", "openai_compatible", "http", "local_http"}:
        _raise(
            "production_embedding_provider_invalid",
            "EMBEDDING_PROVIDER must be openai-compatible or http for protected pgvector browser certification",
            environment_blocked=True,
        )
    embedding_model = str(source.get("EMBEDDING_MODEL") or "").strip()
    if not embedding_model:
        _raise(
            "production_embedding_model_missing",
            "EMBEDDING_MODEL is required for protected pgvector browser certification",
            environment_blocked=True,
        )
    try:
        embedding_dimension = int(str(source.get("EMBEDDING_DIM") or "").strip())
    except ValueError:
        embedding_dimension = 0
    if embedding_dimension <= 0 or embedding_dimension > 65535:
        _raise(
            "production_embedding_dimension_invalid",
            "EMBEDDING_DIM must be a positive vector dimension no greater than 65535",
            environment_blocked=True,
        )
    embedding_endpoint = ""
    embedding_credential_fingerprint = ""
    if embedding_provider in {"openai", "openai_compatible"}:
        embedding_key = str(source.get("EMBEDDING_API_KEY") or "").strip()
        if len(embedding_key) < 20:
            _raise(
                "production_embedding_credentials_missing",
                "PRODUCTION_EMBEDDING_API_KEY must provide a non-empty protected embedding credential",
                environment_blocked=True,
            )
        embedding_endpoint = str(source.get("EMBEDDING_API_BASE") or "").strip()
        parsed_embedding = urlsplit(embedding_endpoint)
        if parsed_embedding.scheme.lower() != "https" or not parsed_embedding.hostname:
            _raise(
                "production_embedding_endpoint_invalid",
                "EMBEDDING_API_BASE must be an explicit HTTPS endpoint",
                environment_blocked=True,
            )
        host = parsed_embedding.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost") or host.startswith("127."):
            _raise(
                "production_embedding_endpoint_local",
                "protected embedding certification cannot use a local endpoint",
            )
        embedding_credential_fingerprint = hashlib.sha256(
            embedding_key.encode("utf-8")
        ).hexdigest()[:16]
    else:
        embedding_endpoint = str(source.get("EMBEDDING_BASE_URL") or "").strip()
        parsed_embedding = urlsplit(embedding_endpoint)
        if parsed_embedding.scheme.lower() != "https" or not parsed_embedding.hostname:
            _raise(
                "production_embedding_endpoint_invalid",
                "EMBEDDING_BASE_URL must be an explicit HTTPS endpoint",
                environment_blocked=True,
            )

    return {
        "provider": identity["provider"],
        "endpoint": identity["endpoint"],
        "model": identity["model"],
        "credential_fingerprint_sha256_16": identity[
            "credential_fingerprint_sha256_16"
        ],
        "embedding_provider": embedding_provider,
        "embedding_endpoint": embedding_endpoint,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "embedding_credential_fingerprint_sha256_16": embedding_credential_fingerprint,
        "commit_sha": commit_sha or "local-uncommitted",
        "workflow_run_id": workflow_run_id or "local",
        "workflow_run_attempt": workflow_run_attempt,
        "workflow_ref": str(source.get("GITHUB_WORKFLOW_REF") or ""),
        "repository": str(source.get("GITHUB_REPOSITORY") or ""),
        "git_ref": str(source.get("GITHUB_REF") or ""),
        "toolchain_fingerprint_sha256": toolchain_fingerprint,
        "run_identity_fingerprint_sha256": str(source.get(RUN_IDENTITY_FINGERPRINT_ENV) or "").strip().casefold(),
    }


def _revalidate_release_toolchain(
    *,
    workspace_root: Path,
    env: Mapping[str, str],
    expected_fingerprint: str,
    stage: str,
) -> None:
    evidence_path = str(env.get(TOOLCHAIN_EVIDENCE_ENV) or "").strip()
    if not evidence_path or not expected_fingerprint:
        _raise(
            "release_toolchain_evidence_missing",
            "protected release toolchain evidence and fingerprint are required",
            stage=stage,
            environment_blocked=True,
        )
    try:
        validate_runtime_evidence(
            workspace_root,
            Path(evidence_path),
            expected_fingerprint=expected_fingerprint,
        )
    except ReleaseToolchainError as exc:
        _raise(
            exc.code,
            str(exc),
            stage=stage,
            environment_blocked=exc.environment_blocked,
        )


def build_release_plan(
    *,
    workspace_root: Path,
    target_path: Path,
    evidence_dir: Path,
    output_dir: Path,
    artifact_name: str,
    python_executable: Path | None = None,
    result_path: Path | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    target = Path(target_path).resolve()
    evidence = Path(evidence_dir).resolve()
    output = Path(output_dir).resolve()
    python = Path(
        python_executable
        or workspace / "services" / "agent-service" / ".venv" / "bin" / "python"
    ).resolve()
    quality = [
        str(python),
        "-B",
        str(workspace / "scripts" / "quality_loop.py"),
        "--workspace-root",
        str(workspace),
        "--mode",
        "release",
        "--target",
        str(target),
        "--evidence-dir",
        str(evidence),
    ]
    build = [
        str(python),
        "-B",
        str(workspace / "scripts" / "build_clean_release.py"),
        "--workspace-root",
        str(workspace),
        "--output-dir",
        str(output),
        "--evidence-dir",
        str(evidence),
        "--frontend-mode",
        "npm-ci",
        "--certification-level",
        "protected-release",
        "--artifact-name",
        artifact_name,
    ]
    return {
        "contract": CONTRACT,
        "authority_gate": AUTHORITY_GATE,
        "quality_command": quality,
        "artifact_command": build,
        "evidence_dir": str(evidence),
        "output_dir": str(output),
        "result_path": str(Path(result_path).resolve()) if result_path else None,
        "artifact_name": artifact_name,
    }


def _require_identity_match(
    *,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    label: str,
) -> None:
    mismatched = [
        field
        for field in IDENTITY_FIELDS
        if str(expected.get(field) or "") != str(actual.get(field) or "")
    ]
    if mismatched:
        _raise(
            "release_identity_mismatch",
            f"{label} does not match protected preflight identity: {', '.join(mismatched)}",
            stage="quality_summary",
        )


def validate_release_summary(
    summary: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dimensions = summary.get("quality_dimensions")
    dimensions = dimensions if isinstance(dimensions, Mapping) else {}
    production = dimensions.get("production_certification")
    production = production if isinstance(production, Mapping) else {}
    real_model = dimensions.get("real_model_certification")
    real_model = real_model if isinstance(real_model, Mapping) else {}

    valid = (
        str(summary.get("mode")) == "release"
        and str(summary.get("decision")) == "PASS"
        and str(summary.get("loop_status")) in PASS_LOOP_STATUSES
        and summary.get("completion_eligible") is True
        and not summary.get("missing_prerequisites")
        and not summary.get("unverified_claim_ids")
        and str(production.get("status")) == "PASS"
        and str(production.get("contract"))
        == EXPECTED_PRODUCTION_DIMENSION_CONTRACT
        and str(real_model.get("status")) == "PASS"
        and str(real_model.get("bundle_contract")) == EXPECTED_BUNDLE_CONTRACT
        and str(production.get("session_id") or "")
        == str(real_model.get("session_id") or "")
        and str(production.get("workspace_fingerprint_sha256") or "")
        == str(real_model.get("workspace_fingerprint_sha256") or "")
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(production.get("toolchain_fingerprint_sha256") or "")))
        and str(production.get("toolchain_fingerprint_sha256") or "")
        == str(real_model.get("toolchain_fingerprint_sha256") or "")
        and bool(re.fullmatch(r"[0-9a-f]{64}", str(summary.get("ci_run_identity_fingerprint_sha256") or "")))
    )
    if not valid:
        _raise(
            "release_quality_summary_invalid",
            "release quality loop did not converge with one CI-verifiable PASS production-certification Bundle",
            stage="quality_summary",
        )

    production_identity = production.get("real_model_identity")
    production_identity = (
        production_identity if isinstance(production_identity, Mapping) else {}
    )
    real_model_identity = real_model.get("identity")
    real_model_identity = (
        real_model_identity if isinstance(real_model_identity, Mapping) else {}
    )
    if expected_identity is not None:
        _require_identity_match(
            expected=expected_identity,
            actual=production_identity,
            label="production certification identity",
        )
        _require_identity_match(
            expected=expected_identity,
            actual=real_model_identity,
            label="real-model certification identity",
        )
        if str(expected_identity.get("toolchain_fingerprint_sha256") or "") != str(production.get("toolchain_fingerprint_sha256") or ""):
            _raise(
                "release_toolchain_mismatch",
                "quality summary does not match protected preflight toolchain",
                stage="quality_summary",
            )
        if str(expected_identity.get("run_identity_fingerprint_sha256") or "") != str(summary.get("ci_run_identity_fingerprint_sha256") or ""):
            _raise(
                "release_run_identity_replay_detected",
                "quality summary belongs to another GitHub workflow run or attempt",
                stage="quality_summary",
            )

    return {
        "loop_status": str(summary.get("loop_status")),
        "workspace_snapshot_fingerprint": str(
            summary.get("workspace_snapshot_fingerprint") or ""
        ),
        "production_session_id": str(production.get("session_id") or ""),
        "production_workspace_fingerprint": str(
            production.get("workspace_fingerprint_sha256") or ""
        ),
        "production_toolchain_fingerprint": str(
            production.get("toolchain_fingerprint_sha256") or ""
        ),
        "ci_run_identity_fingerprint_sha256": str(
            summary.get("ci_run_identity_fingerprint_sha256") or ""
        ),
    }


def _read_quality_summary(evidence: Path) -> dict[str, Any]:
    summary_path = evidence / "run-summary.json"
    if not summary_path.is_file():
        _raise(
            "release_summary_missing",
            "Release Quality Loop emitted no run-summary.json",
            stage="quality_summary",
        )
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _raise(
            "release_summary_invalid_json",
            f"Release Quality Loop summary is not valid JSON: {exc}",
            stage="quality_summary",
        )
    if not isinstance(payload, dict):
        _raise(
            "release_summary_invalid_type",
            "Release Quality Loop summary must be a JSON object",
            stage="quality_summary",
        )
    return payload


def _run(command: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> int:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env),
        check=False,
    )
    return int(completed.returncode)


def _quality_failure_from_evidence(evidence: Path) -> ProductionReleaseExecutionError:
    try:
        summary = _read_quality_summary(evidence)
    except ProductionReleaseExecutionError:
        return ProductionReleaseExecutionError(
            "release_quality_loop_failed",
            "Release Quality Loop failed before emitting a valid summary; protected artifact was not built",
            stage="quality_loop",
        )
    decision = str(summary.get("decision") or "")
    loop_status = str(summary.get("loop_status") or "")
    blocked = decision == "BLOCKED_BY_ENVIRONMENT" or loop_status == "BLOCKED_BY_ENVIRONMENT"
    return ProductionReleaseExecutionError(
        "release_quality_loop_blocked" if blocked else "release_quality_loop_failed",
        "Release Quality Loop was blocked by the protected environment"
        if blocked
        else "Release Quality Loop failed; protected artifact was not built",
        stage="quality_loop",
        environment_blocked=blocked,
    )


def _validate_artifacts(output: Path, *, artifact_name: str) -> list[dict[str, Any]]:
    source_name = f"{artifact_name}.zip"
    sidecar_name = f"{source_name}.sha256"
    evidence_name = f"{artifact_name}-quality-evidence.zip"
    expected = {source_name, sidecar_name, evidence_name}
    entries = list(output.iterdir())
    unsafe = sorted(
        path.name for path in entries if path.is_symlink() or not path.is_file()
    )
    actual = {path.name for path in entries}
    if unsafe or actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        _raise(
            "protected_artifact_set_invalid",
            "protected artifact set is not exact"
            + (f"; unsafe={unsafe}" if unsafe else "")
            + (f"; missing={missing}" if missing else "")
            + (f"; unexpected={unexpected}" if unexpected else ""),
            stage="artifact_validation",
        )

    source = output / source_name
    sidecar = output / sidecar_name
    evidence = output / evidence_name
    if not zipfile.is_zipfile(source) or not zipfile.is_zipfile(evidence):
        _raise(
            "protected_artifact_archive_invalid",
            "protected source and quality evidence artifacts must both be valid ZIP archives",
            stage="artifact_validation",
        )
    try:
        row = sidecar.read_text(encoding="utf-8").strip()
        digest, filename = row.split("  ", 1)
    except (OSError, ValueError) as exc:
        _raise(
            "protected_artifact_sidecar_invalid",
            f"protected source SHA256 sidecar is invalid: {exc}",
            stage="artifact_validation",
        )
    actual_digest = _sha256_file(source)
    if filename != source_name or digest.lower() != actual_digest:
        _raise(
            "protected_artifact_sha256_mismatch",
            "protected source artifact SHA256 sidecar does not match the artifact",
            stage="artifact_validation",
        )

    return [
        {
            "kind": "protected-source",
            "filename": source_name,
            "sha256": actual_digest,
            "size_bytes": source.stat().st_size,
        },
        {
            "kind": "quality-evidence",
            "filename": evidence_name,
            "sha256": _sha256_file(evidence),
            "size_bytes": evidence.stat().st_size,
        },
        {
            "kind": "source-sha256-sidecar",
            "filename": sidecar_name,
            "sha256": _sha256_file(sidecar),
            "size_bytes": sidecar.stat().st_size,
        },
    ]


def _run_quality_in_owned_runtime(
    command: Sequence[str],
    *,
    workspace: Path,
    evidence_dir: Path,
    source_env: Mapping[str, str],
    command_runner,
) -> int:
    """Run cumulative Release gates with owned disposable integration dependencies."""

    try:
        from run_managed_quality_integration import ManagedPostgres
        from verify_full_lifecycle_canary import ProductRuntimeHarness
    except Exception as exc:
        _raise(
            "managed_quality_runtime_import_failed",
            f"owned quality runtime could not be loaded: {exc.__class__.__name__}: {exc}",
            stage="quality_runtime",
        )

    agent_python = locked_project_python(workspace, "agent", env=source_env)
    business_python = locked_project_python(workspace, "business", env=source_env)
    try:
        with ManagedPostgres() as postgres, ProductRuntimeHarness(
            persistence_url=postgres.url
        ) as product:
            # Product processes retain their deterministic provider environment.
            # The controller receives the protected provider credentials again so
            # the final production Bundle proves the official model in the same run.
            environment = product.env.copy()
            environment.update({str(key): str(value) for key, value in source_env.items()})
            environment.update({
                "AGENT_TEST_POSTGRES_URL": postgres.url,
                "BUSINESS_TEST_POSTGRES_URL": postgres.url,
                "BUSINESS_SERVICE_BASE_URL": product.business_url,
                "BUSINESS_SERVICE_TOKEN": product.business_service_token,
                "AGENT_TEST_URL": product.agent_url,
                "BUSINESS_TEST_URL": product.business_url,
                "PRODUCT_HTTP_SMOKE_EPHEMERAL_DATA": "true",
                "QUALITY_PYTHON_EXECUTABLE": str(agent_python),
                "QUALITY_AGENT_PYTHON": str(agent_python),
                "QUALITY_BUSINESS_PYTHON": str(business_python),
            })
            return command_runner(command, cwd=workspace, env=environment)
    except ProductionReleaseExecutionError:
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        text = str(exc)
        environment_blocked = any(marker in text.lower() for marker in (
            "docker command",
            "docker daemon",
            "failed to start managed pgvector",
            "did not become ready",
            "connection refused",
            "temporary failure in name resolution",
        ))
        _raise(
            "managed_quality_runtime_unavailable" if environment_blocked else "managed_quality_runtime_failed",
            text,
            stage="quality_runtime",
            environment_blocked=environment_blocked,
        )


def run_production_release(
    *,
    workspace_root: Path,
    target_path: Path,
    evidence_dir: Path,
    output_dir: Path,
    artifact_name: str,
    env: Mapping[str, str] | None = None,
    command_runner=_run,
    require_ci: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    target = Path(target_path).resolve()
    evidence = Path(evidence_dir).resolve()
    output = Path(output_dir).resolve()
    source_env = dict(os.environ if env is None else env)

    if not workspace.is_dir():
        _raise("workspace_missing", "workspace root does not exist")
    if not target.is_file() or not _is_within(target, workspace):
        _raise(
            "release_target_invalid",
            "release target must be an existing workspace file",
        )
    python = workspace / "services" / "agent-service" / ".venv" / "bin" / "python"
    if not python.is_file():
        _raise(
            "locked_agent_environment_missing",
            "locked Agent Python environment is missing",
            environment_blocked=True,
        )
    _require_external_path(evidence, workspace=workspace, label="evidence directory")
    _require_external_path(output, workspace=workspace, label="output directory")
    if evidence == output:
        _raise(
            "release_directories_collide",
            "evidence and output directories must be distinct",
        )
    _require_empty_directory(evidence, label="evidence directory")
    _require_empty_directory(output, label="output directory")

    identity = validate_execution_environment(env=source_env, require_ci=require_ci, workspace_root=workspace)
    plan = build_release_plan(
        workspace_root=workspace,
        target_path=target,
        evidence_dir=evidence,
        output_dir=output,
        artifact_name=artifact_name,
        python_executable=python,
    )

    if command_runner is _run and require_ci:
        quality_exit = _run_quality_in_owned_runtime(
            plan["quality_command"],
            workspace=workspace,
            evidence_dir=evidence,
            source_env=source_env,
            command_runner=command_runner,
        )
    else:
        quality_exit = command_runner(plan["quality_command"], cwd=workspace, env=source_env)
    if quality_exit != 0:
        raise _quality_failure_from_evidence(evidence)

    if require_ci:
        _revalidate_release_toolchain(
            workspace_root=workspace,
            env=source_env,
            expected_fingerprint=str(identity.get("toolchain_fingerprint_sha256") or ""),
            stage="post_quality_toolchain",
        )
    summary = _read_quality_summary(evidence)
    closure = validate_release_summary(summary, expected_identity=identity)

    if command_runner(plan["artifact_command"], cwd=workspace, env=source_env) != 0:
        _raise(
            "protected_artifact_build_failed",
            "protected clean-release build failed",
            stage="artifact_build",
        )
    if require_ci:
        _revalidate_release_toolchain(
            workspace_root=workspace,
            env=source_env,
            expected_fingerprint=str(identity.get("toolchain_fingerprint_sha256") or ""),
            stage="post_artifact_toolchain",
        )

    artifacts = _validate_artifacts(output, artifact_name=artifact_name)
    return {
        "contract": CONTRACT,
        "status": "PASS",
        "stage": "closed",
        "reason": "production_release_closed",
        "authority_gate": AUTHORITY_GATE,
        "identity": identity,
        **closure,
        "evidence_dir": str(evidence),
        "output_dir": str(output),
        "artifacts": artifacts,
    }


def _failure_result(exc: ProductionReleaseExecutionError) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
        "stage": exc.stage,
        "reason": exc.code,
        "error": exc.detail,
        "authority_gate": AUTHORITY_GATE,
        "artifacts": [],
    }


def execute_production_release(
    *,
    workspace_root: Path,
    target_path: Path,
    evidence_dir: Path,
    output_dir: Path,
    artifact_name: str,
    result_path: Path,
    env: Mapping[str, str] | None = None,
    command_runner=_run,
    require_ci: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    evidence = Path(evidence_dir).resolve()
    output = Path(output_dir).resolve()
    result = Path(result_path).resolve()
    try:
        _require_external_path(result, workspace=workspace, label="execution result path")
        if result == evidence or _is_within(result, evidence):
            _raise(
                "execution_result_inside_evidence",
                "execution result path must stay outside signed evidence",
            )
        if result == output or _is_within(result, output):
            _raise(
                "execution_result_inside_artifacts",
                "execution result path must stay outside protected artifacts",
            )
    except ProductionReleaseExecutionError as exc:
        # An unsafe ledger path must not be written. The caller still receives a
        # structured failure on stdout/return value.
        return _failure_result(exc)

    try:
        payload = run_production_release(
            workspace_root=workspace,
            target_path=target_path,
            evidence_dir=evidence,
            output_dir=output,
            artifact_name=artifact_name,
            env=env,
            command_runner=command_runner,
            require_ci=require_ci,
        )
    except ProductionReleaseExecutionError as exc:
        payload = _failure_result(exc)
    except Exception as exc:  # defensive control-plane ledger
        payload = {
            "contract": CONTRACT,
            "status": "FAIL",
            "stage": "controller_exception",
            "reason": "production_release_controller_exception",
            "error_type": exc.__class__.__name__,
            "error": "unexpected controller exception; inspect protected runner logs",
            "authority_gate": AUTHORITY_GATE,
            "artifacts": [],
        }
    _write_json_atomic(result, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=str(ROOT))
    parser.add_argument("--target", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-path")
    parser.add_argument(
        "--artifact-name",
        default="customer_agent_workspace_v20_17_production_closed",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    output = Path(args.output_dir).resolve()
    result_path = Path(args.result_path).resolve() if args.result_path else (
        output.parent / f"{args.artifact_name}-execution-result.json"
    )
    plan = build_release_plan(
        workspace_root=workspace,
        target_path=Path(args.target),
        evidence_dir=Path(args.evidence_dir),
        output_dir=output,
        artifact_name=args.artifact_name,
        result_path=result_path,
    )
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    result = execute_production_release(
        workspace_root=workspace,
        target_path=Path(args.target),
        evidence_dir=Path(args.evidence_dir),
        output_dir=output,
        artifact_name=args.artifact_name,
        result_path=result_path,
    )
    print(json.dumps(result, ensure_ascii=False))
    if result.get("status") == "PASS":
        return 0
    if result.get("status") == "BLOCKED_BY_ENVIRONMENT":
        return 78
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
