#!/usr/bin/env python3
"""Fail-closed release toolchain and GitHub Actions supply-chain contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from release_run_identity import (
    ReleaseRunIdentityError,
    capture_run_identity,
    validate_run_identity_payload,
)

CONTRACT = "release-toolchain-provenance@1"
LOCK_CONTRACT = "release-toolchain-lock@1"
FINGERPRINT_ENV = "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT"
EVIDENCE_ENV = "PRODUCTION_CERTIFICATION_TOOLCHAIN_EVIDENCE"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ACTION_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+([^\s@]+)@([^\s#]+)(?:\s+#\s*(.*))?\s*$")
_UV_VERSION_OUTPUT_RE = re.compile(
    r"^uv\s+(?P<version>[0-9]+(?:\.[0-9]+){2})(?:\s+\([A-Za-z0-9_.-]+\))?$"
)


class ReleaseToolchainError(RuntimeError):
    def __init__(self, code: str, message: str, *, environment_blocked: bool = False):
        super().__init__(message)
        self.code = str(code)
        self.environment_blocked = bool(environment_blocked)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseToolchainError(code, f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseToolchainError(code, f"JSON object required at {path}")
    return payload


def _lock(workspace: Path) -> dict[str, Any]:
    path = workspace / "deployment" / "ci" / "release-toolchain-lock.json"
    payload = _load_json(path, code="release_toolchain_lock_invalid")
    if payload.get("contract") != LOCK_CONTRACT:
        raise ReleaseToolchainError("release_toolchain_lock_contract_invalid", "release toolchain lock contract is invalid")
    return payload


def _validate_postgres_image_reference(value: Any) -> str:
    image = str(value or "").strip().casefold()
    if not re.fullmatch(r"pgvector/pgvector@sha256:[0-9a-f]{64}", image):
        raise ReleaseToolchainError(
            "release_postgres_image_unlocked",
            "protected pgvector image must be pinned by manifest digest",
        )
    return image


def _workflow_actions(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = _ACTION_RE.match(line)
        if match:
            rows.append((match.group(1), match.group(2), (match.group(3) or "").strip()))
    return rows


def validate_static_contract(workspace_root: Path) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    lock = _lock(workspace)
    workflow_path = workspace / ".github" / "workflows" / "release.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    action_rows = _workflow_actions(workflow)
    expected_actions = lock.get("github_actions")
    if not isinstance(expected_actions, Mapping) or not expected_actions:
        raise ReleaseToolchainError("release_action_lock_missing", "GitHub Action lock is missing")

    actual_by_name: dict[str, list[tuple[str, str]]] = {}
    for name, ref, comment in action_rows:
        actual_by_name.setdefault(name, []).append((ref, comment))
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            raise ReleaseToolchainError(
                "release_action_not_sha_pinned",
                f"release workflow action {name}@{ref} is not pinned to a full commit SHA",
            )
    for name, spec in expected_actions.items():
        if not isinstance(spec, Mapping):
            raise ReleaseToolchainError("release_action_lock_invalid", f"invalid action lock for {name}")
        sha = str(spec.get("sha") or "")
        version = str(spec.get("version") or "")
        matches = actual_by_name.get(str(name), [])
        if not matches or any(ref != sha for ref, _ in matches):
            raise ReleaseToolchainError("release_action_pin_mismatch", f"release workflow does not use locked {name}@{sha}")
        if any(version not in comment for _, comment in matches):
            raise ReleaseToolchainError("release_action_version_comment_mismatch", f"release workflow version comment is missing for {name}")
    unexpected = sorted(set(actual_by_name) - set(str(name) for name in expected_actions))
    if unexpected:
        raise ReleaseToolchainError("release_action_set_unlocked", f"release workflow contains unlocked actions: {unexpected}")

    runner = str(lock.get("runner") or "")
    runner_architecture = str(lock.get("runner_architecture") or "")
    python_version = str(lock.get("python_version") or "")
    node_version = str(lock.get("node_version") or "")
    if f"runs-on: {runner}" not in workflow:
        raise ReleaseToolchainError("release_runner_unlocked", "release workflow runner is not exact")
    if runner_architecture != "x86_64":
        raise ReleaseToolchainError("release_runner_architecture_invalid", "hashed uv bootstrap currently supports only x86_64")
    if f"python-version: '{python_version}'" not in workflow:
        raise ReleaseToolchainError("release_python_version_unlocked", "release workflow Python version is not exact")
    if f"node-version: '{node_version}'" not in workflow:
        raise ReleaseToolchainError("release_node_version_unlocked", "release workflow Node version is not exact")
    required_uv_fragment = "--require-hashes --only-binary=:all: -r deployment/ci/uv-requirements-linux-x86_64.txt"
    if required_uv_fragment not in workflow:
        raise ReleaseToolchainError("release_uv_bootstrap_unlocked", "release workflow does not install uv from the hashed bootstrap lock")
    if re.search(r"pip\s+install[^\n]*\buv(?:\s|$)", workflow) and required_uv_fragment not in workflow:
        raise ReleaseToolchainError("release_uv_bootstrap_unlocked", "release workflow contains an unpinned uv install")
    if "package-manager-cache: false" not in workflow:
        raise ReleaseToolchainError("release_implicit_package_cache_enabled", "setup-node automatic package-manager cache must be disabled")
    if "npm ci --ignore-scripts=false" not in workflow:
        raise ReleaseToolchainError("release_npm_install_unlocked", "release workflow must use npm ci against package-lock.json")
    run_lock = lock.get("release_run_identity")
    if not isinstance(run_lock, Mapping):
        raise ReleaseToolchainError("release_run_identity_lock_missing", "release run identity lock is missing")
    expected_ref = str(run_lock.get("ref") or "")
    expected_event = str(run_lock.get("event_name") or "")
    expected_workflow = str(run_lock.get("workflow_name") or "")
    expected_job = str(run_lock.get("job") or "")
    admission_lock = lock.get("release_admission")
    if not isinstance(admission_lock, Mapping):
        raise ReleaseToolchainError(
            "release_admission_lock_missing",
            "release workflow admission lock is missing",
        )
    admission_job = str(admission_lock.get("job") or "")
    protected_job = str(admission_lock.get("protected_job") or "")
    admission_contract = str(admission_lock.get("contract") or "")
    admission_artifact = str(admission_lock.get("sanitized_result_artifact") or "")
    admission_artifact_prefix = str(admission_lock.get("artifact_name_prefix") or "")
    if (
        admission_job != "release-admission"
        or protected_job != expected_job
        or admission_contract != "release-workflow-admission@1"
        or admission_artifact != "release-admission-result.json"
        or admission_artifact_prefix != "production-release-admission"
        or admission_lock.get("always_upload") is not True
    ):
        raise ReleaseToolchainError(
            "release_admission_lock_invalid",
            "release workflow admission lock does not match the protected release contract",
        )
    preflight_lock = lock.get("protected_environment_preflight")
    if not isinstance(preflight_lock, Mapping):
        raise ReleaseToolchainError(
            "protected_environment_preflight_lock_missing",
            "protected Environment preflight lock is missing",
        )
    if (
        str(preflight_lock.get("contract") or "") != "protected-environment-preflight@1"
        or str(preflight_lock.get("job") or "") != expected_job
        or preflight_lock.get("runs_before_dependency_install") is not True
        or str(preflight_lock.get("sanitized_failure_artifact") or "")
        != "protected-environment-preflight.json"
    ):
        raise ReleaseToolchainError(
            "protected_environment_preflight_lock_invalid",
            "protected Environment preflight lock does not match the release workflow contract",
        )
    required_fragments = (
        f"PRODUCTION_RELEASE_EXPECTED_EVENT: {expected_event}",
        f"PRODUCTION_RELEASE_EXPECTED_WORKFLOW: {expected_workflow}",
        f"PRODUCTION_RELEASE_EXPECTED_JOB: {expected_job}",
        f"PRODUCTION_RELEASE_EXPECTED_REF: {expected_ref}",
        f"  {admission_job}:",
        f"needs: {admission_job}",
        "scripts/release_admission_contract.py",
        "Fail closed on invalid release admission",
        "Upload sanitized release admission evidence",
        "release-admission-result.json",
        "production-release-admission-${{ github.run_id }}-${{ github.run_attempt }}",
        "Validate protected Environment configuration",
        "scripts/protected_environment_preflight.py",
        "protected-environment-preflight.json",
        "secrets.PRODUCTION_MODEL_API_KEY",
        "secrets.PRODUCTION_EMBEDDING_API_KEY",
        "secrets.QUALITY_EVIDENCE_SIGNING_KEY",
        "github.ref_protected == true",
        f"github.ref == '{expected_ref}'",
        "ref: ${{ github.sha }}",
        "fetch-depth: 1",
        "clean: true",
        "persist-credentials: false",
        "PRODUCTION_CERTIFICATION_RUN_IDENTITY_FINGERPRINT",
        "production-certification-evidence-${{ github.run_id }}-${{ github.run_attempt }}",
        "production-closed-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}",
    )
    missing_fragments = [fragment for fragment in required_fragments if fragment not in workflow]
    if missing_fragments:
        raise ReleaseToolchainError(
            "release_run_identity_workflow_unlocked",
            "release workflow is missing protected run identity controls: " + ", ".join(missing_fragments),
        )
    admission_section = workflow.split(f"  {admission_job}:", 1)[1].split(f"  {expected_job}:", 1)[0]
    if "secrets." in admission_section or "environment:" in admission_section:
        raise ReleaseToolchainError(
            "release_admission_secret_boundary_invalid",
            "release admission evidence job must remain secret-free and outside the protected Environment",
        )
    if (
        "if: always()" not in admission_section
        or "if-no-files-found: error" not in admission_section
        or f"${{{{ runner.temp }}}}/{admission_artifact}" not in admission_section
    ):
        raise ReleaseToolchainError(
            "release_admission_evidence_upload_invalid",
            "release admission must always upload exactly one sanitized result artifact",
        )
    try:
        preflight_index = workflow.index("- name: Validate protected Environment configuration")
        dependency_install_index = workflow.index("- name: Install locked Python and frontend environments")
    except ValueError as exc:
        raise ReleaseToolchainError(
            "protected_environment_preflight_workflow_missing",
            "protected Environment preflight or dependency install step is missing",
        ) from exc
    if preflight_index >= dependency_install_index:
        raise ReleaseToolchainError(
            "protected_environment_preflight_order_invalid",
            "protected Environment preflight must run before expensive dependency installation",
        )
    evidence_upload = workflow.split("- name: Upload signed production evidence", 1)
    evidence_section = evidence_upload[1].split("- name: Upload production closed artifacts", 1)[0] if len(evidence_upload) == 2 else ""
    if "include-hidden-files: true" not in evidence_section:
        raise ReleaseToolchainError(
            "release_hidden_evidence_upload_disabled",
            "signed release evidence must explicitly include the .quality target and claims files",
        )
    if "${{ runner.temp }}/protected-environment-preflight.json" not in evidence_section:
        raise ReleaseToolchainError(
            "protected_environment_preflight_artifact_missing",
            "sanitized protected Environment preflight result must be uploaded with release evidence",
        )
    frontend_package = _load_json(
        workspace / "services" / "agent-service" / "frontend" / "package.json",
        code="release_frontend_package_invalid",
    )
    expected_npm = str(lock.get("npm_version") or "")
    if frontend_package.get("packageManager") != f"npm@{expected_npm}":
        raise ReleaseToolchainError("release_npm_version_unlocked", "frontend packageManager does not match the release npm lock")
    uv_version = str(lock.get("uv_version") or "")
    uv_bootstrap = (workspace / "deployment" / "ci" / "uv-requirements-linux-x86_64.txt").read_text(encoding="utf-8")
    requirement_rows = [line.strip() for line in uv_bootstrap.splitlines() if line.strip() and not line.lstrip().startswith("#") and not line.lstrip().startswith("--hash=")]
    if len(requirement_rows) != 1 or not re.fullmatch(rf"uv=={re.escape(uv_version)}\s*\\", requirement_rows[0]):
        raise ReleaseToolchainError("release_uv_bootstrap_lock_invalid", "uv bootstrap lock must contain one exact uv requirement")
    if not re.search(r"--hash=sha256:[0-9a-f]{64}", uv_bootstrap):
        raise ReleaseToolchainError("release_uv_bootstrap_hash_missing", "uv bootstrap wheel SHA-256 is missing")
    postgres_image = _validate_postgres_image_reference(lock.get("postgres_image"))
    managed_runtime = (workspace / "scripts" / "run_managed_quality_integration.py").read_text(encoding="utf-8")
    if f'DEFAULT_POSTGRES_IMAGE = "{postgres_image}"' not in managed_runtime:
        raise ReleaseToolchainError("release_postgres_image_contract_mismatch", "managed PostgreSQL runtime does not use the locked image digest")

    locked_files: dict[str, str] = {}
    for relative in lock.get("locked_source_files") or []:
        path = workspace / str(relative)
        if not path.is_file():
            raise ReleaseToolchainError("release_locked_source_missing", f"locked source file is missing: {relative}")
        locked_files[str(relative)] = _sha256_file(path)
    return {
        "contract": "release-toolchain-static-contract@1",
        "status": "PASS",
        "workflow_sha256": _sha256_file(workflow_path),
        "action_pins": {str(name): str(spec["sha"]) for name, spec in expected_actions.items()},
        "locked_source_sha256": locked_files,
        "runner": runner,
        "runner_architecture": runner_architecture,
        "python_version": python_version,
        "node_version": node_version,
        "npm_version": str(lock.get("npm_version") or ""),
        "uv_version": str(lock.get("uv_version") or ""),
        "postgres_image": postgres_image,
        "postgres_image_release": str(lock.get("postgres_image_release") or ""),
        "release_run_identity": dict(run_lock),
        "release_admission": dict(admission_lock),
        "protected_environment_preflight": dict(preflight_lock),
    }


def _normalize_uv_version_output(value: str) -> str:
    raw = str(value or "").strip()
    match = _UV_VERSION_OUTPUT_RE.fullmatch(raw)
    if match is None:
        raise ReleaseToolchainError(
            "release_uv_version_output_invalid",
            f"unexpected uv --version output: {raw!r}",
            environment_blocked=True,
        )
    return str(match.group("version"))


def _run(command: Sequence[str], *, cwd: Path) -> str:
    try:
        completed = subprocess.run(list(command), cwd=cwd, text=True, capture_output=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseToolchainError("release_toolchain_command_unavailable", f"toolchain command failed: {command[0]}", environment_blocked=True) from exc
    if completed.returncode != 0:
        raise ReleaseToolchainError(
            "release_toolchain_command_failed",
            f"toolchain command returned {completed.returncode}: {command[0]}",
            environment_blocked=True,
        )
    return completed.stdout.strip()


def _resolved_executable(name: str) -> Path:
    found = shutil.which(name)
    if not found:
        raise ReleaseToolchainError("release_toolchain_command_missing", f"required command is missing: {name}", environment_blocked=True)
    # Preserve the launcher path rather than dereferencing symlinks. Some locked
    # toolchains (notably setup-node's npm launcher) depend on invocation through
    # that launcher identity, just as a Python virtualenv does.
    return Path(found).absolute()


def _tree_digest(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ReleaseToolchainError(
            "release_environment_tree_missing",
            f"installed environment tree is missing: {root}",
            environment_blocked=True,
        )
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    excluded_parts = {"__pycache__", ".pytest_cache", ".cache", ".vite"}
    excluded_suffixes = {".pyc", ".pyo"}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative_path = path.relative_to(root)
        if any(part in excluded_parts for part in relative_path.parts) or path.suffix in excluded_suffixes:
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            digest.update(b"L\0")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(os.readlink(path).encode("utf-8"))
            file_count += 1
            continue
        if not path.is_file():
            continue
        digest.update(b"F\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        size = path.stat().st_size
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        file_count += 1
        total_bytes += size
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "content_tree_sha256": digest.hexdigest(),
    }


def _python_environment_digest(python: Path, *, cwd: Path) -> dict[str, Any]:
    code = (
        "import hashlib,json,importlib.metadata as m;"
        "rows=sorted((d.metadata.get('Name','').lower(),d.version) for d in m.distributions());"
        "raw=json.dumps(rows,separators=(',',':')).encode();"
        "print(json.dumps({'count':len(rows),'sha256':hashlib.sha256(raw).hexdigest()}))"
    )
    payload = json.loads(_run([str(python), "-I", "-c", code], cwd=cwd))
    if not isinstance(payload, dict) or not _SHA256_RE.fullmatch(str(payload.get("sha256") or "")):
        raise ReleaseToolchainError("release_python_environment_invalid", f"invalid environment inventory from {python}")
    return {"distribution_count": int(payload.get("count") or 0), "distribution_set_sha256": str(payload["sha256"])}


def _npm_tree_payload(frontend: Path, npm: Path) -> dict[str, Any]:
    command = [str(npm), "ls", "--all", "--json"]
    try:
        completed = subprocess.run(
            command,
            cwd=frontend,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseToolchainError(
            "release_toolchain_command_unavailable",
            f"toolchain command failed: {command[0]}",
            environment_blocked=True,
        ) from exc

    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise ReleaseToolchainError(
            "release_npm_environment_invalid",
            "npm dependency inventory did not emit valid JSON",
            environment_blocked=True,
        ) from exc

    # `npm ls` intentionally returns non-zero for peer/optional diagnostics on
    # some npm/runtime combinations even though it still emits the complete
    # dependency tree. Treat only a structurally valid tree as evidence; a
    # command failure without that tree remains fail-closed.
    if not isinstance(payload, dict) or not any(
        key in payload for key in ("name", "version", "dependencies")
    ):
        raise ReleaseToolchainError(
            "release_npm_environment_invalid",
            "npm dependency inventory did not contain a dependency tree",
            environment_blocked=True,
        )
    dependencies = payload.get("dependencies")
    if dependencies is not None and not isinstance(dependencies, Mapping):
        raise ReleaseToolchainError(
            "release_npm_environment_invalid",
            "npm dependency inventory dependencies field is invalid",
            environment_blocked=True,
        )
    return payload


def _npm_tree_digest(frontend: Path, npm: Path) -> dict[str, Any]:
    payload = _npm_tree_payload(frontend, npm)
    return {"dependency_tree_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def capture_runtime_provenance(workspace_root: Path) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    static = validate_static_contract(workspace)
    lock = _lock(workspace)
    agent_python = workspace / "services" / "agent-service" / ".venv" / "bin" / "python"
    business_python = workspace / "services" / "business-service" / ".venv" / "bin" / "python"
    if not agent_python.is_file() or not business_python.is_file():
        raise ReleaseToolchainError("release_locked_python_environment_missing", "locked Agent and Business environments are required", environment_blocked=True)
    if Path(sys.executable).resolve() != agent_python.resolve():
        raise ReleaseToolchainError(
            "release_provenance_python_mismatch",
            "release provenance must be captured by the locked Agent Python",
            environment_blocked=True,
        )
    os_release: dict[str, str] = {}
    os_release_path = Path("/etc/os-release")
    if os_release_path.is_file():
        for line in os_release_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os_release[key] = value.strip().strip('"')
    actual_runner = {
        "system": platform.system().lower(),
        "architecture": platform.machine().lower(),
        "distribution": str(os_release.get("ID") or "").lower(),
        "version_id": str(os_release.get("VERSION_ID") or ""),
    }
    expected_arch = str(lock.get("runner_architecture") or "").lower()
    if actual_runner != {"system": "linux", "architecture": expected_arch, "distribution": "ubuntu", "version_id": "24.04"}:
        raise ReleaseToolchainError(
            "release_runner_identity_mismatch",
            f"locked runner mismatch: {actual_runner}",
            environment_blocked=True,
        )

    actual_python = _run([str(agent_python), "-c", "import platform; print(platform.python_version())"], cwd=workspace)
    node = _resolved_executable("node")
    npm = _resolved_executable("npm")
    uv = _resolved_executable("uv")
    docker = _resolved_executable("docker")
    actual_node = _run([str(node), "--version"], cwd=workspace).lstrip("v")
    actual_npm = _run([str(npm), "--version"], cwd=workspace)
    actual_uv = _normalize_uv_version_output(_run([str(uv), "--version"], cwd=workspace))
    docker_client_version = _run([str(docker), "version", "--format", "{{.Client.Version}}"], cwd=workspace)
    docker_server_version = _run([str(docker), "version", "--format", "{{.Server.Version}}"], cwd=workspace)
    expected = {
        "python": str(lock.get("python_version") or ""),
        "node": str(lock.get("node_version") or ""),
        "npm": str(lock.get("npm_version") or ""),
        "uv": str(lock.get("uv_version") or ""),
    }
    actual = {"python": actual_python, "node": actual_node, "npm": actual_npm, "uv": actual_uv}
    if actual != expected:
        raise ReleaseToolchainError("release_toolchain_version_mismatch", f"locked toolchain mismatch: expected={expected}, actual={actual}", environment_blocked=True)

    frontend = workspace / "services" / "agent-service" / "frontend"
    try:
        ci_run_identity = capture_run_identity(workspace)
    except ReleaseRunIdentityError as exc:
        raise ReleaseToolchainError(exc.code, str(exc), environment_blocked=exc.environment_blocked) from exc
    payload: dict[str, Any] = {
        "contract": CONTRACT,
        "status": "PASS",
        "runner": str(lock.get("runner") or ""),
        "runner_identity": actual_runner,
        "versions": actual,
        "executables": {
            "python_sha256": _sha256_file(agent_python.resolve()),
            "node_sha256": _sha256_file(node),
            "npm_sha256": _sha256_file(npm),
            "uv_sha256": _sha256_file(uv),
            "docker_sha256": _sha256_file(docker),
        },
        "docker": {
            "client_version": docker_client_version,
            "server_version": docker_server_version,
            "postgres_image": str(lock.get("postgres_image") or ""),
        },
        "static_contract": static,
        "ci_run_identity": ci_run_identity,
        "python_environments": {
            "agent": {
                **_python_environment_digest(agent_python, cwd=workspace),
                **_tree_digest(agent_python.parents[1]),
            },
            "business": {
                **_python_environment_digest(business_python, cwd=workspace),
                **_tree_digest(business_python.parents[1]),
            },
        },
        "frontend_environment": {
            **_npm_tree_digest(frontend, npm),
            **_tree_digest(frontend / "node_modules"),
        },
    }
    payload["toolchain_fingerprint_sha256"] = _canonical_sha256(payload)
    return payload


def validate_runtime_evidence(
    workspace_root: Path,
    evidence_path: Path,
    *,
    expected_fingerprint: str | None = None,
    validate_live_runtime: bool = True,
) -> dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    evidence = Path(evidence_path).resolve()
    payload = _load_json(evidence, code="release_toolchain_evidence_invalid")
    if payload.get("contract") != CONTRACT or payload.get("status") != "PASS":
        raise ReleaseToolchainError("release_toolchain_evidence_contract_invalid", "release toolchain evidence is not a PASS contract")
    fingerprint = str(payload.get("toolchain_fingerprint_sha256") or "").lower()
    unsigned = dict(payload)
    unsigned.pop("toolchain_fingerprint_sha256", None)
    if not _SHA256_RE.fullmatch(fingerprint) or _canonical_sha256(unsigned) != fingerprint:
        raise ReleaseToolchainError("release_toolchain_evidence_fingerprint_invalid", "release toolchain evidence fingerprint is invalid")
    if expected_fingerprint and fingerprint != str(expected_fingerprint).lower():
        raise ReleaseToolchainError("release_toolchain_evidence_mismatch", "release toolchain evidence does not match the injected fingerprint")
    ci_run_identity = payload.get("ci_run_identity")
    if not isinstance(ci_run_identity, Mapping):
        raise ReleaseToolchainError("release_run_identity_missing", "release toolchain evidence has no CI run identity")
    try:
        validate_run_identity_payload(ci_run_identity)
    except ReleaseRunIdentityError as exc:
        raise ReleaseToolchainError(exc.code, str(exc), environment_blocked=exc.environment_blocked) from exc
    current_static = validate_static_contract(workspace)
    recorded_static = payload.get("static_contract")
    if not isinstance(recorded_static, Mapping) or recorded_static != current_static:
        raise ReleaseToolchainError("release_toolchain_source_mutated", "release source/toolchain lock changed after provenance capture")
    if validate_live_runtime:
        current_runtime = capture_runtime_provenance(workspace)
        if str(current_runtime.get("toolchain_fingerprint_sha256") or "") != fingerprint:
            raise ReleaseToolchainError(
                "release_toolchain_runtime_mutated",
                "installed release toolchain changed after provenance capture",
            )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output")
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--validate-evidence")
    parser.add_argument("--expected-fingerprint")
    args = parser.parse_args()
    try:
        workspace = Path(args.workspace_root)
        if args.validate_evidence:
            result = validate_runtime_evidence(workspace, Path(args.validate_evidence), expected_fingerprint=args.expected_fingerprint)
        elif args.static_only:
            result = validate_static_contract(workspace)
        else:
            result = capture_runtime_provenance(workspace)
        if args.output:
            out = Path(args.output).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ReleaseToolchainError as exc:
        result = {
            "contract": CONTRACT,
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "reason": exc.code,
            "error": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
