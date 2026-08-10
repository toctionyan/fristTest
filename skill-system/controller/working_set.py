from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class WorkingSetError(ValueError):
    """Raised when a working-set declaration is invalid."""


@dataclass(frozen=True)
class WorkingSetItem:
    """One remote resource needed before an atomic task step executes."""

    resource: str
    source: str = "github.fetch_file"
    required: bool = True

    def key(self) -> tuple[str, str]:
        return (str(self.source), str(self.resource))


@dataclass
class WorkingSetManifest:
    """Freeze and deduplicate remote inputs before an atomic task step runs.

    The manifest is planning-only. It declares the bounded input set once;
    fetching, caching and batching belong to the task harness.
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
    ) -> None:
        if self.frozen:
            raise WorkingSetError("working set is frozen for this atomic step")
        resource = str(resource).strip()
        source = str(source).strip()
        if not resource:
            raise WorkingSetError("working-set resource must be non-empty")
        if not source:
            raise WorkingSetError("working-set source must be non-empty")
        self.items.append(
            WorkingSetItem(
                resource=resource,
                source=source,
                required=bool(required),
            )
        )

    def extend(self, items: Iterable[WorkingSetItem]) -> None:
        for item in items:
            self.add(
                item.resource,
                source=item.source,
                required=item.required,
            )

    def freeze(self) -> "WorkingSetManifest":
        self.frozen = True
        return self

    def deduplicated_items(self) -> list[WorkingSetItem]:
        """Return a stable unique set; required wins over optional."""
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
                )
        return [by_key[key] for key in order]

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
                }
                for item in unique
            ],
        }
