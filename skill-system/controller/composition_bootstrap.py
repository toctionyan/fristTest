from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from capability_registry import ProviderSpec, load_provider_registry
from workflow_activation import WorkflowActivation, activate_workflow
from workflow_registry import WorkflowSpec

COMPOSITION_REGISTRY_SCHEMA = "harness-composition-registry@1"
COMPOSITION_REGISTRY_PATH = Path("skill-system/registry/compositions.json")


class CompositionBootstrapError(ValueError):
    """Raised when a composition cannot be assembled without weakening boundaries."""


@dataclass(frozen=True)
class CompositionSpec:
    composition_id: str
    workflow_id: str
    available_provider_ids: tuple[str, ...]
    provider_preferences: Mapping[str, str]
    allowed_profiles: Mapping[str, tuple[str, ...]]
    write_authority_required: bool
    completion_authority: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "composition_id": self.composition_id,
            "workflow_id": self.workflow_id,
            "available_provider_ids": list(self.available_provider_ids),
            "provider_preferences": dict(self.provider_preferences),
            "allowed_profiles": {
                capability_id: list(profiles)
                for capability_id, profiles in self.allowed_profiles.items()
            },
            "write_authority_required": self.write_authority_required,
            "completion_authority": self.completion_authority,
        }


@dataclass(frozen=True)
class CompositionAssembly:
    composition: CompositionSpec
    activation: WorkflowActivation

    @property
    def ready(self) -> bool:
        return self.activation.ready

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "harness-composition-assembly@1",
            "status": "PASS" if self.ready else "BLOCKED_CONFIGURATION",
            "composition": self.composition.as_dict(),
            "workflow_activation": self.activation.as_dict(),
            "policy": {
                "composition_selection_grants_write_authority": False,
                "provider_binding_activates_provider": False,
                "adapter_success_completes_taskrun": False,
                "quality_authority_changed": False,
                "taskrun_authority_changed": False,
                "completion_authority_changed": False,
            },
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _identifier(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or any(char.isspace() for char in text):
        raise CompositionBootstrapError(f"{field} must be a non-empty stable identifier")
    return text


def _unique_identifiers(values: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not values:
        raise CompositionBootstrapError(f"{field} must be a non-empty array")
    normalized = tuple(_identifier(value, field=field) for value in values)
    if len(set(normalized)) != len(normalized):
        raise CompositionBootstrapError(f"{field} must contain unique values")
    return normalized


def _parse_provider_preferences(value: object, *, composition_id: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CompositionBootstrapError(
            f"composition {composition_id!r} provider_preferences must be an object"
        )
    result: dict[str, str] = {}
    for capability_id, provider_id in value.items():
        capability = _identifier(capability_id, field="capability_id")
        provider = _identifier(provider_id, field="provider_id")
        result[capability] = provider
    return result


def _parse_allowed_profiles(value: object, *, composition_id: str) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CompositionBootstrapError(
            f"composition {composition_id!r} allowed_profiles must be an object keyed by capability"
        )
    result: dict[str, tuple[str, ...]] = {}
    for capability_id, raw_profiles in value.items():
        capability = _identifier(capability_id, field="capability_id")
        profiles = _unique_identifiers(
            raw_profiles,
            field=f"composition {composition_id!r} allowed_profiles[{capability!r}]",
        )
        result[capability] = profiles
    return result


def _parse_composition(raw: object) -> CompositionSpec:
    if not isinstance(raw, dict):
        raise CompositionBootstrapError("each composition registry row must be an object")
    composition_id = _identifier(raw.get("composition_id"), field="composition_id")
    workflow_id = _identifier(raw.get("workflow_id"), field="workflow_id")
    available_provider_ids = _unique_identifiers(
        raw.get("available_provider_ids"),
        field=f"composition {composition_id!r} available_provider_ids",
    )
    write_authority_required = raw.get("write_authority_required")
    if not isinstance(write_authority_required, bool):
        raise CompositionBootstrapError(
            f"composition {composition_id!r} write_authority_required must be boolean"
        )
    completion_authority = _identifier(
        raw.get("completion_authority"),
        field=f"composition {composition_id!r} completion_authority",
    )
    if completion_authority != "TaskRun":
        raise CompositionBootstrapError(
            f"composition {composition_id!r} completion_authority must remain 'TaskRun'"
        )
    return CompositionSpec(
        composition_id=composition_id,
        workflow_id=workflow_id,
        available_provider_ids=available_provider_ids,
        provider_preferences=_parse_provider_preferences(
            raw.get("provider_preferences"),
            composition_id=composition_id,
        ),
        allowed_profiles=_parse_allowed_profiles(
            raw.get("allowed_profiles"),
            composition_id=composition_id,
        ),
        write_authority_required=write_authority_required,
        completion_authority=completion_authority,
    )


def load_composition_registry(workspace: Path) -> dict[str, CompositionSpec]:
    path = workspace.resolve() / COMPOSITION_REGISTRY_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CompositionBootstrapError(f"composition registry is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CompositionBootstrapError(f"composition registry JSON is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != COMPOSITION_REGISTRY_SCHEMA:
        raise CompositionBootstrapError(
            f"composition registry schema must be {COMPOSITION_REGISTRY_SCHEMA!r}"
        )
    rows = payload.get("compositions")
    if not isinstance(rows, list) or not rows:
        raise CompositionBootstrapError("composition registry requires a non-empty compositions list")

    result: dict[str, CompositionSpec] = {}
    for raw in rows:
        spec = _parse_composition(raw)
        if spec.composition_id in result:
            raise CompositionBootstrapError(f"duplicate composition_id: {spec.composition_id}")
        result[spec.composition_id] = spec
    return result


def require_composition(workspace: Path, composition_id: str) -> CompositionSpec:
    key = _identifier(composition_id, field="composition_id")
    registry = load_composition_registry(workspace)
    try:
        return registry[key]
    except KeyError as exc:
        raise CompositionBootstrapError(f"unknown composition_id: {composition_id!r}") from exc


def _validate_provider_preferences(
    spec: CompositionSpec,
    providers: Mapping[str, ProviderSpec],
) -> None:
    available = set(spec.available_provider_ids)
    unknown = sorted(available - set(providers))
    if unknown:
        raise CompositionBootstrapError(
            f"composition {spec.composition_id!r} references unknown providers: {unknown}"
        )
    for capability_id, provider_id in spec.provider_preferences.items():
        if provider_id not in available:
            raise CompositionBootstrapError(
                f"composition {spec.composition_id!r} prefers unavailable provider {provider_id!r} "
                f"for capability {capability_id!r}"
            )
        if capability_id not in providers[provider_id].capabilities:
            raise CompositionBootstrapError(
                f"composition {spec.composition_id!r} provider {provider_id!r} does not provide "
                f"capability {capability_id!r}"
            )


def _validate_policy_against_workflow(spec: CompositionSpec, workflow: WorkflowSpec) -> None:
    if workflow.write_governed and not spec.write_authority_required:
        raise CompositionBootstrapError(
            f"composition {spec.composition_id!r} cannot assemble write-governed workflow "
            "without write_authority_required=true"
        )

    declared_capabilities = set(workflow.required_capabilities) | set(workflow.optional_capabilities)
    undeclared_profile_policies = sorted(set(spec.allowed_profiles) - declared_capabilities)
    if undeclared_profile_policies:
        raise CompositionBootstrapError(
            f"composition {spec.composition_id!r} has profile policies for undeclared capabilities: "
            f"{undeclared_profile_policies}"
        )

    local_process_caps = {
        capability_id
        for capability_id in declared_capabilities
        if spec.provider_preferences.get(capability_id) == "local.process"
    }
    missing_profile_policy = sorted(
        capability_id
        for capability_id in local_process_caps
        if capability_id in {"test.run", "quality.evaluate"}
        and capability_id not in spec.allowed_profiles
    )
    if missing_profile_policy:
        raise CompositionBootstrapError(
            f"composition {spec.composition_id!r} requires allow-listed profiles for local.process "
            f"capabilities: {missing_profile_policy}"
        )


class CompositionBootstrap:
    """Assemble validated Workflow/provider inputs without acquiring authority."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def resolve(self, composition_id: str) -> CompositionSpec:
        return require_composition(self.workspace, composition_id)

    def assemble(self, composition_id: str) -> CompositionAssembly:
        spec = self.resolve(composition_id)
        providers = load_provider_registry(self.workspace)
        _validate_provider_preferences(spec, providers)
        activation = activate_workflow(
            self.workspace,
            workflow_id=spec.workflow_id,
            available_provider_ids=spec.available_provider_ids,
            provider_preferences=spec.provider_preferences,
        )
        _validate_policy_against_workflow(spec, activation.workflow)
        return CompositionAssembly(composition=spec, activation=activation)

    def build_runtime_input(self, composition_id: str) -> dict[str, Any]:
        assembly = self.assemble(composition_id)
        spec = assembly.composition
        return {
            "schema": "harness-composition-runtime-input@1",
            "status": "PASS" if assembly.ready else "BLOCKED_CONFIGURATION",
            "composition_id": spec.composition_id,
            "workflow_id": spec.workflow_id,
            "available_provider_ids": list(spec.available_provider_ids),
            "provider_preferences": dict(spec.provider_preferences),
            "allowed_profiles": {
                capability_id: list(profiles)
                for capability_id, profiles in spec.allowed_profiles.items()
            },
            "write_authority_required": spec.write_authority_required,
            "completion_authority": spec.completion_authority,
            "workflow_activation": assembly.activation.as_dict(),
            "authority_effect": False,
            "write_authority_granted": False,
            "provider_activation_granted": False,
            "completion_authority_changed": False,
        }


__all__ = [
    "COMPOSITION_REGISTRY_PATH",
    "COMPOSITION_REGISTRY_SCHEMA",
    "CompositionAssembly",
    "CompositionBootstrap",
    "CompositionBootstrapError",
    "CompositionSpec",
    "load_composition_registry",
    "require_composition",
]
