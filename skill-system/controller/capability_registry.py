from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

CAPABILITY_REGISTRY_SCHEMA = "capability-registry@1"
EXECUTOR_REGISTRY_SCHEMA = "executor-registry@1"
INTEGRATION_REGISTRY_SCHEMA = "integration-registry@1"
CAPABILITY_REGISTRY_PATH = Path("skill-system/registry/capabilities.json")
EXECUTOR_REGISTRY_PATH = Path("skill-system/registry/executors.json")
INTEGRATION_REGISTRY_PATH = Path("skill-system/registry/integrations.json")
PROVIDER_TYPES = frozenset({"executor", "integration"})


class CapabilityRegistryError(ValueError):
    """Raised when capability contracts or provider registrations are invalid."""


@dataclass(frozen=True)
class CapabilityContract:
    capability_id: str
    provider_type: str
    mutates: bool
    external_wait: bool
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "provider_type": self.provider_type,
            "mutates": self.mutates,
            "external_wait": self.external_wait,
            "description": self.description,
        }


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    provider_type: str
    priority: int
    activation_key: str
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "priority": self.priority,
            "activation_key": self.activation_key,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class CapabilityBinding:
    capability_id: str
    provider_id: str
    provider_type: str
    activation_key: str
    mutates: bool
    external_wait: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "activation_key": self.activation_key,
            "mutates": self.mutates,
            "external_wait": self.external_wait,
        }


@dataclass(frozen=True)
class CapabilityPreflight:
    status: str
    required_bindings: tuple[CapabilityBinding, ...]
    optional_bindings: tuple[CapabilityBinding, ...]
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "capability-preflight@1",
            "status": self.status,
            "required_bindings": [binding.as_dict() for binding in self.required_bindings],
            "optional_bindings": [binding.as_dict() for binding in self.optional_bindings],
            "missing_required": list(self.missing_required),
            "missing_optional": list(self.missing_optional),
            "authority_effect": False,
            "production_closed": False,
        }


def _text(value: object) -> str:
    return str(value or "").strip()


def _identifier(value: object, *, field: str) -> str:
    text = _text(value)
    if not text or any(char.isspace() for char in text):
        raise CapabilityRegistryError(f"{field} must be a non-empty stable identifier")
    return text


