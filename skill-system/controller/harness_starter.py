from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from capability_registry import load_provider_registry
from harness_authoring import (
    HarnessAuthoringError,
    ProjectDeclaration,
    compile_workflow_declaration,
    load_declaration,
    parse_project_declaration,
    parse_skill_contract,
)
from harness_composition import compose_workflow, parse_composition_declaration


STARTER_SCHEMA = "harness-starter@1"
STARTER_VERIFICATION_SCHEMA = "harness-starter-verification@1"
BUILTIN_STARTERS_ROOT = Path(__file__).resolve().parents[1] / "starters"
MANIFEST_NAME = "starter.json"

_ENTRYPOINTS = frozenset(
    {
        "overall_audit",
        "module_audit",
        "architecture_review",
        "repair_and_prove",
        "repair_with_ci",
        "full_dev",
    }
)
_DECLARATION_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
_FORBIDDEN_AUTOMATIC_CAPABILITIES = frozenset({"code_review.pull_request.merge"})
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")


class HarnessStarterError(HarnessAuthoringError):
    """Raised when a Starter package is incomplete, unsafe, or inconsistent."""


@dataclass(frozen=True)
class StarterManifest:
    starter_id: str
    version: str
    project: str
    skill_contracts: tuple[str, ...]
    workflows: tuple[str, ...]
    compositions: tuple[str, ...]
    entrypoints: dict[str, str]
    standalone_application: bool
    automatic_merge: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": STARTER_SCHEMA,
            "starter_id": self.starter_id,
            "version": self.version,
            "project": self.project,
            "skill_contracts": list(self.skill_contracts),
            "workflows": list(self.workflows),
            "compositions": list(self.compositions),
            "entrypoints": dict(self.entrypoints),
            "policies": {
                "standalone_application": self.standalone_application,
                "automatic_merge": self.automatic_merge,
            },
        }


@dataclass(frozen=True)
class StarterVerification:
    starter: StarterManifest
    package_sha256: str
    project: ProjectDeclaration
    skill_ids: tuple[str, ...]
    workflow_ids: tuple[str, ...]
    composed_workflow_ids: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    provider_bindings: dict[str, str]
    file_sha256: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": STARTER_VERIFICATION_SCHEMA,
            "status": "PASS",
            "starter": {
                "starter_id": self.starter.starter_id,
                "version": self.starter.version,
                "package_sha256": self.package_sha256,
            },
            "project": self.project.as_dict(),
            "inventory": {
                "skills": list(self.skill_ids),
                "workflows": list(self.workflow_ids),
                "composed_workflows": list(self.composed_workflow_ids),
                "entrypoints": dict(self.starter.entrypoints),
            },
            "required_capabilities": list(self.required_capabilities),
            "provider_bindings": dict(sorted(self.provider_bindings.items())),
            "files": dict(sorted(self.file_sha256.items())),
            "policy": {
                "standalone_application": self.starter.standalone_application,
                "automatic_merge": self.starter.automatic_merge,
                "verification_executes_workflow": False,
                "verification_activates_provider": False,
                "verification_grants_write_authority": False,
                "verification_changes_completion_authority": False,
            },
        }


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessStarterError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise HarnessStarterError(f"{field} keys must be strings")
    return dict(value)


def _closed(row: Mapping[str, Any], allowed: set[str], *, field: str) -> None:
    unexpected = sorted(set(row) - allowed)
    if unexpected:
        raise HarnessStarterError(f"{field} contains unsupported keys: {unexpected}")


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessStarterError(f"{field} must be a non-empty string")
    text = value.strip()
    if not _STABLE_ID.fullmatch(text):
        raise HarnessStarterError(f"{field} must be a stable identifier")
    return text


def _relative_declaration(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessStarterError(f"{field} must be a relative declaration path")
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        raw != value.strip()
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or not path.parts
        or path.suffix.casefold() not in _DECLARATION_SUFFIXES
    ):
        raise HarnessStarterError(f"{field} must be a bounded relative JSON/YAML path")
    return path.as_posix()


