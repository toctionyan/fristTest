from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAX_PARALLEL_REMOTE_READS = 4


class BatchPlanError(ValueError):
    """Raised when a bounded remote-read plan is invalid or cyclic."""


@dataclass(frozen=True)
class ReadRequest:
    """One immutable remote read declared by the frozen working set."""

    key: str
    source: str
    ref: str
    path: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise BatchPlanError("read request key must be non-empty")
        if not self.source.strip():
            raise BatchPlanError(f"read request {self.key} must declare source")
        if not self.ref.strip():
            raise BatchPlanError(f"read request {self.key} must declare an immutable ref")
        if not self.path.strip():
            raise BatchPlanError(f"read request {self.key} must declare path")
        if self.key in self.depends_on:
            raise BatchPlanError(f"read request {self.key} cannot depend on itself")


@dataclass(frozen=True)
class ReadBatch:
    """One bounded parallel batch for a single connector/ref boundary."""

    source: str
    ref: str
    requests: tuple[ReadRequest, ...]

    @property
    def size(self) -> int:
        return len(self.requests)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(request.key for request in self.requests)


def _validated_requests(requests: Iterable[ReadRequest]) -> list[ReadRequest]:
    ordered = list(requests)
    keys = [request.key for request in ordered]
    if len(keys) != len(set(keys)):
        raise BatchPlanError("remote read request keys must be unique")
    known = set(keys)
    for request in ordered:
        unknown = set(request.depends_on) - known
        if unknown:
            raise BatchPlanError(
                f"read request {request.key} depends on unknown requests: {sorted(unknown)}"
            )
    return ordered


def plan_read_batches(
    requests: Iterable[ReadRequest],
    *,
    max_parallel: int = MAX_PARALLEL_REMOTE_READS,
) -> list[ReadBatch]:
    """Create dependency-safe, connector-local, bounded parallel read batches.

    The planner never executes tools.  It converts a frozen working set into a
    deterministic execution plan that a task harness may run concurrently.
    Independent reads sharing the same source/ref are grouped together, but no
    batch may exceed ``max_parallel``.  Dependency descendants are not scheduled
    until all requests in the current dependency frontier are complete.
    """

    if max_parallel < 1:
        raise BatchPlanError("max_parallel must be at least 1")
    if max_parallel > MAX_PARALLEL_REMOTE_READS:
        raise BatchPlanError(
            f"max_parallel cannot exceed safety cap {MAX_PARALLEL_REMOTE_READS}"
        )

    ordered = _validated_requests(requests)
    pending = {request.key: request for request in ordered}
    completed: set[str] = set()
    plan: list[ReadBatch] = []

    while pending:
        ready = [
            request
            for request in ordered
            if request.key in pending and set(request.depends_on).issubset(completed)
        ]
        if not ready:
            cycle = [request.key for request in ordered if request.key in pending]
            raise BatchPlanError(
                "remote read dependency graph is cyclic or unsatisfied: " + ", ".join(cycle)
            )

        grouped: dict[tuple[str, str], list[ReadRequest]] = {}
        group_order: list[tuple[str, str]] = []
        for request in ready:
            boundary = (request.source, request.ref)
            if boundary not in grouped:
                grouped[boundary] = []
                group_order.append(boundary)
            grouped[boundary].append(request)

        frontier_keys: set[str] = set()
        for boundary in group_order:
            source, ref = boundary
            members = grouped[boundary]
            for offset in range(0, len(members), max_parallel):
                chunk = tuple(members[offset : offset + max_parallel])
                plan.append(ReadBatch(source=source, ref=ref, requests=chunk))
                frontier_keys.update(request.key for request in chunk)

        # Advance only after the complete dependency frontier has been planned.
        # This prevents a descendant from being placed in a batch concurrent
        # with one of its prerequisites merely because an earlier chunk was full.
        completed.update(frontier_keys)
        for key in frontier_keys:
            pending.pop(key, None)

    return plan


def plan_metrics(plan: Iterable[ReadBatch]) -> dict[str, int]:
    batches = list(plan)
    return {
        "batch_count": len(batches),
        "request_count": sum(batch.size for batch in batches),
        "max_parallel_width": max((batch.size for batch in batches), default=0),
    }
