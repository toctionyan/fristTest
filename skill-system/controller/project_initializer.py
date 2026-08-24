from __future__ import annotations

import fcntl
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from concrete_host_bootstrap import (
    CONCRETE_HOST_BOOTSTRAP_SCHEMA,
    DEFAULT_BOOTSTRAP_RELATIVE,
    ConcreteHostBootstrapError,
    seal_bootstrap,
)
from harness_starter import HarnessStarterError, initialize_starter


class ProjectInitializerError(RuntimeError):
    """Raised when one concrete Host project installation cannot be published safely."""


def _dedupe(values: Iterable[str], *, field: str) -> list[str]:
    result = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not result:
        raise ProjectInitializerError(f"{field} requires at least one command profile")
    return result


def _assert_safe_destination(project_root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(project_root)
    except ValueError as exc:
        raise ProjectInitializerError(f"Host installation path escapes project: {path}") from exc
    current = path
    while current != project_root:
        if current.is_symlink():
            raise ProjectInitializerError(f"Host installation path uses a symlink: {current}")
        current = current.parent


def _initialize_concrete_host_project_locked(
    *,
    project_workspace: Path,
    registry_workspace: Path,
    starter_id: str = "customer-agent",
    test_profiles: Iterable[str] = ("test",),
    quality_profiles: Iterable[str] = ("quality",),
    process_timeout_seconds: int = 900,
    github_repository: str | None = None,
    github_token_environment_variable: str = "GITHUB_TOKEN",
) -> dict[str, Any]:
    """Install, register, and seal one built-in Starter without touching product code."""

    project_source = Path(project_workspace)
    if project_source.is_symlink():
        raise ProjectInitializerError("project_workspace must not be a symlink")
    project_root = project_source.resolve()
    registry_root = Path(registry_workspace).resolve()
    if not project_root.is_dir() or project_root.is_symlink():
        raise ProjectInitializerError("project_workspace must be an existing safe directory")
    if not starter_id or any(char.isspace() for char in starter_id):
        raise ProjectInitializerError("starter_id must be a stable identifier")
    if not isinstance(process_timeout_seconds, int) or not 1 <= process_timeout_seconds <= 3600:
        raise ProjectInitializerError("process_timeout_seconds must be 1..3600")

    starter_relative = Path(".harness/starters") / starter_id
    registration_relative = Path(".harness/runtime/starter-registration.json")
    bootstrap_relative = DEFAULT_BOOTSTRAP_RELATIVE
    checkpointer_relative = Path(".harness/runtime/langgraph-checkpoints.sqlite3")
    starter_path = project_root / starter_relative
    registration_path = project_root / registration_relative
    bootstrap_path = project_root / bootstrap_relative
    targets = (starter_path, registration_path, bootstrap_path)
    for target in targets:
        _assert_safe_destination(project_root, target)
        if target.exists() or target.is_symlink():
            raise ProjectInitializerError(f"refusing to overwrite existing Host installation: {target}")

    initially_missing_parents = [
        path
        for path in (
            project_root / ".harness/host",
            project_root / ".harness/runtime",
            project_root / ".harness/starters",
            project_root / ".harness",
        )
        if not path.exists()
    ]
    try:
        verification = initialize_starter(
            starter_id,
            starter_path,
            registry_workspace=registry_root,
        )
        from starter_runtime import register_starter_runtime

        registration = register_starter_runtime(
            project_workspace=project_root,
            starter_directory=starter_path,
            output=registration_path,
            registry_workspace=registry_root,
        )
        project_commands = set(verification.project.commands)
        profiles = {
            "test.run": _dedupe(test_profiles, field="test_profiles"),
            "quality.evaluate": _dedupe(quality_profiles, field="quality_profiles"),
        }
        missing = sorted(
            {
                profile
                for values in profiles.values()
                for profile in values
                if profile not in project_commands
            }
        )
        if missing:
            raise ProjectInitializerError(
                f"Starter project has no declared command profiles: {missing}"
            )
        github = None
        if github_repository is not None:
            github = {
                "repository_full_name": github_repository,
                "token_environment_variable": github_token_environment_variable,
                "code_review_provider_id": "github.code_review",
                "ci_provider_id": "github.actions",
                "api_base": "https://api.github.com",
            }
        bootstrap = seal_bootstrap(
            {
                "schema": CONCRETE_HOST_BOOTSTRAP_SCHEMA,
                "starter": {
                    "starter_id": verification.starter.starter_id,
                    "package_sha256": verification.package_sha256,
                },
                "registration": {
                    "path": registration_relative.as_posix(),
                    "sha256": registration["registration_sha256"],
                },
                "checkpointer": {
                    "type": "sqlite",
                    "path": checkpointer_relative.as_posix(),
                },
                "providers": {
                    "execution_profiles": profiles,
                    "process_timeout_seconds": process_timeout_seconds,
                    "github": github,
                },
                "runtime": {
                    "session_root": ".harness/runtime/host-sessions",
                    "taskrun_root": ".harness/taskruns",
                    "workspace_fingerprint": None,
                },
                "policy": {
                    "configuration_grants_write_authority": False,
                    "configuration_completes_taskrun": False,
                    "automatic_merge": False,
                    "completion_authority": "TaskRun",
                    "authority_effect": False,
                },
            }
        )
        bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(bootstrap_path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(bootstrap, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        for path in (bootstrap_path, registration_path):
            if path.is_file() or path.is_symlink():
                path.unlink()
        if starter_path.is_dir() and not starter_path.is_symlink():
            shutil.rmtree(starter_path)
        for parent in initially_missing_parents:
            try:
                parent.rmdir()
            except OSError:
                pass
        if isinstance(exc, (ProjectInitializerError, ConcreteHostBootstrapError)):
            raise
        if isinstance(exc, HarnessStarterError):
            raise ProjectInitializerError(str(exc)) from exc
        raise ProjectInitializerError(f"Host project initialization failed: {exc}") from exc

    return {
        "schema": "concrete-host-project-initialization@1",
        "status": "PASS",
        "project_workspace": str(project_root),
        "starter": starter_relative.as_posix(),
        "registration": registration_relative.as_posix(),
        "bootstrap": bootstrap_relative.as_posix(),
        "bootstrap_sha256": bootstrap["bootstrap_sha256"],
        "factory": "concrete_host_bootstrap:build_orchestrator",
        "environment": {
            "HARNESS_HOST_FACTORY": "concrete_host_bootstrap:build_orchestrator",
            "HARNESS_HOST_BOOTSTRAP": str(bootstrap_path),
            "github_token_variable": (
                github_token_environment_variable if github_repository is not None else None
            ),
        },
        "policy": dict(bootstrap["policy"]),
    }


def initialize_concrete_host_project(
    *,
    project_workspace: Path,
    registry_workspace: Path,
    starter_id: str = "customer-agent",
    test_profiles: Iterable[str] = ("test",),
    quality_profiles: Iterable[str] = ("quality",),
    process_timeout_seconds: int = 900,
    github_repository: str | None = None,
    github_token_environment_variable: str = "GITHUB_TOKEN",
) -> dict[str, Any]:
    """Serialize publication so concurrent initializers cannot delete each other."""

    project_source = Path(project_workspace)
    if project_source.is_symlink():
        raise ProjectInitializerError("project_workspace must not be a symlink")
    project_root = project_source.resolve()
    if not project_root.is_dir():
        raise ProjectInitializerError("project_workspace must be an existing safe directory")
    harness_root = project_root / ".harness"
    _assert_safe_destination(project_root, harness_root)
    harness_root.mkdir(mode=0o700, exist_ok=True)
    lock_path = harness_root / "host-init.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with os.fdopen(descriptor, "r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return _initialize_concrete_host_project_locked(
                project_workspace=project_root,
                registry_workspace=registry_workspace,
                starter_id=starter_id,
                test_profiles=test_profiles,
                quality_profiles=quality_profiles,
                process_timeout_seconds=process_timeout_seconds,
                github_repository=github_repository,
                github_token_environment_variable=github_token_environment_variable,
            )
    except Exception:
        # Keep a durable empty lock file when .harness contains installation state.
        if harness_root.is_dir() and not any(
            path for path in harness_root.iterdir() if path != lock_path
        ):
            lock_path.unlink(missing_ok=True)
            harness_root.rmdir()
        raise


__all__ = ["ProjectInitializerError", "initialize_concrete_host_project"]