def _path_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise HarnessStarterError(f"{field} must be a non-empty array")
    paths = tuple(
        _relative_declaration(item, field=f"{field} item") for item in value
    )
    if len(set(paths)) != len(paths):
        raise HarnessStarterError(f"{field} must contain unique paths")
    return paths


def parse_starter_manifest(raw: Mapping[str, Any]) -> StarterManifest:
    row = _object(raw, field="Starter manifest")
    _closed(
        row,
        {
            "schema", "starter_id", "version", "project", "skill_contracts",
            "workflows", "compositions", "entrypoints", "policies",
        },
        field="Starter manifest",
    )
    if row.get("schema") != STARTER_SCHEMA:
        raise HarnessStarterError(f"Starter schema must be {STARTER_SCHEMA!r}")
    starter_id = _text(row.get("starter_id"), field="starter_id")
    version = _text(row.get("version"), field="version")
    project = _relative_declaration(row.get("project"), field="project")
    skill_contracts = _path_list(row.get("skill_contracts"), field="skill_contracts")
    workflows = _path_list(row.get("workflows"), field="workflows")
    compositions = _path_list(row.get("compositions"), field="compositions")
    declared_paths = (project, *skill_contracts, *workflows, *compositions)
    if len(set(declared_paths)) != len(declared_paths):
        raise HarnessStarterError("Starter declaration paths must be unique across categories")

    raw_entrypoints = _object(row.get("entrypoints"), field="entrypoints")
    if set(raw_entrypoints) != _ENTRYPOINTS:
        missing = sorted(_ENTRYPOINTS - set(raw_entrypoints))
        unexpected = sorted(set(raw_entrypoints) - _ENTRYPOINTS)
        raise HarnessStarterError(
            f"entrypoints must define exactly {sorted(_ENTRYPOINTS)}; "
            f"missing={missing} unexpected={unexpected}"
        )
    entrypoints = {
        name: _text(raw_entrypoints[name], field=f"entrypoints.{name}")
        for name in sorted(raw_entrypoints)
    }
    policies = _object(row.get("policies"), field="policies")
    _closed(policies, {"standalone_application", "automatic_merge"}, field="policies")
    standalone_application = policies.get("standalone_application")
    automatic_merge = policies.get("automatic_merge")
    if not isinstance(standalone_application, bool) or not isinstance(automatic_merge, bool):
        raise HarnessStarterError("Starter policy fields must be boolean")
    if standalone_application is not True:
        raise HarnessStarterError("Starter must preserve standalone_application: true")
    return StarterManifest(
        starter_id=starter_id,
        version=version,
        project=project,
        skill_contracts=skill_contracts,
        workflows=workflows,
        compositions=compositions,
        entrypoints=entrypoints,
        standalone_application=standalone_application,
        automatic_merge=automatic_merge,
    )


def _manifest_path(directory: Path) -> Path:
    return directory.resolve() / MANIFEST_NAME


def _load_manifest(directory: Path) -> StarterManifest:
    path = _manifest_path(directory)
    if not path.is_file() or path.is_symlink():
        raise HarnessStarterError(f"Starter manifest is missing or unsafe: {path}")
    return parse_starter_manifest(load_declaration(path))


