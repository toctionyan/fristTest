from __future__ import annotations

"""Resource-level extension contracts.

A resource plugin owns only stable resource identity semantics.  It must not
contain operation rules, transaction lifecycle logic, UI authority logic or
LLM interpretation.  This keeps adding a resource type from changing the
runtime kernel.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class ResourcePlugin(Protocol):
    resource_type: str
    id_fields: tuple[str, ...]

    def normalize_target(self, target: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical resource target or raise ValueError."""

    def public_target(self, resource_id: str) -> dict[str, str]:
        """Build the client-neutral target shape used by operation contracts."""

    def fetch_current(self, adapter: Any, actor: Any, *, resource_id: str) -> dict[str, Any]:
        """Read a current authoritative resource projection."""

    def label_for(self, facts: dict[str, Any], *, resource_id: str) -> str:
        """Return a customer-readable label for an artifact projection."""


@dataclass(frozen=True)
class DeclarativeResourcePlugin:
    """Small default implementation for resources identified by one stable id."""

    resource_type: str
    id_fields: tuple[str, ...] = ("resource_id",)

    def normalize_target(self, target: dict[str, Any]) -> dict[str, Any]:
        row = dict(target or {})
        declared_type = str(row.get("resource_type") or "").strip()
        if declared_type and declared_type != self.resource_type:
            raise ValueError(
                f"target resource type mismatch: expected {self.resource_type}, got {declared_type}"
            )
        resource_id = ""
        for field in self.id_fields:
            candidate = str(row.get(field) or "").strip()
            if candidate:
                resource_id = candidate
                break
        if not resource_id:
            raise ValueError(f"{self.resource_type} target requires a resource id")
        return {"resource_type": self.resource_type, "resource_id": resource_id}

    def public_target(self, resource_id: str) -> dict[str, str]:
        normalized = self.normalize_target({"resource_type": self.resource_type, "resource_id": resource_id})
        return {"resource_type": str(normalized["resource_type"]), "resource_id": str(normalized["resource_id"])}

    def fetch_current(self, adapter: Any, actor: Any, *, resource_id: str) -> dict[str, Any]:
        raise NotImplementedError(f"resource {self.resource_type} does not declare a read adapter")

    def label_for(self, facts: dict[str, Any], *, resource_id: str) -> str:
        return f"{self.resource_type}:{resource_id}"
