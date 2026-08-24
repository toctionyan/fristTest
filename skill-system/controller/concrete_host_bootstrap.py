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


CONCRETE_HOST_BOOTSTRAP_SCHEMA = "concrete-host-bootstrap@4"
DEFAULT_BOOTSTRAP_ENVIRONMENT_VARIABLE = "HARNESS_HOST_BOOTSTRAP"
DEFAULT_BOOTSTRAP_RELATIVE = Path(".harness/host/bootstrap.json")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_POLICY = {
    "configuration_grants_write_authority": False,
    "configuration_completes_taskrun": False,
    "scheduler_is_authority": False,
    "external_event_completes_taskrun": False,
    "provider_webhook_is_authority": False,
    "provider_webhook_interprets_ci": False,
    "provider_polling": False,
    "automatic_merge": False,
    "completion_authority": "TaskRun",
    "authority_effect": False,
}
_SCHEDULER_DEFAULT = {
    "type": "durable-local-one-shot",
    "event_root": ".harness/runtime/external-events",
    "receipt_root": ".harness/runtime/external-wakeup-receipts",
    "lock_root": ".harness/runtime/external-wakeup-locks",
    "max_events_per_run": 100,
    "provider_polling": False,
    "authority_effect": False,
}


def _provider_webhook_default(
    github: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if github is None:
        return None
    return {
        "type": "github-workflow-run-hmac-sha256",
        "provider_id": github.get("ci_provider_id", "github.actions"),
        "repository_full_name": github.get("repository_full_name"),
        "secret_environment_variable": "GITHUB_WEBHOOK_SECRET",
        "evidence_root": ".harness/runtime/provider-webhook-evidence/github",
        "receipt_root": ".harness/runtime/provider-webhook-receipts/github",
        "lock_root": ".harness/runtime/provider-webhook-locks/github",
        "max_body_bytes": 1048576,
        "http_path_prefix": "/v1/github/workflow-run/",
        "automatic_wake": True,
        "provider_polling": False,
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
    # Programmatic authoring upgrades previous non-authorizing bootstrap input.
    # Persisted files are validated against the current closed schema and cannot
    # omit or weaken the successor fields.
    if result.get("schema") == "concrete-host-bootstrap@3":
        result["schema"] = CONCRETE_HOST_BOOTSTRAP_SCHEMA
    result.setdefault("scheduler", dict(_SCHEDULER_DEFAULT))
    providers = result.get("providers")
    github = providers.get("github") if isinstance(providers, Mapping) else None
    result.setdefault(
        "provider_webhook",
        _provider_webhook_default(github if isinstance(github, Mapping) else None),
    )
    policy = result.get("policy")
    if isinstance(policy, Mapping):
        normalized_policy = dict(policy)
        for field in (
            "scheduler_is_authority",
            "external_event_completes_taskrun",
            "provider_webhook_is_authority",
            "provider_webhook_interprets_ci",
            "provider_polling",
        ):
            normalized_policy.setdefault(field, False)
        result["policy"] = normalized_policy
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
            "authority",
            "human_gate",
            "scheduler",
            "provider_webhook",
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

    authority = _object(payload.get("authority"), field="authority")
    _closed(
        authority,
        {
            "type",
            "active_contract_path",
            "audit_root",
            "generic_merge_authority",
        },
        field="authority",
    )
    if authority.get("type") != "repair-change-permit":
        raise ConcreteHostBootstrapError(
            "authority.type must reuse the repair ChangePermit authority"
        )
    authority["active_contract_path"] = _relative(
        authority.get("active_contract_path"),
        field="authority.active_contract_path",
        suffix=".json",
    )
    authority["audit_root"] = _relative(
        authority.get("audit_root"), field="authority.audit_root"
    )
    if not authority["audit_root"].startswith(".harness/"):
        raise ConcreteHostBootstrapError(
            "authority.audit_root must stay beneath .harness"
        )
    if authority.get("generic_merge_authority") is not False:
        raise ConcreteHostBootstrapError(
            "generic Write Authority cannot enable merge authority"
        )

    human_gate = _object(payload.get("human_gate"), field="human_gate")
    _closed(
        human_gate,
        {"type", "gate_root", "decision_root", "authority_effect"},
        field="human_gate",
    )
    if human_gate.get("type") != "durable-local":
        raise ConcreteHostBootstrapError("human_gate.type must be durable-local")
    for field in ("gate_root", "decision_root"):
        human_gate[field] = _relative(
            human_gate.get(field), field=f"human_gate.{field}"
        )
        if not human_gate[field].startswith(".harness/"):
            raise ConcreteHostBootstrapError(
                f"human_gate.{field} must stay beneath .harness"
            )
    if human_gate.get("authority_effect") is not False:
        raise ConcreteHostBootstrapError("Human Gate configuration cannot grant authority")

    scheduler = _object(payload.get("scheduler"), field="scheduler")
    _closed(
        scheduler,
        {
            "type",
            "event_root",
            "receipt_root",
            "lock_root",
            "max_events_per_run",
            "provider_polling",
            "authority_effect",
        },
        field="scheduler",
    )
    if scheduler.get("type") != "durable-local-one-shot":
        raise ConcreteHostBootstrapError(
            "scheduler.type must be durable-local-one-shot"
        )
    for field in ("event_root", "receipt_root", "lock_root"):
        scheduler[field] = _relative(
            scheduler.get(field), field=f"scheduler.{field}"
        )
        if not scheduler[field].startswith(".harness/"):
            raise ConcreteHostBootstrapError(
                f"scheduler.{field} must stay beneath .harness"
            )
    if len(
        {
            scheduler["event_root"],
            scheduler["receipt_root"],
            scheduler["lock_root"],
        }
    ) != 3:
        raise ConcreteHostBootstrapError(
            "scheduler event, receipt, and lock roots must be distinct"
        )
    limit = scheduler.get("max_events_per_run")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ConcreteHostBootstrapError(
            "scheduler.max_events_per_run must be 1..1000"
        )
    if scheduler.get("provider_polling") is not False:
        raise ConcreteHostBootstrapError("scheduler cannot enable Provider polling")
    if scheduler.get("authority_effect") is not False:
        raise ConcreteHostBootstrapError("scheduler configuration cannot grant authority")

    provider_webhook = payload.get("provider_webhook")
    if github is None:
        if provider_webhook is not None:
            raise ConcreteHostBootstrapError(
                "provider_webhook requires providers.github"
            )
    else:
        provider_webhook = _object(
            provider_webhook, field="provider_webhook"
        )
        _closed(
            provider_webhook,
            {
                "type",
                "provider_id",
                "repository_full_name",
                "secret_environment_variable",
                "evidence_root",
                "receipt_root",
                "lock_root",
                "max_body_bytes",
                "http_path_prefix",
                "automatic_wake",
                "provider_polling",
                "authority_effect",
            },
            field="provider_webhook",
        )
        if provider_webhook.get("type") != "github-workflow-run-hmac-sha256":
            raise ConcreteHostBootstrapError(
                "provider_webhook.type must be github-workflow-run-hmac-sha256"
            )
        if provider_webhook.get("provider_id") != github["ci_provider_id"]:
            raise ConcreteHostBootstrapError(
                "provider_webhook.provider_id must match providers.github.ci_provider_id"
            )
        if (
            provider_webhook.get("repository_full_name")
            != github["repository_full_name"]
        ):
            raise ConcreteHostBootstrapError(
                "provider_webhook.repository_full_name must match providers.github"
            )
        secret_variable = provider_webhook.get("secret_environment_variable")
        if (
            not isinstance(secret_variable, str)
            or not _ENVIRONMENT_VARIABLE.fullmatch(secret_variable)
        ):
            raise ConcreteHostBootstrapError(
                "provider_webhook.secret_environment_variable must be an uppercase name"
            )
        if secret_variable == github["token_environment_variable"]:
            raise ConcreteHostBootstrapError(
                "provider webhook secret must not reuse the GitHub API token variable"
            )
        webhook_roots: list[str] = []
        for field in ("evidence_root", "receipt_root", "lock_root"):
            provider_webhook[field] = _relative(
                provider_webhook.get(field), field=f"provider_webhook.{field}"
            )
            if not provider_webhook[field].startswith(".harness/"):
                raise ConcreteHostBootstrapError(
                    f"provider_webhook.{field} must stay beneath .harness"
                )
            webhook_roots.append(provider_webhook[field])
        if len(set(webhook_roots)) != 3 or set(webhook_roots).intersection(
            {scheduler["event_root"], scheduler["receipt_root"], scheduler["lock_root"]}
        ):
            raise ConcreteHostBootstrapError(
                "provider webhook roots must be distinct from each other and Scheduler roots"
            )
        body_limit = provider_webhook.get("max_body_bytes")
        if (
            not isinstance(body_limit, int)
            or isinstance(body_limit, bool)
            or not 1 <= body_limit <= 10 * 1024 * 1024
        ):
            raise ConcreteHostBootstrapError(
                "provider_webhook.max_body_bytes must be 1..10485760"
            )
        if provider_webhook.get("http_path_prefix") != "/v1/github/workflow-run/":
            raise ConcreteHostBootstrapError(
                "provider_webhook.http_path_prefix is fixed"
            )
        if provider_webhook.get("automatic_wake") is not True:
            raise ConcreteHostBootstrapError(
                "provider_webhook must deliver through one Scheduler wake"
            )
        if provider_webhook.get("provider_polling") is not False:
            raise ConcreteHostBootstrapError(
                "provider_webhook cannot enable Provider polling"
            )
        if provider_webhook.get("authority_effect") is not False:
            raise ConcreteHostBootstrapError(
                "provider_webhook configuration cannot grant authority"
            )

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
        "authority": authority,
        "human_gate": human_gate,
        "scheduler": scheduler,
        "provider_webhook": provider_webhook,
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
    from durable_external_event_scheduler import DurableExternalEventScheduler
    from durable_human_gate import DurableHumanGateAdapter
    from governed_write_authority import ChangePermitWriteAuthorityGuard
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
        denied_environment_variables=tuple(
            value
            for value in (
                (
                    github_raw["token_environment_variable"]
                    if github_raw is not None
                    else None
                ),
                (
                    config["provider_webhook"]["secret_environment_variable"]
                    if config["provider_webhook"] is not None
                    else None
                ),
            )
            if value is not None
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
    authority_config = config["authority"]
    write_authority_guard = ChangePermitWriteAuthorityGuard(
        workspace=project_workspace,
        active_contract_path=authority_config["active_contract_path"],
        audit_root=authority_config["audit_root"],
    )
    human_gate_config = config["human_gate"]
    human_gate_adapter = DurableHumanGateAdapter(
        workspace=project_workspace,
        gate_root=human_gate_config["gate_root"],
        decision_root=human_gate_config["decision_root"],
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
        write_authority_guard=write_authority_guard,
        human_gate_adapter=human_gate_adapter,
        session_root=config["runtime"]["session_root"],
        taskrun_root=config["runtime"]["taskrun_root"],
    )
    scheduler_config = config["scheduler"]
    wakeup_scheduler = DurableExternalEventScheduler(
        workspace=project_workspace,
        orchestrator=orchestrator,
        event_root=scheduler_config["event_root"],
        receipt_root=scheduler_config["receipt_root"],
        lock_root=scheduler_config["lock_root"],
        max_events_per_run=scheduler_config["max_events_per_run"],
    )
    provider_webhook_config = config["provider_webhook"]
    provider_webhook_transport = None
    if provider_webhook_config is not None:
        from github_webhook_external_event_transport import (
            GitHubWorkflowRunWebhookTransport,
        )

        provider_webhook_transport = GitHubWorkflowRunWebhookTransport(
            workspace=project_workspace,
            scheduler=wakeup_scheduler,
            host_id=host_id,
            repository_full_name=provider_webhook_config["repository_full_name"],
            provider_id=provider_webhook_config["provider_id"],
            secret_environment_variable=provider_webhook_config[
                "secret_environment_variable"
            ],
            evidence_root=provider_webhook_config["evidence_root"],
            receipt_root=provider_webhook_config["receipt_root"],
            lock_root=provider_webhook_config["lock_root"],
            max_body_bytes=provider_webhook_config["max_body_bytes"],
        )
    # Keep the sqlite connection alive for the one-shot command lifetime.
    orchestrator._concrete_bootstrap_connection = connection
    orchestrator._concrete_wakeup_scheduler = wakeup_scheduler
    orchestrator._concrete_provider_webhook_transport = provider_webhook_transport
    orchestrator._concrete_bootstrap_policy = {
        "provider_ids": list(assembly.provider_ids),
        "write_authority_injected": True,
        "write_authority_currently_granted": False,
        "write_authority_source": "active ChangePermit; evaluated per mutating dispatch",
        "generic_merge_authority": False,
        "human_gate_injected": True,
        "scheduler_injected": True,
        "scheduler_type": "durable-local-one-shot",
        "provider_webhook_injected": provider_webhook_transport is not None,
        "provider_webhook_type": (
            provider_webhook_config["type"]
            if provider_webhook_config is not None
            else None
        ),
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