def _member(directory: Path, relative: str) -> Path:
    root = directory.resolve()
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise HarnessStarterError(f"Starter declaration is missing or unsafe: {relative}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HarnessStarterError(f"Starter declaration escapes package: {relative}") from exc
    current = path.parent
    while current != root:
        if current.is_symlink():
            raise HarnessStarterError(f"Starter path contains a symlink: {relative}")
        current = current.parent
    return resolved


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_digest(files: Mapping[str, str]) -> str:
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_unique(identity: str, seen: set[str], *, field: str) -> None:
    if identity in seen:
        raise HarnessStarterError(f"duplicate {field}: {identity}")
    seen.add(identity)


def _verify_declared_inventory(directory: Path, manifest: StarterManifest) -> dict[str, str]:
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise HarnessStarterError(
                f"Starter package cannot contain symlinks: {path.relative_to(directory)}"
            )
    declared = {
        MANIFEST_NAME,
        manifest.project,
        *manifest.skill_contracts,
        *manifest.workflows,
        *manifest.compositions,
    }
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in _DECLARATION_SUFFIXES
    }
    missing = sorted(declared - actual)
    undeclared = sorted(actual - declared)
    if missing or undeclared:
        raise HarnessStarterError(
            f"Starter declaration inventory mismatch: missing={missing} undeclared={undeclared}"
        )
    return {relative: _sha256(_member(directory, relative)) for relative in sorted(declared)}


def _validate_provider_coverage(
    project: ProjectDeclaration,
    required_capabilities: set[str],
    *,
    registry_workspace: Path,
) -> dict[str, str]:
    providers = load_provider_registry(registry_workspace.resolve())
    missing = sorted(required_capabilities - set(project.providers))
    if missing:
        raise HarnessStarterError(
            f"Project declaration has no Provider binding for required capabilities: {missing}"
        )
    for capability_id, provider_id in project.providers.items():
        provider = providers.get(provider_id)
        if provider is None:
            raise HarnessStarterError(
                f"Project declaration references unknown Provider {provider_id!r} for "
                f"{capability_id!r}"
            )
        if capability_id not in provider.capabilities:
            raise HarnessStarterError(
                f"Provider {provider_id!r} does not provide capability {capability_id!r}"
            )
    return {
        capability_id: project.providers[capability_id]
        for capability_id in sorted(required_capabilities)
    }


def verify_starter(directory: Path, *, registry_workspace: Path) -> StarterVerification:
    root = Path(directory).resolve()
    if not root.is_dir() or root.is_symlink():
        raise HarnessStarterError(f"Starter directory is missing or unsafe: {root}")
    manifest = _load_manifest(root)
    file_digests = _verify_declared_inventory(root, manifest)
    project = parse_project_declaration(load_declaration(_member(root, manifest.project)))

    skill_rows: list[dict[str, Any]] = []
    skill_ids: set[str] = set()
    for relative in manifest.skill_contracts:
        raw = load_declaration(_member(root, relative))
        contract = parse_skill_contract(raw)
        _require_unique(contract.skill, skill_ids, field="Skill identity")
        skill_rows.append(raw)

    workflow_rows: dict[str, dict[str, Any]] = {}
    compiled_workflows: dict[str, Any] = {}
    workflow_ids: set[str] = set()
    required_capabilities: set[str] = set()
    for relative in manifest.workflows:
        raw = load_declaration(_member(root, relative))
        compiled = compile_workflow_declaration(raw)
        workflow_id = compiled.spec.workflow_id
        _require_unique(workflow_id, workflow_ids, field="Workflow identity")
        workflow_rows[workflow_id] = raw
        compiled_workflows[workflow_id] = compiled
        required_capabilities.update(compiled.spec.required_capabilities)

    composed_ids: set[str] = set()
    for relative in manifest.compositions:
        raw = load_declaration(_member(root, relative))
        composition = parse_composition_declaration(raw)
        try:
            base = workflow_rows[composition.base_workflow]
        except KeyError as exc:
            raise HarnessStarterError(
                f"Composition {composition.composition_id!r} references a base Workflow "
                "outside the Starter"
            ) from exc
        plan = compose_workflow(base, raw, skill_rows)
        _require_unique(
            plan.composition.composition_id,
            composed_ids,
            field="composed Workflow identity",
        )
        if plan.composition.composition_id in workflow_rows:
            raise HarnessStarterError(
                f"composed Workflow collides with base Workflow: {plan.composition.composition_id}"
            )
        required_capabilities.update(
            plan.compiled_plan["runtime"]["requirements"]["capabilities"]["required"]
        )

    available_workflows = set(workflow_rows) | composed_ids
    unresolved_entrypoints = sorted(
        name
        for name, workflow_id in manifest.entrypoints.items()
        if workflow_id not in available_workflows
    )
    if unresolved_entrypoints:
        raise HarnessStarterError(
            f"Starter entrypoints reference unknown Workflows: {unresolved_entrypoints}"
        )
    unresolved_defaults = sorted(
        name
        for name, workflow_id in project.defaults.items()
        if workflow_id not in available_workflows
    )
    if unresolved_defaults:
        raise HarnessStarterError(
            f"Project defaults reference unknown Workflows: {unresolved_defaults}"
        )

    if manifest.automatic_merge is False:
        for workflow_id, compiled in compiled_workflows.items():
            forbidden = sorted(
                set(compiled.spec.required_capabilities) & _FORBIDDEN_AUTOMATIC_CAPABILITIES
            )
            graph_uses = {
                step.use
                for step in (compiled.spec.graph.steps.values() if compiled.spec.graph else ())
                if step.use is not None
            }
            forbidden.extend(sorted(graph_uses & _FORBIDDEN_AUTOMATIC_CAPABILITIES))
            if forbidden:
                raise HarnessStarterError(
                    f"Workflow {workflow_id!r} violates automatic_merge: false: {forbidden}"
                )

    provider_bindings = _validate_provider_coverage(
        project,
        required_capabilities,
        registry_workspace=registry_workspace,
    )
    return StarterVerification(
        starter=manifest,
        package_sha256=_package_digest(file_digests),
        project=project,
        skill_ids=tuple(sorted(skill_ids)),
        workflow_ids=tuple(sorted(workflow_rows)),
        composed_workflow_ids=tuple(sorted(composed_ids)),
        required_capabilities=tuple(sorted(required_capabilities)),
        provider_bindings=provider_bindings,
        file_sha256=file_digests,
    )


