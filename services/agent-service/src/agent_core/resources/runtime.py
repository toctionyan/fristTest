from __future__ import annotations

"""Generic resource target hydration for structured entry points.

This is intentionally separate from target-set cardinality resolution.  It
turns one verified UI reference into a fresh ledger artifact via the registered
resource plugin, without knowing whether the resource is an order, invoice or
future domain object.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from agent_core.ledger import append_entries, artifact_entry, find_handle
from agent_core.resources.registry import ResourcePluginRegistry


class ReadableResourcePlugin(Protocol):
    resource_type: str

    def normalize_target(self, target: dict[str, Any]) -> dict[str, Any]: ...
    def fetch_current(self, adapter: Any, actor: Any, *, resource_id: str) -> dict[str, Any]: ...
    def label_for(self, facts: dict[str, Any], *, resource_id: str) -> str: ...


@dataclass(frozen=True)
class HydratedResourceTarget:
    ledger: list[dict[str, Any]]
    artifact: dict[str, Any]


class ResourceTargetRuntime:
    def __init__(self, resources: ResourcePluginRegistry) -> None:
        self._resources = resources

    def resolve_structured_target(
        self,
        *,
        ledger: list[dict[str, Any]],
        scope: dict[str, str],
        turn: int,
        expected_resource_type: str,
        raw_target: dict[str, Any],
        adapter: Any,
        actor: Any,
        source: str = "transaction_start",
    ) -> HydratedResourceTarget:
        plugin = self._resources.require(expected_resource_type)
        target = dict(raw_target or {})
        handle = str(target.get("target_handle") or target.get("handle") or "").strip()
        if handle:
            found = find_handle(
                ledger,
                handle,
                scope=scope,
                allowed_kinds={"artifact"},
                allowed_resource_types={expected_resource_type},
                active_only=False,
            )
            if found:
                return HydratedResourceTarget(ledger=list(ledger), artifact=found)

        normalized = plugin.normalize_target({"resource_type": expected_resource_type, **target})
        resource_id = str(normalized["resource_id"])
        for item in ledger:
            if (
                isinstance(item, dict)
                and item.get("kind") == "artifact"
                and str(item.get("resource_type") or "") == expected_resource_type
                and str(item.get("resource_id") or "") == resource_id
            ):
                return HydratedResourceTarget(ledger=list(ledger), artifact=item)

        readable = plugin
        if not hasattr(readable, "fetch_current") or not hasattr(readable, "label_for"):
            raise ValueError(f"resource {expected_resource_type} cannot be hydrated by the structured target runtime")
        payload = readable.fetch_current(adapter, actor, resource_id=resource_id)
        if not payload.get("success") or not isinstance(payload.get("data"), dict):
            raise LookupError(f"{expected_resource_type} not found or not visible")
        facts = dict(payload["data"])
        artifact = artifact_entry(
            resource_type=expected_resource_type,
            resource_id=resource_id,
            label=readable.label_for(facts, resource_id=resource_id),
            facts=facts,
            scope=scope,
            turn=max(1, int(turn or 0)),
            source=source,
            freshness_version=int(facts.get("version") or 1),
        )
        return HydratedResourceTarget(ledger=append_entries(ledger, [artifact]), artifact=artifact)
