from __future__ import annotations

"""Generic customer action metadata from installed operation plugins.

Domain modules own resource-specific card projection.  This helper merely
turns a verified target into public operation metadata and never assumes an
order resource at import time.
"""
from typing import Any, Iterable


class CustomerActionSpec:
    def __init__(self, plugin: Any):
        self._plugin = plugin
        self.business_code = plugin.business_code
        self.action_id = plugin.action_id
        self.label = plugin.label
        self.intent_template = getattr(plugin, "intent_template", "")
        self.capability_key = getattr(plugin, "capability_key", "business_application")
        self.mode = getattr(plugin, "mode", "agent_transaction")

    def to_public(self, *, resource_type: str, resource_id: str, input_hints: dict[str, Any] | None = None) -> dict[str, Any]:
        public = self._plugin.public_metadata(target={"resource_type": resource_type, "resource_id": str(resource_id)})
        public["input_hints"] = dict(input_hints or {})
        return public


def _registry():
    from agent_core.modules import current_runtime_registry
    return current_runtime_registry()


def customer_actions_for_business_codes(
    codes: Iterable[Any], *, resource_type: str, resource_id: str,
) -> list[dict[str, Any]]:
    return _registry().operations.public_actions_for_business_codes(codes, resource_id=str(resource_id), resource_type=str(resource_type))


def registered_business_codes() -> set[str]:
    return _registry().operations.business_codes()


def registered_action_ids() -> set[str]:
    return _registry().operations.action_ids()


def action_spec_for_action_id(action_id: str) -> CustomerActionSpec | None:
    wanted = str(action_id or "").strip()
    plugin = _registry().operations.get(wanted)
    return CustomerActionSpec(plugin) if plugin is not None else None


def validate_catalog_integrity(
    *,
    action_ids: Iterable[str],
    gateway_policy_ids: Iterable[str],
    commit_dispatcher_ids: Iterable[str],
) -> None:
    """Validate cross-layer action coverage from explicitly composed inputs."""
    installed = {str(value) for value in action_ids if str(value)}
    missing = {
        "gateway_policy": sorted(installed - {str(value) for value in gateway_policy_ids}),
        "commit_dispatcher": sorted(installed - {str(value) for value in commit_dispatcher_ids}),
    }
    missing = {key: value for key, value in missing.items() if value}
    if missing:
        raise RuntimeError(f"customer action catalog contains incomplete transaction lifecycles: {missing}")