def list_builtin_starters(*, registry_workspace: Path) -> list[dict[str, Any]]:
    if not BUILTIN_STARTERS_ROOT.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for directory in sorted(path for path in BUILTIN_STARTERS_ROOT.iterdir() if path.is_dir()):
        verification = verify_starter(directory, registry_workspace=registry_workspace)
        result.append(
            {
                "starter_id": verification.starter.starter_id,
                "version": verification.starter.version,
                "package_sha256": verification.package_sha256,
                "entrypoints": dict(verification.starter.entrypoints),
            }
        )
    return result


def require_builtin_starter(starter_id: str) -> Path:
    stable_id = _text(starter_id, field="starter_id")
    root = BUILTIN_STARTERS_ROOT.resolve()
    candidate = (root / stable_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HarnessStarterError("starter_id escapes the built-in Starter root") from exc
    if not candidate.is_dir() or candidate.is_symlink():
        raise HarnessStarterError(f"unknown built-in Starter: {starter_id!r}")
    return candidate


def initialize_starter(
    starter_id: str,
    output: Path,
    *,
    registry_workspace: Path,
) -> StarterVerification:
    source = require_builtin_starter(starter_id)
    expected = verify_starter(source, registry_workspace=registry_workspace)
    target = Path(output).resolve()
    if target.exists():
        raise HarnessStarterError(f"refusing to install into existing target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, target, symlinks=False)
        installed = verify_starter(target, registry_workspace=registry_workspace)
        if installed.package_sha256 != expected.package_sha256:
            raise HarnessStarterError("installed Starter differs from the verified built-in package")
    except Exception:
        if target.is_dir():
            shutil.rmtree(target)
        raise
    return installed


__all__ = [
    "BUILTIN_STARTERS_ROOT",
    "HarnessStarterError",
    "STARTER_SCHEMA",
    "STARTER_VERIFICATION_SCHEMA",
    "StarterManifest",
    "StarterVerification",
    "initialize_starter",
    "list_builtin_starters",
    "parse_starter_manifest",
    "require_builtin_starter",
    "verify_starter",
]
