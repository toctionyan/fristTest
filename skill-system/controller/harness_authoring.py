from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from workflow_registry import WorkflowRegistryError, WorkflowSpec, parse_workflow_spec

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - exercised by environments without PyYAML
    yaml = None


PROJECT_SCHEMA = "harness-project@1"
SKILL_CONTRACT_SCHEMA = "harness-skill-contract@1"
WORKFLOW_SCHEMA = "harness-workflow@1"
COMPILED_PLAN_SCHEMA = "compiled-workflow-plan@1"

_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
_PROJECT_TYPES = frozenset({"agent", "api", "cli", "library", "service", "web", "other"})
_SKILL_MODES = frozenset({"read_only", "mutating"})
_EXTENSION_TYPES = frozenset(
    {"procedure", "audit-lens", "gate", "finding-enricher", "context-provider"}
)
_WORKFLOW_MODES = frozenset({"READ_ONLY", "WRITE"})
_DEFAULT_WORKFLOWS = frozenset(
    {"audit_workflow", "repair_workflow", "full_dev_workflow"}
)


class HarnessAuthoringError(ValueError):
    """Raised when a portable Harness declaration is unsafe or invalid."""


@dataclass(frozen=True)
class ProjectDeclaration:
    project_id: str
    project_type: str
    commands: dict[str, str]
    write_scope: tuple[str, ...]
    providers: dict[str, str]
    defaults: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": PROJECT_SCHEMA,
            "project_id": self.project_id,
            "project_type": self.project_type,
            "commands": dict(self.commands),
            "write_scope": list(self.write_scope),
            "providers": dict(self.providers),
            "defaults": dict(self.defaults),
        }


@dataclass(frozen=True)
class SkillContractDeclaration:
    skill: str
    version: str
    mode: str
    inputs: tuple[str, ...]
    capabilities: tuple[str, ...]
    outputs: tuple[str, ...]
    extension_type: str
    extension_points: dict[str, tuple[str, ...]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SKILL_CONTRACT_SCHEMA,
            "skill": self.skill,
            "version": self.version,
            "mode": self.mode,
            "inputs": list(self.inputs),
            "capabilities": list(self.capabilities),
            "outputs": list(self.outputs),
            "extension_type": self.extension_type,
            "extension_points": {
                name: list(types) for name, types in self.extension_points.items()
            },
        }


@dataclass(frozen=True)
class CompiledWorkflowPlan:
    workflow_version: str
    source_sha256: str
    spec: WorkflowSpec
    completion_policy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": COMPILED_PLAN_SCHEMA,
            "workflow_version": self.workflow_version,
            "source_sha256": self.source_sha256,
            "runtime": self.spec.as_dict(),
            "completion": {
                "transition_to": "VALIDATING",
                "policy": self.completion_policy,
                "authority": "TaskRun",
            },
        }


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessAuthoringError(f"{field} must be an object")
    non_string_keys = [key for key in value if not isinstance(key, str)]
    if non_string_keys:
        raise HarnessAuthoringError(f"{field} keys must be strings")
    return dict(value)


def _closed(row: Mapping[str, Any], allowed: set[str], *, field: str) -> None:
    unexpected = sorted(set(row) - allowed)
    if unexpected:
        raise HarnessAuthoringError(f"{field} contains unsupported keys: {unexpected}")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessAuthoringError(f"{field} must be a non-empty string")
    return value.strip()


