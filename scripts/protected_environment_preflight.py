#!/usr/bin/env python3
"""Validate protected release environment inputs before expensive dependency install.

The preflight is deliberately standard-library-only. It runs after GitHub's
pinned setup-python/setup-node actions but before uv sync, npm ci, Playwright,
or model calls. It never emits credential values.
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
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

CONTRACT = "protected-environment-preflight@1"
_LOCK_CONTRACT = "release-toolchain-lock@1"
_ALLOWED_EMBEDDING_PROVIDERS = frozenset({"openai", "openai_compatible"})
_TEST_MARKERS = (
    "changeme",
    "deterministic",
    "dummy",
    "example",
    "fake",
    "mock",
    "not-a-real",
    "not_a_real",
    "placeholder",
    "stub",
    "test-key",
    "test_key",
    "test-model",
    "test_model",
)
_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,160}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


class ProtectedEnvironmentPreflightError(RuntimeError):
    def __init__(self, code: str, detail: str, *, environment_blocked: bool = False) -> None:
        super().__init__(detail)
        self.code = str(code)
        self.detail = str(detail)
        self.environment_blocked = bool(environment_blocked)


def _raise(code: str, detail: str, *, environment_blocked: bool = False) -> None:
    raise ProtectedEnvironmentPreflightError(
        code,
        detail,
        environment_blocked=environment_blocked,
    )


def _required(source: Mapping[str, str], name: str, *, secret: bool = False) -> str:
    value = str(source.get(name) or "").strip()
    if not value:
        _raise(
            f"{name.lower()}_missing",
            f"{name} is required for protected production certification",
            environment_blocked=True,
        )
    if secret and len(value.encode("utf-8")) < 20:
        _raise(
            f"{name.lower()}_too_short",
            f"{name} is too short for protected production certification",
        )
    return value


def _contains_test_marker(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in _TEST_MARKERS)


def _validate_secret(source: Mapping[str, str], name: str, *, minimum_bytes: int) -> str:
    value = _required(source, name)
    if len(value.encode("utf-8")) < minimum_bytes:
        _raise(
            f"{name.lower()}_too_short",
            f"{name} must contain at least {minimum_bytes} bytes",
        )
    if _contains_test_marker(value):
        _raise(
            f"{name.lower()}_placeholder_forbidden",
            f"{name} contains a test or placeholder marker",
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_text(source: Mapping[str, str], name: str) -> str:
    value = _required(source, name)
    if not _SAFE_TEXT_RE.fullmatch(value):
        _raise(
            f"{name.lower()}_invalid",
            f"{name} must be one printable value no longer than 160 characters",
        )
    if _contains_test_marker(value):
        _raise(
            f"{name.lower()}_placeholder_forbidden",
            f"{name} contains a test or placeholder marker",
        )
    return value


def _embedding_endpoint(raw: str) -> str:
    parsed = urlsplit(raw.strip())
    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password:
        _raise("protected_embedding_endpoint_invalid", "embedding endpoint must be credential-free HTTPS")
    if parsed.query or parsed.fragment or parsed.port not in {None, 443}:
        _raise("protected_embedding_endpoint_invalid", "embedding endpoint must use the default HTTPS origin without query or fragment")
    host = str(parsed.hostname or "").rstrip(".").casefold()
    if not host:
        _raise("protected_embedding_endpoint_invalid", "embedding endpoint host is missing")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.startswith("127."):
        _raise("protected_embedding_endpoint_local", "protected embedding certification cannot use a local endpoint")
    return urlunsplit(("https", host, (parsed.path or "").rstrip("/"), "", ""))


def _command_output(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(command),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _raise(
            "protected_environment_command_unavailable",
            f"required command is unavailable: {command[0]}",
            environment_blocked=True,
        )
    if result.returncode != 0:
        _raise(
            "protected_environment_command_failed",
            f"required command failed: {command[0]}",
            environment_blocked=True,
        )
    return result.stdout.strip()


def _load_lock(workspace_root: Path) -> dict[str, Any]:
    path = workspace_root / "deployment" / "ci" / "release-toolchain-lock.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _raise("protected_environment_lock_invalid", f"release toolchain lock is invalid: {exc}")
    if not isinstance(payload, dict) or payload.get("contract") != _LOCK_CONTRACT:
        _raise("protected_environment_lock_invalid", "release toolchain lock contract is invalid")
    return payload


def validate_protected_environment(
    *,
    workspace_root: Path,
    env: Mapping[str, str] | None = None,
    command_lookup=shutil.which,
) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    if str(source.get("GITHUB_ACTIONS") or "").strip().casefold() != "true" or str(
        source.get("CI") or ""
    ).strip().casefold() != "true":
        _raise(
            "protected_environment_ci_context_missing",
            "protected environment preflight must run inside GitHub Actions CI",
            environment_blocked=True,
        )
    if str(source.get("GITHUB_REF_PROTECTED") or "").strip().casefold() != "true":
        _raise("protected_environment_ref_unprotected", "production certification ref is not protected")
    if str(source.get("GITHUB_REF") or "").strip() != "refs/heads/main":
        _raise("protected_environment_ref_mismatch", "production certification must run on refs/heads/main")
    sha = str(source.get("GITHUB_SHA") or "").strip()
    run_id = str(source.get("GITHUB_RUN_ID") or "").strip()
    run_attempt = str(source.get("GITHUB_RUN_ATTEMPT") or "").strip()
    if not _SHA_RE.fullmatch(sha):
        _raise("protected_environment_commit_invalid", "GITHUB_SHA must be a hexadecimal commit identity")
    if not run_id.isdigit() or not run_attempt.isdigit() or int(run_attempt) < 1:
        _raise("protected_environment_run_identity_invalid", "GitHub run ID and attempt must be positive integers")

    workspace = Path(workspace_root).resolve()
    lock = _load_lock(workspace)
    expected_python = str(lock.get("python_version") or "")
    actual_python = ".".join(str(value) for value in sys.version_info[:3])
    if actual_python != expected_python:
        _raise(
            "protected_environment_python_version_mismatch",
            f"protected preflight requires Python {expected_python}",
            environment_blocked=True,
        )

    required_commands = ("node", "npm", "docker", "git")
    missing_commands = [name for name in required_commands if command_lookup(name) is None]
    if missing_commands:
        _raise(
            "protected_environment_commands_missing",
            "required protected runner commands are missing: " + ", ".join(missing_commands),
            environment_blocked=True,
        )
    node_version = _command_output(("node", "--version")).lstrip("v")
    npm_version = _command_output(("npm", "--version"))
    if node_version != str(lock.get("node_version") or ""):
        _raise(
            "protected_environment_node_version_mismatch",
            f"protected preflight requires Node {lock.get('node_version')}",
            environment_blocked=True,
        )
    if npm_version != str(lock.get("npm_version") or ""):
        _raise(
            "protected_environment_npm_version_mismatch",
            f"protected preflight requires npm {lock.get('npm_version')}",
            environment_blocked=True,
        )

    _safe_text(source, "REAL_MODEL_CERTIFICATION_PROVIDER")
    _safe_text(source, "OPENAI_MODEL")
    identity_path = workspace / "services" / "agent-service" / "src" / "agent_core" / "model_calls" / "real_model_identity.py"
    try:
        identity_namespace = runpy.run_path(str(identity_path))
    except OSError as exc:
        _raise(
            "protected_model_identity_authority_missing",
            f"real-model identity authority is unavailable: {exc}",
        )
    identity_error = identity_namespace["RealModelCertificationError"]
    resolve_identity = identity_namespace["resolve_real_model_identity"]
    try:
        chat_identity = resolve_identity(source)
    except identity_error as exc:
        _raise(
            str(getattr(exc, "code", "protected_model_identity_invalid")),
            str(getattr(exc, "detail", str(exc))),
            environment_blocked=bool(getattr(exc, "environment_blocked", False)),
        )
    provider = str(chat_identity["provider"])
    chat_model = str(chat_identity["model"])
    chat_endpoint = str(chat_identity["endpoint"])
    chat_fingerprint = str(chat_identity["credential_fingerprint_sha256_16"])

    embedding_provider = _safe_text(source, "EMBEDDING_PROVIDER").casefold()
    if embedding_provider not in _ALLOWED_EMBEDDING_PROVIDERS:
        _raise("protected_embedding_provider_invalid", "embedding provider must be OpenAI-compatible")
    embedding_model = _safe_text(source, "EMBEDDING_MODEL")
    embedding_endpoint = _embedding_endpoint(_required(source, "EMBEDDING_API_BASE"))
    embedding_fingerprint = _validate_secret(source, "EMBEDDING_API_KEY", minimum_bytes=20)
    raw_dimension = _required(source, "EMBEDDING_DIM")
    if not raw_dimension.isdigit() or not 1 <= int(raw_dimension) <= 65535:
        _raise("protected_embedding_dimension_invalid", "embedding dimension must be between 1 and 65535")
    signing_fingerprint = _validate_secret(source, "QUALITY_EVIDENCE_SIGNING_KEY", minimum_bytes=32)

    return {
        "contract": CONTRACT,
        "status": "PASS",
        "repository": str(source.get("GITHUB_REPOSITORY") or ""),
        "git_ref": "refs/heads/main",
        "commit_sha": sha.casefold(),
        "run_id": run_id,
        "run_attempt": run_attempt,
        "provider": provider,
        "chat_endpoint": chat_endpoint,
        "chat_model": chat_model,
        "chat_credential_fingerprint_sha256_16": chat_fingerprint,
        "embedding_provider": embedding_provider,
        "embedding_endpoint": embedding_endpoint,
        "embedding_model": embedding_model,
        "embedding_dimension": int(raw_dimension),
        "embedding_credential_fingerprint_sha256_16": embedding_fingerprint,
        "evidence_signing_key_fingerprint_sha256_16": signing_fingerprint,
        "python_version": actual_python,
        "node_version": node_version,
        "npm_version": npm_version,
        "required_commands_present": list(required_commands),
        "credential_values_emitted": False,
    }


def _failure(exc: ProtectedEnvironmentPreflightError) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
        "reason": exc.code,
        "error": exc.detail,
        "credential_values_emitted": False,
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        payload = validate_protected_environment(workspace_root=Path(args.workspace_root))
    except ProtectedEnvironmentPreflightError as exc:
        payload = _failure(exc)
    if args.output:
        _write_json_atomic(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if payload["status"] == "PASS":
        return 0
    if payload["status"] == "BLOCKED_BY_ENVIRONMENT":
        return 78
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
