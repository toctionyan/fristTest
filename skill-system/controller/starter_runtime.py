from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from capability_registry import CapabilityBinding, load_capability_contracts, load_provider_registry
from harness_authoring import compile_workflow_declaration, load_declaration, parse_skill_contract
from harness_composition import compose_workflow, parse_composition_declaration
from harness_starter import StarterVerification, verify_starter
from langgraph_workflow_runtime import (
    RUNTIME_STATUS_BLOCKED,
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_HUMAN_GATE,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    RUNTIME_STATUS_WAITING_HOST,
    WorkflowRuntimeState,
    build_langgraph_workflow,
    initial_workflow_state,
    is_durable_checkpointer,
    resume_workflow_state,
)
from task_run import TERMINAL_STATUSES, TaskRunStore
from workflow_activation import WorkflowActivation, activate_workflow_spec
from workflow_dispatcher import (
    CanonicalSkillInvocationAdapter,
    ProviderAdapterRegistry,
    SkillHostAdapter,
    WorkflowAdapterDispatcher,
    WriteAuthorityGuard,
)
from workflow_registry import WorkflowSpec
from workflow_taskrun_bridge import (
    checkpoint_workflow_resume,
    checkpoint_workflow_start,
    checkpoint_workflow_state,
)


STARTER_RUNTIME_REGISTRATION_SCHEMA = "harness-starter-runtime-registration@1"
STARTER_RUNTIME_ROUTE_SCHEMA = "harness-starter-runtime-route@1"
STARTER_RUNTIME_EXECUTION_SCHEMA = "harness-starter-runtime-execution@1"
STARTER_HOST_SELECTION_REQUEST_SCHEMA = "starter-host-selection-request@1"
STARTER_HOST_SELECTION_SCHEMA = "starter-host-selection@1"
STARTER_HOST_CONFIRMATION_SCHEMA = "starter-host-selection-confirmation@1"
STARTER_HOST_SELECTION_RESOLUTION_SCHEMA = "starter-host-selection-resolution@1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENTRYPOINT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class StarterRuntimeError(RuntimeError):
    """Raised when a Starter registration, route, or execution is unsafe."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bounded_relative(value: object, *, field: str, suffix: str | None = None) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise StarterRuntimeError(f"{field} must be a bounded relative path")
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if raw != value.strip() or path.is_absolute() or not path.parts or "." in path.parts or ".." in path.parts:
        raise StarterRuntimeError(f"{field} must be a bounded relative path")
    if suffix is not None and path.suffix.casefold() != suffix:
        raise StarterRuntimeError(f"{field} must end with {suffix}")
    return Path(path.as_posix())


def _member(root: Path, relative: Path, *, field: str, directory: bool = False) -> Path:
    root = root.resolve()
    candidate = root / relative
    current = candidate
    while current != root:
        if current.is_symlink():
            raise StarterRuntimeError(f"{field} contains a symlink")
        current = current.parent
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StarterRuntimeError(f"{field} escapes the project workspace") from exc
    exists = resolved.is_dir() if directory else resolved.is_file()
    if not exists:
        raise StarterRuntimeError(f"{field} is missing: {relative.as_posix()}")
    return resolved


def _relative_to(root: Path, path: Path, *, field: str) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise StarterRuntimeError(f"{field} must be inside the project workspace") from exc


def _workflow_index(starter_root: Path, verification: StarterVerification) -> dict[str, WorkflowSpec]:
    manifest = verification.starter
    raw_workflows: dict[str, dict[str, Any]] = {}
    specs: dict[str, WorkflowSpec] = {}
    for relative in manifest.workflows:
        raw = load_declaration(starter_root / relative)
        compiled = compile_workflow_declaration(raw)
        raw_workflows[compiled.spec.workflow_id] = raw
        specs[compiled.spec.workflow_id] = compiled.spec

    skill_rows = [load_declaration(starter_root / relative) for relative in manifest.skill_contracts]
    for relative in manifest.compositions:
        raw = load_declaration(starter_root / relative)
        composition = parse_composition_declaration(raw)
        try:
            base = raw_workflows[composition.base_workflow]
        except KeyError as exc:
            raise StarterRuntimeError(
                f"composition {composition.composition_id!r} has no package-local base Workflow"
            ) from exc
        plan = compose_workflow(base, raw, skill_rows)
        specs[composition.composition_id] = compile_workflow_declaration(plan.derived_workflow).spec
    return specs


def _workflow_seals(
    verification: StarterVerification, specs: Mapping[str, WorkflowSpec]
) -> dict[str, dict[str, str]]:
    return {
        name: {
            "workflow_id": workflow_id,
            "workflow_sha256": _digest(specs[workflow_id].as_dict()),
        }
        for name, workflow_id in sorted(verification.starter.entrypoints.items())
    }


def _registration_unsigned(
    *, project_workspace: Path, starter_root: Path, verification: StarterVerification
) -> dict[str, Any]:
    if verification.starter.automatic_merge:
        raise StarterRuntimeError(
            "Starter runtime registration requires automatic_merge: false"
        )
    starter_relative = _relative_to(
        project_workspace, starter_root, field="Starter directory"
    ).as_posix()
    specs = _workflow_index(starter_root, verification)
    skill_paths: dict[str, dict[str, str]] = {}
    for skill_id, package_relative in sorted(verification.starter.skill_entrypoints.items()):
        project_relative = (Path(starter_relative) / package_relative).as_posix()
        path = _member(
            project_workspace,
            Path(project_relative),
            field=f"Skill entrypoint {skill_id}",
        )
        skill_paths[skill_id] = {
            "path": project_relative,
            "sha256": _file_digest(path),
        }
    return {
        "schema": STARTER_RUNTIME_REGISTRATION_SCHEMA,
        "starter": {
            "starter_id": verification.starter.starter_id,
            "version": verification.starter.version,
            "path": starter_relative,
            "package_sha256": verification.package_sha256,
        },
        "project": {
            "project_id": verification.project.project_id,
            "project_type": verification.project.project_type,
            "commands": dict(verification.project.commands),
            "write_scope": list(verification.project.write_scope),
            "providers": dict(verification.project.providers),
        },
        "entrypoints": _workflow_seals(verification, specs),
        "skills": skill_paths,
        "policy": {
            "standalone_application": verification.starter.standalone_application,
            "automatic_merge": False,
            "registration_executes_workflow": False,
            "registration_grants_write_authority": False,
            "routing_grants_write_authority": False,
            "activation_completes_taskrun": False,
            "completion_authority": "TaskRun",
            "authority_effect": False,
        },
    }


def register_starter_runtime(
    *,
    project_workspace: Path,
    starter_directory: Path,
    output: Path,
    registry_workspace: Path,
) -> dict[str, Any]:
    project_root = Path(project_workspace).resolve()
    if not project_root.is_dir() or project_root.is_symlink():
        raise StarterRuntimeError("project workspace is missing or unsafe")
    starter_root = Path(starter_directory).resolve()
    starter_relative = _relative_to(project_root, starter_root, field="Starter directory")
    _member(project_root, starter_relative, field="Starter directory", directory=True)
    output_path = Path(output).resolve()
    output_relative = _relative_to(project_root, output_path, field="registration output")
    if output_path.exists() or output_path.is_symlink():
        raise StarterRuntimeError(f"refusing to overwrite registration output: {output_path}")
    try:
        output_path.relative_to(starter_root)
    except ValueError:
        pass
    else:
        raise StarterRuntimeError("registration output must be outside the immutable Starter package")
    parent = output_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    _member(project_root, output_relative.parent, field="registration parent", directory=True)

    verification = verify_starter(starter_root, registry_workspace=registry_workspace)
    payload = _registration_unsigned(
        project_workspace=project_root,
        starter_root=starter_root,
        verification=verification,
    )
    payload["registration_sha256"] = _digest(payload)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    payload["registration_ref"] = f"file:{output_relative.as_posix()}"
    return payload


@dataclass(frozen=True)
class LoadedStarterRegistration:
    project_workspace: Path
    registration_path: Path
    starter_root: Path
    verification: StarterVerification
    payload: dict[str, Any]
    workflows: dict[str, WorkflowSpec]
    skill_paths: dict[str, Path]

    @property
    def registration_ref(self) -> str:
        relative = self.registration_path.relative_to(self.project_workspace)
        return f"file:{relative.as_posix()}"


def load_starter_registration(
    *, project_workspace: Path, registration: Path, registry_workspace: Path
) -> LoadedStarterRegistration:
    project_root = Path(project_workspace).resolve()
    registration_relative = _relative_to(
        project_root, Path(registration).resolve(), field="registration"
    )
    path = _member(project_root, registration_relative, field="registration")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StarterRuntimeError(f"registration JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != STARTER_RUNTIME_REGISTRATION_SCHEMA:
        raise StarterRuntimeError(
            f"registration schema must be {STARTER_RUNTIME_REGISTRATION_SCHEMA!r}"
        )
    expected_fingerprint = payload.get("registration_sha256")
    unsigned = dict(payload)
    unsigned.pop("registration_sha256", None)
    if not isinstance(expected_fingerprint, str) or not _SHA256.fullmatch(expected_fingerprint):
        raise StarterRuntimeError("registration requires a valid registration_sha256")
    if _digest(unsigned) != expected_fingerprint:
        raise StarterRuntimeError("registration fingerprint mismatch")

    starter = payload.get("starter")
    if not isinstance(starter, dict):
        raise StarterRuntimeError("registration starter must be an object")
    starter_relative = _bounded_relative(starter.get("path"), field="starter.path")
    starter_root = _member(project_root, starter_relative, field="starter.path", directory=True)
    try:
        path.relative_to(starter_root)
    except ValueError:
        pass
    else:
        raise StarterRuntimeError("registration cannot be stored inside its Starter package")
    verification = verify_starter(starter_root, registry_workspace=registry_workspace)
    if starter != {
        "starter_id": verification.starter.starter_id,
        "version": verification.starter.version,
        "path": starter_relative.as_posix(),
        "package_sha256": verification.package_sha256,
    }:
        raise StarterRuntimeError("registered Starter identity or package digest drifted")

    workflows = _workflow_index(starter_root, verification)
    if payload.get("entrypoints") != _workflow_seals(verification, workflows):
        raise StarterRuntimeError("registered Workflow entrypoint identity or digest drifted")

    expected_unsigned = _registration_unsigned(
        project_workspace=project_root,
        starter_root=starter_root,
        verification=verification,
    )
    if unsigned != expected_unsigned:
        raise StarterRuntimeError("registration content does not match the verified Starter package")
    skill_paths = {
        skill_id: _bounded_relative(row["path"], field=f"skills.{skill_id}.path", suffix=".md")
        for skill_id, row in payload["skills"].items()
    }
    return LoadedStarterRegistration(
        project_workspace=project_root,
        registration_path=path,
        starter_root=starter_root,
        verification=verification,
        payload=payload,
        workflows=workflows,
        skill_paths=skill_paths,
    )


def parse_starter_command(command: str) -> dict[str, str]:
    text = str(command or "").strip()
    patterns = (
        (r"^/harness audit all(?:\s+(?P<payload>.+))?$", "overall_audit"),
        (r"^/harness audit module\s+(?P<payload>\S(?:.*\S)?)$", "module_audit"),
        (r"^/harness architecture\s+(?P<payload>\S(?:.*\S)?)$", "architecture_review"),
        (r"^/harness repair\s+(?P<payload>\S(?:.*\S)?)\s+--local$", "repair_and_prove"),
        (r"^/harness repair\s+(?P<payload>\S(?:.*\S)?)\s+--ci$", "repair_with_ci"),
        (r"^/harness full-dev\s+(?P<payload>\S(?:.*\S)?)\s+--ci$", "full_dev"),
    )
    matches: list[tuple[str, str]] = []
    for pattern, entrypoint in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            payload = str(match.groupdict().get("payload") or "").strip()
            if any(token.startswith("--") for token in payload.split()):
                continue
            matches.append((entrypoint, payload))
    if len(matches) != 1:
        raise StarterRuntimeError(
            "command must exactly match one supported /harness form; write routes require --local or --ci"
        )
    entrypoint, payload = matches[0]
    return {"entrypoint": entrypoint, "payload": payload}


@dataclass(frozen=True)
class ResolvedStarterEntrypoint:
    registration: LoadedStarterRegistration
    entrypoint: str
    workflow: WorkflowSpec
    activation: WorkflowActivation
    user_payload: str

    def effect_preview(self, *, registry_workspace: Path) -> dict[str, Any]:
        contracts = load_capability_contracts(registry_workspace)
        providers = load_provider_registry(registry_workspace)
        required = list(self.workflow.required_capabilities)
        mutating = [capability for capability in required if contracts[capability].mutates]
        integration = [
            capability
            for capability in required
            if providers[self.registration.verification.project.providers[capability]].provider_type
            == "integration"
        ]
        return {
            "schema": "harness-starter-effect-preview@1",
            "workflow_id": self.workflow.workflow_id,
            "mode": self.workflow.mode,
            "required_capabilities": required,
            "mutating_capabilities": mutating,
            "integration_effects": integration,
            "write_scope": list(self.registration.verification.project.write_scope),
            "automatic_merge": False,
            "write_authority_present": False,
            "registration_authority_effect": False,
            "routing_authority_effect": False,
            "next_action": "REQUIRE_WRITE_AUTHORITY" if mutating else "START_TASKRUN",
        }

    def as_route(self, *, registry_workspace: Path) -> dict[str, Any]:
        return {
            "schema": STARTER_RUNTIME_ROUTE_SCHEMA,
            "status": "PASS" if self.activation.ready else "BLOCKED_CONFIGURATION",
            "entrypoint": self.entrypoint,
            "selected_workflow": self.workflow.workflow_id,
            "user_payload": self.user_payload,
            "registration_ref": self.registration.registration_ref,
            "activation": self.activation.as_dict(),
            "effect_preview": self.effect_preview(registry_workspace=registry_workspace),
            "policy": {
                "exact_selection_required": True,
                "fuzzy_fallback_allowed": False,
                "route_executes_workflow": False,
                "route_grants_write_authority": False,
                "route_completes_taskrun": False,
                "completion_authority": "TaskRun",
                "authority_effect": False,
            },
        }


def resolve_starter_entrypoint(
    loaded: LoadedStarterRegistration,
    *,
    registry_workspace: Path,
    entrypoint: str | None = None,
    command: str | None = None,
    user_payload: str = "",
) -> ResolvedStarterEntrypoint:
    if bool(entrypoint) == bool(command):
        raise StarterRuntimeError("choose exactly one explicit entrypoint or strict /harness command")
    if command:
        parsed = parse_starter_command(command)
        selected = parsed["entrypoint"]
        payload = parsed["payload"]
    else:
        selected = str(entrypoint or "").strip()
        payload = str(user_payload or "")
        if not _ENTRYPOINT.fullmatch(selected):
            raise StarterRuntimeError("entrypoint must be a stable exact identifier")
    seal = loaded.payload["entrypoints"].get(selected)
    if not isinstance(seal, dict):
        raise StarterRuntimeError(f"unknown Starter entrypoint: {selected!r}")
    workflow = loaded.workflows[seal["workflow_id"]]
    project = loaded.verification.project
    activation = activate_workflow_spec(
        registry_workspace,
        workflow=workflow,
        available_provider_ids=set(project.providers.values()),
        provider_preferences=project.providers,
    )
    return ResolvedStarterEntrypoint(
        registration=loaded,
        entrypoint=selected,
        workflow=workflow,
        activation=activation,
        user_payload=payload,
    )


@dataclass(frozen=True)
class HostStarterSelectionResolution:
    """Validated Host selection; `resolved` is absent until mutation is confirmed."""

    resolved: ResolvedStarterEntrypoint | None
    record: dict[str, Any]


def _selection_fields(
    payload: Mapping[str, Any], *, required: set[str]
) -> None:
    missing = sorted(required - set(payload))
    unexpected = sorted(set(payload) - required)
    if missing or unexpected:
        raise StarterRuntimeError(
            f"invalid Host selection fields: missing={missing} unexpected={unexpected}"
        )


def build_starter_host_selection_request(
    loaded: LoadedStarterRegistration,
    *,
    registry_workspace: Path,
    host_id: str,
    user_request: str,
) -> dict[str, Any]:
    """Expose only verified candidates for ChatGPT/Codex semantic selection.

    Repository code does not interpret natural language.  The Host chooses one
    candidate, after which `resolve_starter_host_selection` validates exact
    identity and requires explicit confirmation for a mutating effect preview.
    """

    host = str(host_id or "").strip().lower()
    if host not in {"chatgpt", "codex"}:
        raise StarterRuntimeError(f"unsupported Starter Host: {host!r}")
    text = str(user_request or "").strip()
    if not text:
        raise StarterRuntimeError("Host selection requires a non-empty user_request")
    candidates: list[dict[str, Any]] = []
    for entrypoint in sorted(loaded.payload["entrypoints"]):
        resolved = resolve_starter_entrypoint(
            loaded,
            registry_workspace=registry_workspace,
            entrypoint=entrypoint,
            user_payload=text,
        )
        candidates.append(
            {
                "entrypoint": entrypoint,
                "workflow_id": resolved.workflow.workflow_id,
                "mode": resolved.workflow.mode,
                "effect_preview": resolved.effect_preview(
                    registry_workspace=registry_workspace
                ),
            }
        )
    request: dict[str, Any] = {
        "schema": STARTER_HOST_SELECTION_REQUEST_SCHEMA,
        "host_id": host,
        "registration_ref": loaded.registration_ref,
        "registration_sha256": loaded.payload["registration_sha256"],
        "user_request": text,
        "candidates": candidates,
        "policy": {
            "host_interprets_language": True,
            "repository_keyword_router": False,
            "exact_entrypoint_required": True,
            "mutating_confirmation_required": True,
            "selection_grants_write_authority": False,
            "automatic_merge": False,
            "completion_authority": "TaskRun",
            "authority_effect": False,
        },
    }
    request["request_fingerprint_sha256"] = _digest(request)
    return request


def resolve_starter_host_selection(
    loaded: LoadedStarterRegistration,
    *,
    registry_workspace: Path,
    request: Mapping[str, Any],
    selection: Mapping[str, Any],
    confirmation: Mapping[str, Any] | None = None,
) -> HostStarterSelectionResolution:
    """Reduce one Host model choice to an exact safe Starter route."""

    expected = build_starter_host_selection_request(
        loaded,
        registry_workspace=registry_workspace,
        host_id=str(request.get("host_id") or ""),
        user_request=str(request.get("user_request") or ""),
    )
    if dict(request) != expected:
        raise StarterRuntimeError("Host selection request is stale or was modified")
    _selection_fields(
        selection,
        required={
            "schema", "host_id", "request_fingerprint_sha256",
            "selected_entrypoint", "authority_effect",
        },
    )
    if selection.get("schema") != STARTER_HOST_SELECTION_SCHEMA:
        raise StarterRuntimeError("unsupported Host selection schema")
    if selection.get("host_id") != request.get("host_id"):
        raise StarterRuntimeError("Host selection host_id mismatch")
    if selection.get("request_fingerprint_sha256") != request.get(
        "request_fingerprint_sha256"
    ):
        raise StarterRuntimeError("Host selection request fingerprint mismatch")
    if selection.get("authority_effect") is not False:
        raise StarterRuntimeError("Host selection cannot change authority")
    entrypoint = str(selection.get("selected_entrypoint") or "").strip()
    candidate_names = {
        str(row.get("entrypoint") or "")
        for row in request["candidates"]
        if isinstance(row, Mapping)
    }
    if entrypoint not in candidate_names:
        raise StarterRuntimeError("Host selected an entrypoint outside the verified candidates")
    resolved = resolve_starter_entrypoint(
        loaded,
        registry_workspace=registry_workspace,
        entrypoint=entrypoint,
        user_payload=str(request["user_request"]),
    )
    preview = resolved.effect_preview(registry_workspace=registry_workspace)
    preview_sha = _digest(preview)
    mutating = bool(preview["mutating_capabilities"])

    if confirmation is not None:
        _selection_fields(
            confirmation,
            required={
                "schema", "request_fingerprint_sha256", "selected_entrypoint",
                "effect_preview_sha256", "confirmed", "authority_effect",
            },
        )
        if confirmation.get("schema") != STARTER_HOST_CONFIRMATION_SCHEMA:
            raise StarterRuntimeError("unsupported Host selection confirmation schema")
        if (
            confirmation.get("request_fingerprint_sha256")
            != request.get("request_fingerprint_sha256")
            or confirmation.get("selected_entrypoint") != entrypoint
            or confirmation.get("effect_preview_sha256") != preview_sha
            or confirmation.get("confirmed") is not True
            or confirmation.get("authority_effect") is not False
        ):
            raise StarterRuntimeError("Host selection confirmation does not bind the exact preview")

    awaiting = mutating and confirmation is None
    record = {
        "schema": STARTER_HOST_SELECTION_RESOLUTION_SCHEMA,
        "status": "AWAITING_CONFIRMATION" if awaiting else "PASS",
        "host_id": request["host_id"],
        "request_fingerprint_sha256": request["request_fingerprint_sha256"],
        "selected_entrypoint": entrypoint,
        "selected_workflow": resolved.workflow.workflow_id,
        "effect_preview": preview,
        "effect_preview_sha256": preview_sha,
        "confirmation_required": mutating,
        "confirmed": bool(confirmation is not None),
        "next_action": "CONFIRM_EXACT_EFFECT_PREVIEW" if awaiting else "START_TASKRUN",
        "policy": {
            "selection_is_execution": False,
            "selection_grants_write_authority": False,
            "automatic_merge": False,
            "completion_authority": "TaskRun",
            "authority_effect": False,
        },
    }
    return HostStarterSelectionResolution(
        resolved=None if awaiting else resolved,
        record=record,
    )


def _skill_bindings(
    resolved: ResolvedStarterEntrypoint,
) -> dict[str, tuple[CapabilityBinding, ...]]:
    bindings = {
        binding.capability_id: binding
        for binding in (
            *resolved.activation.capability_preflight.required_bindings,
            *resolved.activation.capability_preflight.optional_bindings,
        )
    }
    result: dict[str, tuple[CapabilityBinding, ...]] = {}
    manifest = resolved.registration.verification.starter
    for relative in manifest.skill_contracts:
        contract = parse_skill_contract(load_declaration(resolved.registration.starter_root / relative))
        if contract.skill not in resolved.workflow.skills:
            continue
        missing = sorted(set(contract.capabilities) - set(bindings))
        if missing:
            raise StarterRuntimeError(
                f"Skill {contract.skill!r} has unbound required capabilities: {missing}"
            )
        result[contract.skill] = tuple(bindings[capability] for capability in contract.capabilities)
    missing_contracts = sorted(set(resolved.workflow.skills) - set(result))
    if missing_contracts:
        raise StarterRuntimeError(
            f"Workflow has no package Skill contract binding: {missing_contracts}"
        )
    return result


class StarterWorkflowRuntime:
    """Execute one verified Starter entrypoint through the canonical runtime stack."""

    def __init__(
        self,
        *,
        registry_workspace: Path,
        resolved: ResolvedStarterEntrypoint,
        skill_host: SkillHostAdapter,
        provider_adapters: ProviderAdapterRegistry,
        checkpointer: Any,
        taskrun_store: TaskRunStore,
        workspace_fingerprint: str | None,
        write_authority_guard: WriteAuthorityGuard | None = None,
        human_gate_adapter: Any | None = None,
    ) -> None:
        if not resolved.activation.ready:
            raise StarterRuntimeError("Starter Workflow activation is blocked")
        if not is_durable_checkpointer(checkpointer):
            raise StarterRuntimeError("Starter runtime requires a durable LangGraph checkpointer")
        if str(taskrun_store.payload.get("status") or "") in TERMINAL_STATUSES:
            raise StarterRuntimeError("terminal TaskRun cannot start a Starter Workflow")
        self.registry_workspace = Path(registry_workspace).resolve()
        self.resolved = resolved
        self.store = taskrun_store
        self.checkpointer = checkpointer
        self.workspace_fingerprint = workspace_fingerprint
        if workspace_fingerprint:
            self.store.assert_resume_fingerprint(workspace_fingerprint)
        skill_adapter = CanonicalSkillInvocationAdapter(
            workspace=resolved.registration.project_workspace,
            request_class=resolved.workflow.request_class,
            host=skill_host,
            canonical_skill_paths=resolved.registration.skill_paths,
            skill_capability_bindings=_skill_bindings(resolved),
            write_authority_guard=write_authority_guard,
        )
        self.dispatcher = WorkflowAdapterDispatcher(
            skill_adapter=skill_adapter,
            provider_adapters=provider_adapters,
            write_authority_guard=write_authority_guard,
            human_gate_adapter=human_gate_adapter,
        )

    def _assert_task(self, state: Mapping[str, Any] | None = None) -> None:
        task_id = str(self.store.payload.get("task_id") or "")
        if not task_id:
            raise StarterRuntimeError("TaskRun requires task_id")
        if state is not None:
            if str(state.get("task_id") or "") != task_id:
                raise StarterRuntimeError("Workflow state task_id does not match TaskRun")
            if str(state.get("workflow_id") or "") != self.resolved.workflow.workflow_id:
                raise StarterRuntimeError("Workflow state workflow_id does not match resolved entrypoint")

    def _graph(self):
        return build_langgraph_workflow(
            workflow=self.resolved.workflow,
            activation=self.resolved.activation,
            dispatcher=self.dispatcher,
            checkpointer=self.checkpointer,
        )

    def _config(self) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": (
                    f"starter:{self.store.payload['task_id']}:"
                    f"{self.resolved.workflow.workflow_id}"
                )
            },
            "recursion_limit": 80,
        }

    def _execution(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema": STARTER_RUNTIME_EXECUTION_SCHEMA,
            "workflow_id": self.resolved.workflow.workflow_id,
            "entrypoint": self.resolved.entrypoint,
            "runtime_state": dict(state),
            "taskrun_status": str(self.store.payload.get("status") or ""),
            "taskrun_phase": str(self.store.payload.get("phase") or ""),
            "policy": {
                "graph_end_completes_taskrun": False,
                "completion_authority": "TaskRun",
                "automatic_merge": False,
                "authority_effect": False,
            },
        }

    @staticmethod
    def _taskrun_projection(runtime_status: str) -> tuple[str, str] | None:
        return {
            RUNTIME_STATUS_WAITING_EXTERNAL: (
                "WAITING_EXTERNAL_RESULT",
                "WORKFLOW_WAITING_EXTERNAL",
            ),
            RUNTIME_STATUS_WAITING_HOST: (
                "WAITING_EXTERNAL_RESULT",
                "WORKFLOW_WAITING_HOST",
            ),
            RUNTIME_STATUS_HUMAN_GATE: ("BLOCKED", "WORKFLOW_HUMAN_GATE"),
            RUNTIME_STATUS_BLOCKED: ("BLOCKED", "WORKFLOW_BLOCKED_UNRECOVERABLE"),
            RUNTIME_STATUS_END: (
                "VALIDATING",
                "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY",
            ),
        }.get(runtime_status)

    def recover(
        self,
        *,
        prior_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Adopt one durable, non-running graph snapshot after a Host crash.

        Recovery never invokes the graph.  A snapshot equal to the pre-resume
        state while TaskRun already says RUNTIME_RESUMED is ambiguous: the
        process may have died while a step effect was in flight.  That case is
        deliberately blocked instead of being replayed.
        """

        snapshot = self._graph().get_state(self._config())
        values = getattr(snapshot, "values", None)
        if not isinstance(values, Mapping) or not values:
            raise StarterRuntimeError("no durable LangGraph snapshot is available")
        state = dict(values)
        self._assert_task(state)
        runtime_status = str(state.get("runtime_status") or "").strip()
        projection = self._taskrun_projection(runtime_status)
        if projection is None:
            raise StarterRuntimeError(
                "durable LangGraph snapshot is still RUNNING or has no recoverable terminal state"
            )
        if (
            prior_state is not None
            and dict(prior_state) == state
            and str(self.store.payload.get("phase") or "")
            in {"WORKFLOW_RUNTIME_STARTED", "WORKFLOW_RUNTIME_RESUMED"}
        ):
            raise StarterRuntimeError(
                "durable LangGraph snapshot did not advance after a claimed transition"
            )
        current = (
            str(self.store.payload.get("status") or ""),
            str(self.store.payload.get("phase") or ""),
        )
        if current != projection:
            checkpoint_workflow_state(
                self.store,
                state=state,
                workspace_fingerprint=self.workspace_fingerprint,
            )
        return self._execution(state)

    def start(self, *, target_ref: Mapping[str, Any]) -> dict[str, Any]:
        self._assert_task()
        taskrun_status = str(self.store.payload.get("status") or "")
        if taskrun_status not in {"CREATED", "PLANNED"}:
            raise StarterRuntimeError(
                f"Starter Workflow start requires TaskRun CREATED or PLANNED, got {taskrun_status}"
            )
        if not target_ref:
            raise StarterRuntimeError("Starter Workflow start requires target_ref")
        checkpoint_workflow_start(
            self.store,
            workflow_id=self.resolved.workflow.workflow_id,
            workspace_fingerprint=self.workspace_fingerprint,
            evidence_refs=(self.resolved.registration.registration_ref,),
        )
        state = initial_workflow_state(
            workflow_id=self.resolved.workflow.workflow_id,
            task_id=str(self.store.payload["task_id"]),
            target_ref={**dict(target_ref), "user_payload": self.resolved.user_payload},
        )
        result = self._graph().invoke(state, config=self._config())
        checkpoint_workflow_state(
            self.store,
            state=result,
            workspace_fingerprint=self.workspace_fingerprint,
        )
        return self._execution(result)

    def resume(
        self,
        *,
        state: Mapping[str, Any],
        external_event: Mapping[str, Any] | None = None,
        human_decision: Mapping[str, Any] | None = None,
        host_execution_result: Mapping[str, Any] | None = None,
        evidence_refs: Iterable[str],
        correlation_ref: str | None = None,
    ) -> dict[str, Any]:
        self._assert_task(state)
        status = str(state.get("runtime_status") or "")
        supplied = sum(
            value is not None
            for value in (external_event, human_decision, host_execution_result)
        )
        if supplied != 1:
            raise StarterRuntimeError("Workflow resume requires exactly one resume input")
        resume_correlation = correlation_ref
        if status == RUNTIME_STATUS_WAITING_EXTERNAL:
            resume_kind = "EXTERNAL_EVENT"
            wait = state.get("external_wait")
            if not isinstance(wait, Mapping):
                raise StarterRuntimeError("WAITING_EXTERNAL state requires an external_wait handle")
            expected_correlation = str(wait.get("correlation_ref") or "").strip()
            expected_event = str(wait.get("resume_event") or "").strip()
            actual_correlation = str(correlation_ref or "").strip()
            event_correlation = str(
                (external_event or {}).get("correlation_ref") or ""
            ).strip()
            actual_event = str((external_event or {}).get("event") or "").strip()
            if not expected_correlation or not expected_event:
                raise StarterRuntimeError(
                    "external_wait handle requires correlation_ref and resume_event"
                )
            if (
                actual_correlation != expected_correlation
                or event_correlation != expected_correlation
                or actual_event != expected_event
            ):
                raise StarterRuntimeError(
                    "external resume event or correlation does not match the durable wait handle"
                )
        elif status == RUNTIME_STATUS_WAITING_HOST:
            wait = state.get("host_wait")
            if not isinstance(wait, Mapping) or not wait:
                raise StarterRuntimeError("WAITING_HOST state requires a host_wait handle")
            pointer = host_execution_result or {}
            if (
                pointer.get("schema") != "host-skill-execution-resume@1"
                or pointer.get("event") != wait.get("resume_event")
                or pointer.get("execution_id") != wait.get("execution_id")
                or pointer.get("authority_effect") is not False
                or not str(pointer.get("result_ref") or "").strip()
                or not _SHA256.fullmatch(str(pointer.get("result_sha256") or ""))
            ):
                raise StarterRuntimeError(
                    "Host execution result does not match the durable host_wait handle"
                )
            resume_kind = "HOST_EXECUTION"
            resume_correlation = str(wait["execution_id"])
        elif status == RUNTIME_STATUS_HUMAN_GATE:
            if not human_decision:
                raise StarterRuntimeError("HUMAN_GATE resume requires human_decision")
            resume_kind = "HUMAN_DECISION"
        else:
            raise StarterRuntimeError(
                "Starter Workflow can resume only from WAITING_EXTERNAL, WAITING_HOST, or HUMAN_GATE"
            )
        refs = tuple(str(ref).strip() for ref in evidence_refs if str(ref).strip())
        checkpoints = self.store.payload.get("checkpoints") or []
        latest = checkpoints[-1] if checkpoints else {}
        metadata = latest.get("metadata") if isinstance(latest, Mapping) else {}
        if (
            not isinstance(metadata, Mapping)
            or str(metadata.get("workflow_id") or "") != self.resolved.workflow.workflow_id
        ):
            raise StarterRuntimeError(
                "TaskRun wait/gate checkpoint does not belong to the resolved Workflow"
            )
        checkpoint_workflow_resume(
            self.store,
            workflow_id=self.resolved.workflow.workflow_id,
            resume_kind=resume_kind,
            workspace_fingerprint=self.workspace_fingerprint,
            evidence_refs=refs,
            correlation_ref=resume_correlation,
        )
        resumed: WorkflowRuntimeState = resume_workflow_state(
            state,
            external_event=external_event,
            human_decision=human_decision,
            host_execution_result=host_execution_result,
        )
        result = self._graph().invoke(resumed, config=self._config())
        checkpoint_workflow_state(
            self.store,
            state=result,
            workspace_fingerprint=self.workspace_fingerprint,
        )
        return self._execution(result)


__all__ = [
    "LoadedStarterRegistration",
    "HostStarterSelectionResolution",
    "STARTER_HOST_CONFIRMATION_SCHEMA",
    "STARTER_HOST_SELECTION_REQUEST_SCHEMA",
    "STARTER_HOST_SELECTION_RESOLUTION_SCHEMA",
    "STARTER_HOST_SELECTION_SCHEMA",
    "ResolvedStarterEntrypoint",
    "STARTER_RUNTIME_EXECUTION_SCHEMA",
    "STARTER_RUNTIME_REGISTRATION_SCHEMA",
    "STARTER_RUNTIME_ROUTE_SCHEMA",
    "StarterRuntimeError",
    "StarterWorkflowRuntime",
    "load_starter_registration",
    "build_starter_host_selection_request",
    "parse_starter_command",
    "register_starter_runtime",
    "resolve_starter_entrypoint",
    "resolve_starter_host_selection",
]
