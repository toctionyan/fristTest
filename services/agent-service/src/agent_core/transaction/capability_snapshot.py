from __future__ import annotations

"""Draft-time operation capability snapshots.

The registry may evolve after a Draft is created.  A frozen snapshot prevents
new plugin rules from silently reinterpreting a persisted, user-visible Draft.
"""

from typing import Any

from agent_core.modules import current_runtime_registry
from agent_core.operations.capability import capability_digest


SNAPSHOT_REQUIRED_KEYS = {
    "capability_id",
    "version",
    "target_resource_types",
    "target_cardinality",
    "min_targets",
    "max_targets",
    "input_binding",
    "authority_scope",
    "execution_mode",
    "result_shape",
    "supports_lifecycle_query",
    "input_schema_version",
    "input_schema_digest",
    "digest",
}


def snapshot_for_action(action_id: str) -> dict[str, Any] | None:
    plugin = current_runtime_registry().operations.get(str(action_id or ""))
    if plugin is None:
        return None
    capability = getattr(plugin, "operation_capability", None)
    if capability is None:
        return None
    return capability.snapshot(input_schema=list(getattr(plugin, "input_schema", []) or []))


def has_complete_snapshot(offer: dict[str, Any]) -> bool:
    snapshot = offer.get("operation_capability_snapshot") if isinstance(offer.get("operation_capability_snapshot"), dict) else {}
    return SNAPSHOT_REQUIRED_KEYS.issubset(snapshot) and bool(str(offer.get("operation_capability_digest") or snapshot.get("digest") or ""))


def snapshot_matches_registry(offer: dict[str, Any]) -> bool:
    if not has_complete_snapshot(offer):
        return False
    snapshot = dict(offer.get("operation_capability_snapshot") or {})
    current = snapshot_for_action(str(offer.get("action_id") or ""))
    if current is None:
        return False
    return (
        str(current.get("capability_id") or "") == str(snapshot.get("capability_id") or "")
        and str(current.get("version") or "") == str(snapshot.get("version") or "")
        and capability_digest(current) == capability_digest(snapshot)
    )


def attach_snapshot(offer: dict[str, Any]) -> dict[str, Any]:
    """Attach a snapshot only for a newly-created Draft, never reinterpret old ones."""
    row = dict(offer)
    snapshot = snapshot_for_action(str(row.get("action_id") or ""))
    if snapshot is None:
        return row
    row["operation_capability_snapshot"] = snapshot
    row["operation_capability_id"] = str(snapshot.get("capability_id") or "")
    row["operation_capability_version"] = str(snapshot.get("version") or "")
    row["operation_capability_digest"] = capability_digest(snapshot)
    return row
