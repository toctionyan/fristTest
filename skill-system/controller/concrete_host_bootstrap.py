from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Mapping


CONCRETE_HOST_BOOTSTRAP_SCHEMA = "concrete-host-bootstrap@1"
DEFAULT_BOOTSTRAP_ENVIRONMENT_VARIABLE = "HARNESS_HOST_BOOTSTRAP"
DEFAULT_BOOTSTRAP_RELATIVE = Path(".harness/host/bootstrap.json")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_POLICY = {
    "configuration_grants_write_authority": False,
    "configuration_completes_taskrun": False,
    "automatic_merge": False,
    "completion_authority": "TaskRun",
    "authority_effect": False,
}


class ConcreteHostBootstrapError(RuntimeError):
    """Raised when generated Host composition is missing, stale, or unsafe."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def bootstrap_digest(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("bootstrap_sha256", None)
    return hashlib.sha256(_canonical(unsigned)).hexdigest()


def seal_bootstrap(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["bootstrap_sha256"] = bootstrap_digest(result)
    return validate_bootstrap_declaration(result)


def _closed(payload: Mapping[str, Any], fields: set[str], *, field: str) -> None:
    missing = sorted(fields - set(payload))
    unexpected = sorted(set(payload) - fields)
    if missing or unexpected:
        raise ConcreteHostBootstrapError(
            f"{field} fields are not closed: missing={missing} unexpected={unexpected}"
        )


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ConcreteHostBootstrapError(f"{field} must be an object")
    return dict(value)


def _relative(value: object, *, field: str, suffix: str | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConcreteHostBootstrapError(f"{field} must be a bounded relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ConcreteHostBootstrapError(f"{field} must be a bounded relative path")
    if suffix is not None and path.suffix != suffix:
        raise ConcreteHostBootstrapError(f"{field} must end with {suffix}")
    if path.parts[:2] == (".harness", "starters"):
        raise ConcreteHostBootstrapError(f"{field} cannot write into an immutable Starter")
    return path.as_posix()


def _workspace_path(workspace: Path, relative: str, *, field: str) -> Path:
    candidate = workspace / relative
    if candidate.is_symlink():
        raise ConcreteHostBootstrapError(f"{field} cannot be a symlink")
    try:
        candidate.resolve().relative_to(workspace.resolve())
    except ValueError as exc:
        raise ConcreteHostBootstrapError(f"{field} escapes the project workspace") from exc
    return candidate


def _profiles(value: object) -> dict[str, list[str]]:
    payload = _object(value, field="providers.execution_profiles")
    _closed(payload, {"test.run", "quality.evaluate"}, field="providers.execution_profiles")
    result: dict[str, list[str]] = {}
    for capability, raw in payload.items():
        if (
            not isinstance(raw, list)
            or not raw
            or any(not isinstance(item, str) or not item.strip() for item in raw)
        ):
            raise ConcreteHostBootstrapError(
                f"providers.execution_profiles.{capability} must be a non-empty string array"
            )
        profiles = [item.strip() for item in raw]
        if len(profiles) != len(set(profiles)):
            raise ConcreteHostBootstrapError(
                f"providers.execution_profiles.{capability} must be unique"
            )
        result[capability] = profiles
    return result


def validate_bootstrap_declaration(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = _object(raw, field="bootstrap")
    _closed(
        payload,
        {
            "schema",
            "starter",
            "registration",
            "checkpointer",
            "providers",
            "runtime",
            "policy",
            "bootstrap_sha256",
        },
        field="bootstrap",
    )
    if payload.get("schema") != CONCRETE_HOST_BOOTSTRAP_SCHEMA:
        raise ConcreteHostBootstrapError("unsupported concrete Host bootstrap schema")
    starter = _object(payload.get("starter"), field="starter")
    _closed(starter, {"starter_id", "package_sha256"}, field="starter")
    if not isinstance(starter.get("starter_id"), str) or not starter["starter_id"].strip():
        raise ConcreteHostBootstrapError("starter.starter_id must be non-empty")
    if not isinstance(starter.get("package_sha256"), str) or not _SHA256.fullmatch(
        starter["package_sha256"]
    ):
        raise ConcreteHostBootstrapError("starter.package_sha256 must be a SHA-256 digest")

    registration = _object(payload.get("registration"), field="registration")
    _closed(registration, {"path", "sha256"}, field="registration")
    registration["path"] = _relative(
        registration.get("path"), field="registration.path", suffix=".json"
    )
    if not isinstance(registration.get("sha256"), str) or not _SHA256.fullmatch(
        registration["sha256"]
    ):
        raise ConcreteHostBootstrapError("registration.sha256 must be a SHA-256 digest")

    checkpointer = _object(payload.get("checkpointer"), field="checkpointer")
    _closed(checkpointer, {"type", "path"}, field="checkpointer")
    if checkpointer.get("type") != "sqlite":
        raise ConcreteHostBootstrapError("only the durable sqlite checkpointer is supported")
    checkpointer["path"] = _relative(
        checkpointer.get("path"), field="checkpointer.path", suffix=".sqlite3"
    )

    providers = _object(payload.get("providers"), field="providers")
    _closed(
        providers,
        {"execution_profiles", "process_timeout_seconds", "github"},
        field="providers",
    )
    providers["execution_profiles"] = _profiles(providers.get("execution_profiles"))
    timeout = providers.get("process_timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
        raise ConcreteHostBootstrapError("providers.process_timeout_seconds must be 1..3600")
    github = providers.get("github")
    if github is not None:
        github = _object(github, field="providers.github")
        _closed(
            github,
            {
                "repository_full_name",
                "token_environment_variable",
                "code_review_provider_id",
                "ci_provider_id",
                "api_base",
            },
            field="providers.github",
        )
        repository = github.get("repository_full_name")
        if (
            not isinstance(repository, str)
            or repository.count("/") != 1
            or any(not part for part in repository.split("/"))
        ):
            raise ConcreteHostBootstrapError(
                "providers.github.repository_full_name must use owner/repository"
            )
        variable = github.get("token_environment_variable")
        if not isinstance(variable, str) or not _ENVIRONMENT_VARIABLE.fullmatch(variable):
            raise ConcreteHostBootstrapError(
                "providers.github.token_environment_variable must be an uppercase name"
            )
        for field in ("code_review_provider_id", "ci_provider_id", "api_base"):
            if not isinstance(github.get(field), str) or not github[field].strip():
                raise ConcreteHostBootstrapError(f"providers.github.{field} must be non-empty")
        if github["api_base"] != "https://api.github.com":
            raise ConcreteHostBootstrapError("providers.github.api_base must be official GitHub API")
        providers["github"] = github

    runtime = _object(payload.get("runtime"), field="runtime")
    _closed(
        runtime,
        {"session_root", "taskrun_root", "workspace_fingerprint"},
        field="runtime",
    )
    runtime["session_root"] = _relative(runtime.get("session_root"), field="runtime.session_root")
    runtime["taskrun_root"] = _relative(runtime.get("taskrun_root"), field="runtime.taskrun_root")
    fingerprint = runtime.get("workspace_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not fingerprint.strip()):
        raise ConcreteHostBootstrapError(
            "runtime.workspace_fingerprint must be null or non-empty"
        )
    if payload.get("policy") != _POLICY:
        raise ConcreteHostBootstrapError("bootstrap policy must preserve fixed authority boundaries")
    digest = payload.get("bootstrap_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ConcreteHostBootstrapError("bootstrap_sha256 must be a SHA-256 digest")
    normalized = {
        "schema": CONCRETE_HOST_BOOTSTRAP_SCHEMA,
        "starter": starter,
        "registration": registration,
        "checkpointer": checkpointer,
        "providers": providers,
        "runtime": runtime,
        "policy": dict(_POLICY),
        "bootstrap_sha256": digest,
    }
    if bootstrap_digest(normalized) != digest:
        raise ConcreteHostBootstrapError("bootstrap fingerprint mismatch")
    return normalized


def load_bootstrap(path: Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path)
    if source.is_symlink():
        raise ConcreteHostBootstrapError("bootstrap file is missing or unsafe")
    resolved = source.resolve()
    if not resolved.is_file():
        raise ConcreteHostBootstrapError("bootstrap file is missing or unsafe")
    if tuple(resolved.parts[-3:]) != tuple(DEFAULT_BOOTSTRAP_RELATIVE.parts):
        raise ConcreteHostBootstrapError(
            "bootstrap must be stored at .harness/host/bootstrap.json"
        )
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConcreteHostBootstrapError("bootstrap JSON is unreadable") from exc
    if not isinstance(raw, Mapping):
        raise ConcreteHostBootstrapError("bootstrap JSON must be an object")
    project_workspace = resolved.parents[2]
    if not project_workspace.is_dir() or project_workspace.is_symlink():
        raise ConcreteHostBootstrapError("project workspace is missing or unsafe")
    return project_workspace, validate_bootstrap_declaration(raw)


class ProjectCommandProfileRunner:
    """Run only immutable Starter project commands, never Workflow-provided shell."""

    def __init__(
        self,
        *,
        workspace: Path,
        commands: Mapping[str, str],
        timeout: int,
        denied_environment_variables: tuple[str, ...] = (),
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.timeout = timeout
        self.environment = dict(os.environ)
        for variable in denied_environment_variables:
            self.environment.pop(variable, None)
        self.commands: dict[str, tuple[str, ...]] = {}
        for profile, command in commands.items():
            try:
                argv = tuple(shlex.split(str(command)))
            except ValueError as exc:
                raise ConcreteHostBootstrapError(
                    f"project command {profile!r} has invalid quoting"
                ) from exc
            if not argv:
                raise ConcreteHostBootstrapError(f"project command {profile!r} is empty")
            self.commands[str(profile)] = argv

    def __call__(self, profile: str, *, state_file: Path) -> Mapping[str, Any]:
        argv = self.commands.get(profile)
        if argv is None:
            raise ConcreteHostBootstrapError(f"unknown sealed project command profile: {profile}")
        try:
            completed = subprocess.run(
                argv,
                cwd=self.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self.environment,
            )
            result = {
                "schema": "project-command-profile-result@1",
                "profile": profile,
                "argv": list(argv),
                "status": "PASS" if completed.returncode == 0 else "FAIL",
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-8000:],
                "stderr_tail": completed.stderr[-8000:],
                "authority_effect": False,
            }
        except subprocess.TimeoutExpired:
            result = {
                "schema": "project-command-profile-result@1",
                "profile": profile,
                "argv": list(argv),
                "status": "FAIL",
                "error": "project command timed out",
                "authority_effect": False,
            }
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return result


def build_orchestrator(*, host_id: str):
    """Built-in trusted factory selected outside the untrusted Host command."""

    configured = os.environ.get(DEFAULT_BOOTSTRAP_ENVIRONMENT_VARIABLE, "").strip()
    if not configured:
        raise ConcreteHostBootstrapError(
            f"{DEFAULT_BOOTSTRAP_ENVIRONMENT_VARIABLE} is required"
        )
    project_workspace, config = load_bootstrap(Path(configured))

    from langgraph.checkpoint.sqlite import SqliteSaver
    from starter_host_orchestrator import StarterHostOrchestrator
    from starter_provider_bootstrap import (
        GitHubPullRequestConfiguration,
        build_concrete_starter_provider_registry,
    )
    from starter_runtime import load_starter_registration

    registration_path = _workspace_path(
        project_workspace,
        config["registration"]["path"],
        field="registration.path",
    )
    loaded = load_starter_registration(
        project_workspace=project_workspace,
        registration=registration_path,
        registry_workspace=Path(__file__).resolve().parents[2],
    )
    if loaded.payload["registration_sha256"] != config["registration"]["sha256"]:
        raise ConcreteHostBootstrapError("registered Starter fingerprint drifted")
    if (
        loaded.verification.starter.starter_id != config["starter"]["starter_id"]
        or loaded.verification.package_sha256 != config["starter"]["package_sha256"]
    ):
        raise ConcreteHostBootstrapError("registered Starter package identity drifted")

    provider_config = config["providers"]
    command_ids = {
        profile
        for profiles in provider_config["execution_profiles"].values()
        for profile in profiles
    }
    commands = loaded.verification.project.commands
    missing_commands = sorted(command_ids - set(commands))
    if missing_commands:
        raise ConcreteHostBootstrapError(
            f"bootstrap references unknown sealed project commands: {missing_commands}"
        )
    github_raw = provider_config["github"]
    runner = ProjectCommandProfileRunner(
        workspace=project_workspace,
        commands={command: commands[command] for command in command_ids},
        timeout=provider_config["process_timeout_seconds"],
        denied_environment_variables=(
            (github_raw["token_environment_variable"],)
            if github_raw is not None
            else ()
        ),
    )
    github = None
    if github_raw is not None:
        github = GitHubPullRequestConfiguration.from_environment(**github_raw)
    assembly = build_concrete_starter_provider_registry(
        workspace=project_workspace,
        write_scope=loaded.verification.project.write_scope,
        allowed_profiles=provider_config["execution_profiles"],
        github=github,
        process_runner=runner,
    )

    checkpointer_path = _workspace_path(
        project_workspace,
        config["checkpointer"]["path"],
        field="checkpointer.path",
    )
    checkpointer_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpointer_path, check_same_thread=False)
    orchestrator = StarterHostOrchestrator(
        registry_workspace=Path(__file__).resolve().parents[2],
        project_workspace=project_workspace,
        registration=registration_path,
        host_id=host_id,
        provider_adapters=assembly.registry,
        checkpointer=SqliteSaver(connection),
        workspace_fingerprint=config["runtime"]["workspace_fingerprint"],
        write_authority_guard=None,
        human_gate_adapter=None,
        session_root=config["runtime"]["session_root"],
        taskrun_root=config["runtime"]["taskrun_root"],
    )
    # Keep the sqlite connection alive for the one-shot command lifetime.
    orchestrator._concrete_bootstrap_connection = connection
    orchestrator._concrete_bootstrap_policy = {
        "provider_ids": list(assembly.provider_ids),
        "write_authority_injected": False,
        "human_gate_injected": False,
        **_POLICY,
    }
    return orchestrator


__all__ = [
    "CONCRETE_HOST_BOOTSTRAP_SCHEMA",
    "ConcreteHostBootstrapError",
    "DEFAULT_BOOTSTRAP_ENVIRONMENT_VARIABLE",
    "ProjectCommandProfileRunner",
    "bootstrap_digest",
    "build_orchestrator",
    "load_bootstrap",
    "seal_bootstrap",
    "validate_bootstrap_declaration",
]