def _stable_id(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    if not _STABLE_ID.fullmatch(text):
        raise HarnessAuthoringError(f"{field} must be a stable identifier")
    return text


def _bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise HarnessAuthoringError(f"{field} must be boolean")
    return value


def _string_list(value: object, *, field: str, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise HarnessAuthoringError(f"{field} must be an array")
    values = tuple(_required_text(item, field=f"{field} item") for item in value)
    if not allow_empty and not values:
        raise HarnessAuthoringError(f"{field} cannot be empty")
    if len(set(values)) != len(values):
        raise HarnessAuthoringError(f"{field} must contain unique values")
    return values


def _string_map(value: object, *, field: str) -> dict[str, str]:
    row = _object(value, field=field)
    result: dict[str, str] = {}
    for raw_key, raw_value in row.items():
        key = _stable_id(raw_key, field=f"{field} key")
        result[key] = _required_text(raw_value, field=f"{field}.{key}")
    return result


def _source_digest(row: Mapping[str, Any]) -> str:
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _workflow_wire_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Type-check the open wire format before canonical semantic parsing.

    This checks only closed-object and JSON/YAML scalar shape. Runtime topology,
    provider neutrality, reachability, and binding semantics stay owned by the
    existing Workflow parser and graph contract.
    """

    request_class = _required_text(row.get("request_class"), field="request_class")
    skills = list(_string_list(row.get("skills"), field="skills"))

    requirements = _object(row.get("requirements", {}), field="requirements")
    _closed(requirements, {"capabilities"}, field="requirements")
    capabilities = _object(requirements.get("capabilities", {}), field="requirements.capabilities")
    _closed(
        capabilities,
        {"required", "optional"},
        field="requirements.capabilities",
    )
    required_capabilities = list(
        _string_list(capabilities.get("required", []), field="requirements.capabilities.required")
    )
    optional_capabilities = list(
        _string_list(capabilities.get("optional", []), field="requirements.capabilities.optional")
    )

    graph = _object(row.get("graph"), field="graph")
    _closed(graph, {"start", "steps", "max_attempts_per_step"}, field="graph")
    _required_text(graph.get("start"), field="graph.start")
    max_attempts = graph.get("max_attempts_per_step")
    if max_attempts is not None and (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or not 1 <= max_attempts <= 64
    ):
        raise HarnessAuthoringError("graph.max_attempts_per_step must be an integer between 1 and 64")
    raw_steps = _object(graph.get("steps"), field="graph.steps")
    if not raw_steps:
        raise HarnessAuthoringError("graph.steps cannot be empty")
    for step_id, raw_step in raw_steps.items():
        _stable_id(step_id, field="graph step_id")
        step = _object(raw_step, field=f"graph.steps.{step_id}")
        _closed(step, {"type", "use", "routes", "max_attempts"}, field=f"graph.steps.{step_id}")
        _required_text(step.get("type"), field=f"graph.steps.{step_id}.type")
        if "use" in step:
            _required_text(step["use"], field=f"graph.steps.{step_id}.use")
        routes = _object(step.get("routes"), field=f"graph.steps.{step_id}.routes")
        if not routes:
            raise HarnessAuthoringError(f"graph.steps.{step_id}.routes cannot be empty")
        for outcome, target in routes.items():
            _required_text(outcome, field=f"graph.steps.{step_id} route outcome")
            _required_text(target, field=f"graph.steps.{step_id} route target")
        step_attempts = step.get("max_attempts")
        if step_attempts is not None and (
            not isinstance(step_attempts, int)
            or isinstance(step_attempts, bool)
            or not 1 <= step_attempts <= 64
        ):
            raise HarnessAuthoringError(
                f"graph.steps.{step_id}.max_attempts must be an integer between 1 and 64"
            )

    return {
        "request_class": request_class,
        "skills": skills,
        "requirements": {
            "capabilities": {
                "required": required_capabilities,
                "optional": optional_capabilities,
            }
        },
        "graph": graph,
    }


def load_declaration(path: Path) -> dict[str, Any]:
    """Load JSON or safe YAML without constructors, imports, or evaluation."""

    source = Path(path)
    if not source.is_file():
        raise HarnessAuthoringError(f"declaration is missing: {source}")
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise HarnessAuthoringError(f"cannot read declaration {source}: {exc}") from exc
    suffix = source.suffix.casefold()
    try:
        if suffix == ".json":
            payload = json.loads(text)
        elif suffix in {".yaml", ".yml"}:
            if yaml is None:
                raise HarnessAuthoringError(
                    "YAML support requires PyYAML; use a .json declaration or install PyYAML"
                )
            payload = yaml.safe_load(text)
        else:
            raise HarnessAuthoringError("declaration extension must be .json, .yaml, or .yml")
    except HarnessAuthoringError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise HarnessAuthoringError(f"declaration {source} is invalid: {exc}") from exc
    except Exception as exc:
        if yaml is not None and isinstance(exc, yaml.YAMLError):
            raise HarnessAuthoringError(f"declaration {source} is invalid: {exc}") from exc
        raise
    return _object(payload, field=f"declaration {source}")


def parse_project_declaration(raw: Mapping[str, Any]) -> ProjectDeclaration:
    row = _object(raw, field="Project declaration")
    _closed(
        row,
        {"schema", "project_id", "project_type", "commands", "write_scope", "providers", "defaults"},
        field="Project declaration",
    )
    if row.get("schema") != PROJECT_SCHEMA:
        raise HarnessAuthoringError(f"Project schema must be {PROJECT_SCHEMA!r}")
    project_id = _stable_id(row.get("project_id"), field="project_id")
    project_type = _stable_id(row.get("project_type"), field="project_type")
    if project_type not in _PROJECT_TYPES:
        raise HarnessAuthoringError(f"project_type must be one of {sorted(_PROJECT_TYPES)}")
    commands = _string_map(row.get("commands"), field="commands")
    if not commands:
        raise HarnessAuthoringError("commands cannot be empty")
    write_scope = _string_list(row.get("write_scope"), field="write_scope", allow_empty=False)
    for scope in write_scope:
        normalized = scope.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if (
            normalized != scope
            or normalized.startswith("/")
            or scope in {".", "*", "**"}
            or ".." in parts
            or not parts
        ):
            raise HarnessAuthoringError(
                f"write_scope item {scope!r} must be a bounded relative path or glob"
            )
    providers = _string_map(row.get("providers", {}), field="providers")
    defaults = _string_map(row.get("defaults", {}), field="defaults")
    unexpected_defaults = sorted(set(defaults) - _DEFAULT_WORKFLOWS)
    if unexpected_defaults:
        raise HarnessAuthoringError(f"defaults contains unsupported keys: {unexpected_defaults}")
    return ProjectDeclaration(
        project_id=project_id,
        project_type=project_type,
        commands=commands,
        write_scope=write_scope,
        providers=providers,
        defaults=defaults,
    )


def parse_skill_contract(raw: Mapping[str, Any]) -> SkillContractDeclaration:
    row = _object(raw, field="Skill contract")
    _closed(
        row,
        {
            "schema", "skill", "version", "mode", "inputs", "capabilities",
            "outputs", "extension_type", "extension_points",
        },
        field="Skill contract",
    )
    if row.get("schema") != SKILL_CONTRACT_SCHEMA:
        raise HarnessAuthoringError(f"Skill contract schema must be {SKILL_CONTRACT_SCHEMA!r}")
    skill = _stable_id(row.get("skill"), field="skill")
    version = _required_text(row.get("version"), field="version")
    mode = _required_text(row.get("mode"), field="mode")
    if mode not in _SKILL_MODES:
        raise HarnessAuthoringError(f"Skill mode must be one of {sorted(_SKILL_MODES)}")
    inputs = _string_list(row.get("inputs", []), field="inputs")
    capabilities = _string_list(row.get("capabilities", []), field="capabilities")
    outputs = _string_list(row.get("outputs", []), field="outputs", allow_empty=False)
    if mode == "mutating" and "workspace.write" not in capabilities:
        raise HarnessAuthoringError("mutating Skill contracts require workspace.write capability")
    extension_type = _required_text(row.get("extension_type", "procedure"), field="extension_type")
    if extension_type not in _EXTENSION_TYPES:
        raise HarnessAuthoringError(f"extension_type must be one of {sorted(_EXTENSION_TYPES)}")
    raw_points = _object(row.get("extension_points", {}), field="extension_points")
    extension_points: dict[str, tuple[str, ...]] = {}
    for raw_name, raw_types in raw_points.items():
        name = _stable_id(raw_name, field="extension point")
        types = _string_list(raw_types, field=f"extension_points.{name}", allow_empty=False)
        unsupported = sorted(set(types) - _EXTENSION_TYPES)
        if unsupported:
            raise HarnessAuthoringError(
                f"extension_points.{name} contains unsupported extension types: {unsupported}"
            )
        extension_points[name] = types
    return SkillContractDeclaration(
        skill=skill,
        version=version,
        mode=mode,
        inputs=inputs,
        capabilities=capabilities,
        outputs=outputs,
        extension_type=extension_type,
        extension_points=extension_points,
    )


def compile_workflow_declaration(raw: Mapping[str, Any]) -> CompiledWorkflowPlan:
    row = _object(raw, field="Workflow declaration")
    _closed(
        row,
        {
            "schema", "id", "version", "request_class", "skills", "mode",
            "status_first", "deterministic_response", "write_governed",
            "requirements", "graph", "completion",
        },
        field="Workflow declaration",
    )
    if row.get("schema") != WORKFLOW_SCHEMA:
        raise HarnessAuthoringError(f"Workflow schema must be {WORKFLOW_SCHEMA!r}")
    workflow_id = _stable_id(row.get("id"), field="Workflow id")
    version = _required_text(row.get("version"), field="Workflow version")
    mode = _required_text(row.get("mode"), field="Workflow mode")
    if mode not in _WORKFLOW_MODES:
        raise HarnessAuthoringError(f"Workflow mode must be one of {sorted(_WORKFLOW_MODES)}")
    completion = _object(row.get("completion"), field="Workflow completion")
    _closed(completion, {"transition_to", "policy", "authority"}, field="Workflow completion")
    if completion.get("transition_to") != "VALIDATING":
        raise HarnessAuthoringError("Workflow completion transition_to must be VALIDATING")
    if completion.get("authority") != "TaskRun":
        raise HarnessAuthoringError("Workflow completion authority must be TaskRun")
    policy = _stable_id(completion.get("policy"), field="Workflow completion policy")
    wire = _workflow_wire_row(row)
    runtime_row = {
        "workflow_id": workflow_id,
        "request_class": wire["request_class"],
        "skills": wire["skills"],
        "mode": mode,
        "status_first": _bool(row.get("status_first", False), field="status_first"),
        "deterministic_response": _bool(
            row.get("deterministic_response", False), field="deterministic_response"
        ),
        "write_governed": _bool(row.get("write_governed", False), field="write_governed"),
        "requirements": wire["requirements"],
        "graph": wire["graph"],
    }
    if mode == "WRITE" and runtime_row["write_governed"] is not True:
        raise HarnessAuthoringError("WRITE Workflows must set write_governed: true")
    try:
        spec = parse_workflow_spec(runtime_row)
    except WorkflowRegistryError as exc:
        raise HarnessAuthoringError(str(exc)) from exc
    return CompiledWorkflowPlan(
        workflow_version=version,
        source_sha256=_source_digest(row),
        spec=spec,
        completion_policy=policy,
    )


def validate_declaration(raw: Mapping[str, Any]) -> dict[str, Any]:
    schema = raw.get("schema") if isinstance(raw, Mapping) else None
    if schema == PROJECT_SCHEMA:
        return parse_project_declaration(raw).as_dict()
    if schema == SKILL_CONTRACT_SCHEMA:
        return parse_skill_contract(raw).as_dict()
    if schema == WORKFLOW_SCHEMA:
        return compile_workflow_declaration(raw).as_dict()
    raise HarnessAuthoringError(
        f"unsupported declaration schema {schema!r}; expected {PROJECT_SCHEMA}, "
        f"{SKILL_CONTRACT_SCHEMA}, or {WORKFLOW_SCHEMA}"
    )


def explain_workflow(plan: CompiledWorkflowPlan) -> dict[str, Any]:
    graph = plan.spec.graph
    steps: list[dict[str, Any]] = []
    if graph is not None:
        for step_id, step in graph.steps.items():
            steps.append(
                {
                    "step_id": step_id,
                    "type": step.step_type,
                    "use": step.use,
                    "routes": dict(step.routes),
                    "max_attempts": step.max_attempts,
                }
            )
    return {
        "schema": "workflow-explanation@1",
        "workflow_id": plan.spec.workflow_id,
        "request_class": plan.spec.request_class,
        "mode": plan.spec.mode,
        "skills": list(plan.spec.skills),
        "required_capabilities": list(plan.spec.required_capabilities),
        "optional_capabilities": list(plan.spec.optional_capabilities),
        "start": graph.start if graph is not None else None,
        "steps": steps,
        "completion": {
            "graph_end_means": "TaskRun VALIDATING",
            "policy": plan.completion_policy,
            "authority": "TaskRun",
        },
    }


def initialize_project(
    output: Path,
    *,
    project_id: str,
    project_type: str,
    commands: Mapping[str, str],
    write_scope: Sequence[str],
    providers: Mapping[str, str] | None = None,
    defaults: Mapping[str, str] | None = None,
    force: bool = False,
) -> ProjectDeclaration:
    declaration = parse_project_declaration(
        {
            "schema": PROJECT_SCHEMA,
            "project_id": project_id,
            "project_type": project_type,
            "commands": dict(commands),
            "write_scope": list(write_scope),
            "providers": dict(providers or {}),
            "defaults": dict(defaults or {}),
        }
    )
    target = Path(output)
    if target.exists() and not force:
        raise HarnessAuthoringError(f"refusing to overwrite existing declaration: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = declaration.as_dict()
    suffix = target.suffix.casefold()
    if suffix == ".json":
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    elif suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise HarnessAuthoringError(
                "YAML support requires PyYAML; choose a .json output or install PyYAML"
            )
        rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    else:
        raise HarnessAuthoringError("output extension must be .json, .yaml, or .yml")
    try:
        target.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise HarnessAuthoringError(f"cannot write declaration {target}: {exc}") from exc
    return declaration


__all__ = [
    "COMPILED_PLAN_SCHEMA",
    "PROJECT_SCHEMA",
    "SKILL_CONTRACT_SCHEMA",
    "WORKFLOW_SCHEMA",
    "CompiledWorkflowPlan",
    "HarnessAuthoringError",
    "ProjectDeclaration",
    "SkillContractDeclaration",
    "compile_workflow_declaration",
    "explain_workflow",
    "initialize_project",
    "load_declaration",
    "parse_project_declaration",
    "parse_skill_contract",
    "validate_declaration",
]