def _read_json(path: Path, *, schema: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CapabilityRegistryError(f"registry is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CapabilityRegistryError(f"registry JSON is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != schema:
        raise CapabilityRegistryError(f"registry schema must be {schema!r}: {path}")
    return payload


def load_capability_contracts(workspace: Path) -> dict[str, CapabilityContract]:
    root = workspace.resolve()
    payload = _read_json(root / CAPABILITY_REGISTRY_PATH, schema=CAPABILITY_REGISTRY_SCHEMA)
    rows = payload.get("capabilities")
    if not isinstance(rows, list) or not rows:
        raise CapabilityRegistryError("capability registry requires a non-empty capabilities list")
    contracts: dict[str, CapabilityContract] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise CapabilityRegistryError("each capability row must be an object")
        capability_id = _identifier(raw.get("capability_id"), field="capability_id")
        provider_type = _identifier(raw.get("provider_type"), field="provider_type")
        if provider_type not in PROVIDER_TYPES:
            raise CapabilityRegistryError(f"unsupported provider_type for {capability_id}: {provider_type}")
        if capability_id in contracts:
            raise CapabilityRegistryError(f"duplicate capability_id: {capability_id}")
        mutates = raw.get("mutates", False)
        external_wait = raw.get("external_wait", False)
        if not isinstance(mutates, bool) or not isinstance(external_wait, bool):
            raise CapabilityRegistryError(f"capability flags must be boolean: {capability_id}")
        contracts[capability_id] = CapabilityContract(
            capability_id=capability_id,
            provider_type=provider_type,
            mutates=mutates,
            external_wait=external_wait,
            description=_text(raw.get("description")),
        )
    return contracts


def _load_provider_file(
    workspace: Path,
    *,
    relative_path: Path,
    schema: str,
    expected_type: str,
    contracts: Mapping[str, CapabilityContract],
) -> dict[str, ProviderSpec]:
    payload = _read_json(workspace.resolve() / relative_path, schema=schema)
    rows = payload.get("providers")
    if not isinstance(rows, list):
        raise CapabilityRegistryError(f"provider registry requires providers array: {relative_path}")
    providers: dict[str, ProviderSpec] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise CapabilityRegistryError("each provider row must be an object")
        provider_id = _identifier(raw.get("provider_id"), field="provider_id")
        provider_type = _identifier(raw.get("provider_type"), field="provider_type")
        if provider_type != expected_type:
            raise CapabilityRegistryError(
                f"provider {provider_id!r} type mismatch: expected={expected_type} actual={provider_type}"
            )
        if provider_id in providers:
            raise CapabilityRegistryError(f"duplicate provider_id: {provider_id}")
        activation_key = _identifier(raw.get("activation_key"), field="activation_key")
        priority = raw.get("priority", 100)
        if not isinstance(priority, int) or priority < 0:
            raise CapabilityRegistryError(f"provider priority must be a non-negative integer: {provider_id}")
        raw_capabilities = raw.get("capabilities")
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise CapabilityRegistryError(f"provider {provider_id!r} requires capabilities")
        capabilities = tuple(_identifier(value, field="capability") for value in raw_capabilities)
        if len(set(capabilities)) != len(capabilities):
            raise CapabilityRegistryError(f"provider {provider_id!r} contains duplicate capabilities")
        for capability_id in capabilities:
            contract = contracts.get(capability_id)
            if contract is None:
                raise CapabilityRegistryError(
                    f"provider {provider_id!r} references unknown capability {capability_id!r}"
                )
            if contract.provider_type != expected_type:
                raise CapabilityRegistryError(
                    f"provider {provider_id!r} cannot provide {capability_id!r}; "
                    f"contract requires {contract.provider_type}"
                )
        providers[provider_id] = ProviderSpec(
            provider_id=provider_id,
            provider_type=provider_type,
            priority=priority,
            activation_key=activation_key,
            capabilities=capabilities,
        )
    return providers


def load_provider_registry(workspace: Path) -> dict[str, ProviderSpec]:
    contracts = load_capability_contracts(workspace)
    executors = _load_provider_file(
        workspace,
        relative_path=EXECUTOR_REGISTRY_PATH,
        schema=EXECUTOR_REGISTRY_SCHEMA,
        expected_type="executor",
        contracts=contracts,
    )
    integrations = _load_provider_file(
        workspace,
        relative_path=INTEGRATION_REGISTRY_PATH,
        schema=INTEGRATION_REGISTRY_SCHEMA,
        expected_type="integration",
        contracts=contracts,
    )
    overlap = sorted(set(executors) & set(integrations))
    if overlap:
        raise CapabilityRegistryError(f"provider ids must be globally unique: {overlap}")
    return {**executors, **integrations}


def _normalize_capabilities(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(_identifier(value, field="capability") for value in values)
    if len(set(normalized)) != len(normalized):
        raise CapabilityRegistryError("capability requirements must be unique")
    return normalized


def resolve_capability(
    workspace: Path,
    capability_id: str,
    *,
    available_provider_ids: Iterable[str],
    provider_preferences: Mapping[str, str] | None = None,
) -> CapabilityBinding | None:
    contracts = load_capability_contracts(workspace)
    providers = load_provider_registry(workspace)
    capability = _identifier(capability_id, field="capability_id")
    contract = contracts.get(capability)
    if contract is None:
        raise CapabilityRegistryError(f"unknown capability: {capability}")

    available = {_identifier(value, field="provider_id") for value in available_provider_ids}
    candidates = [
        provider
        for provider in providers.values()
        if provider.provider_id in available and capability in provider.capabilities
    ]
    preferred_id = _text((provider_preferences or {}).get(capability))
    if preferred_id:
        candidates = [provider for provider in candidates if provider.provider_id == preferred_id]
    if not candidates:
        return None
    provider = sorted(candidates, key=lambda row: (row.priority, row.provider_id))[0]
    return CapabilityBinding(
        capability_id=capability,
        provider_id=provider.provider_id,
        provider_type=provider.provider_type,
        activation_key=provider.activation_key,
        mutates=contract.mutates,
        external_wait=contract.external_wait,
    )


def preflight_capabilities(
    workspace: Path,
    *,
    required: Iterable[str] = (),
    optional: Iterable[str] = (),
    available_provider_ids: Iterable[str] = (),
    provider_preferences: Mapping[str, str] | None = None,
) -> CapabilityPreflight:
    required_ids = _normalize_capabilities(required)
    optional_ids = _normalize_capabilities(optional)
    overlap = sorted(set(required_ids) & set(optional_ids))
    if overlap:
        raise CapabilityRegistryError(f"capabilities cannot be both required and optional: {overlap}")

    required_bindings: list[CapabilityBinding] = []
    optional_bindings: list[CapabilityBinding] = []
    missing_required: list[str] = []
    missing_optional: list[str] = []
    for capability_id in required_ids:
        binding = resolve_capability(
            workspace,
            capability_id,
            available_provider_ids=available_provider_ids,
            provider_preferences=provider_preferences,
        )
        if binding is None:
            missing_required.append(capability_id)
        else:
            required_bindings.append(binding)
    for capability_id in optional_ids:
        binding = resolve_capability(
            workspace,
            capability_id,
            available_provider_ids=available_provider_ids,
            provider_preferences=provider_preferences,
        )
        if binding is None:
            missing_optional.append(capability_id)
        else:
            optional_bindings.append(binding)
    return CapabilityPreflight(
        status="PASS" if not missing_required else "BLOCKED_CONFIGURATION",
        required_bindings=tuple(required_bindings),
        optional_bindings=tuple(optional_bindings),
        missing_required=tuple(missing_required),
        missing_optional=tuple(missing_optional),
    )


__all__ = [
    "CAPABILITY_REGISTRY_PATH",
    "CAPABILITY_REGISTRY_SCHEMA",
    "EXECUTOR_REGISTRY_PATH",
    "EXECUTOR_REGISTRY_SCHEMA",
    "INTEGRATION_REGISTRY_PATH",
    "INTEGRATION_REGISTRY_SCHEMA",
    "CapabilityBinding",
    "CapabilityContract",
    "CapabilityPreflight",
    "CapabilityRegistryError",
    "ProviderSpec",
    "load_capability_contracts",
    "load_provider_registry",
    "preflight_capabilities",
    "resolve_capability",
]
