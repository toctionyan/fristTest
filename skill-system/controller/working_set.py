from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class WorkingSetError(ValueError):
    """Raised when a working-set declaration is invalid."""


@dataclass(frozen=True)
class WorkingSetItem:
    """One resource needed by an atomic task step before execution starts."""

    resource: str
    source: str = "github.fetch_file"
    required: bool = True
    role: str = "source"

    def key(self) -> tuple[str, str]:
        return (str(self.source), str(self.resource))


@dataclass
class WorkingSetManifest:
    """Freeze and deduplicate remote inputs before an atomic task step runs.

    The manifest is intentionally planning-only.  It does not fetch resources
    itself and does not alter Quality Loop semantics.  A harness can build this
    manifest once, deduplicate it, then execute the bounded remote plan through
    the anti-stall policy instead of discovering files one-by-one while running.
    """

    goal: str
    items: list[WorkingSetItem] = field(default_factory=list)
    frozen: bool = False

    def add(
        self,
        resource: str,
        *,
        source: str = "github.fetch_file",
        required: bool = True,
        role: str = "source",
    ) -> None:
        if self.frozen:
            raise WorkingSetError("working set is frozen for this atomic step")
        resource = str(resource).strip()
        source = str(source).strip()
        role = str(role).strip() or "source"
        if not resource:
            raise WorkingSetError("working-set resource must be non-empty")
        if not source:
            raise WorkingSetError("working-set source must be non-empty")
        self.items.append(
            WorkingSetItem(
                resource=resource,
                source=source,
                required=bool(required),
                role=role,
            )
        )

    def extend(self, items: Iterable[WorkingSetItem]) -> None:
        for item in items:
            self.add(
                item.resource,
                source=item.source,
                required=item.required,
                role=item.role,
            )

    def freeze(self) -> "WorkingSetManifest":
        self.frozen = True
        return self

    def deduplicated_items(self) -> list[WorkingSetItem]:
        """Return a stable unique plan, preserving the first requested order.

        If the same resource is later declared required after being optional,
        required wins without creating a second remote fetch.
        """
        order: list[tuple[str, str]] = []
        by_key: dict[tuple[str, str], WorkingSetItem] = {}
        for item in self.items:
            key = item.key()
            current = by_key.get(key)
            if current is None:
                order.append(key)
                by_key[key] = item
                continue
            if item.required and not current.required:
                by_key[key] = WorkingSetItem(
                    resource=current.resource,
                    source=current.source,
                    required=True,
                    role=current.role,
                )
        return [by_key[key] for key in order]

    def remote_plan(self) -> list[dict[str, Any]]:
        """Group the unique resources by source so a harness can batch them."""
        grouped: dict[str, list[WorkingSetItem]] = {}
        source_order: list[str] = []
        for item in self.deduplicated_items():
            if item.source not in grouped:
                grouped[item.source] = []
                source_order.append(item.source)
            grouped[item.source].append(item)
        return [
            {
                "source": source,
                "resources": [item.resource for item in grouped[source]],
                "required_resources": [
                    item.resource for item in grouped[source] if item.required
                ],
            }
            for source in source_order
        ]

    def snapshot(self) -> dict[str, Any]:
        unique = self.deduplicated_items()
        return {
            "goal": str(self.goal),
            "frozen": self.frozen,
            "declared_count": len(self.items),
            "unique_count": len(unique),
            "duplicate_count": len(self.items) - len(unique),
            "items": [
                {
                    "resource": item.resource,
                    "source": item.source,
                    "required": item.required,
                    "role": item.role,
                }
                for item in unique
            ],
            "remote_plan": self.remote_plan(),
        }
