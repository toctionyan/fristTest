from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAX_PARALLEL_REMOTE_READS = 4


class BatchPlanError(ValueError):
    """Raised when a bounded remote-read plan is invalid."""


@dataclass(frozen=True)
class ReadRequest:
    """One immutable remote read declared by the frozen working set."""

    key: str
    source: str
    ref: str
    path: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise BatchPlanError("read request key must be non-empty")
        if not self.source.strip():
            raise BatchPlanError(f"read request {self.key} must declare source")
        if not self.ref.strip():
            raise BatchPlanError(f"read request {self.key} must declare an immutable ref")
        if not self.path.strip():
            raise BatchPlanError(f"read request {self.key} must declare path")


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
    return ordered


def plan_read_batches(
    requests: Iterable[ReadRequest],
    *,
    max_parallel: int = MAX_PARALLEL_REMOTE_READS,
) -> list[ReadBatch]:
    """Group immutable reads into deterministic connector-local bounded batches.

    Dependency scheduling is deliberately out of scope until the real harness
    has a dependency-bearing working-set contract. This planner only solves the
    requirement we execute today: cap parallel independent reads.
    """
    if max_parallel < 1:
        raise BatchPlanError("max_parallel must be at least 1")
    if max_parallel > MAX_PARALLEL_REMOTE_READS:
        raise BatchPlanError(
            f"max_parallel cannot exceed safety cap {MAX_PARALLEL_REMOTE_READS}"
        )

    ordered = _validated_requests(requests)
    grouped: dict[tuple[str, str], list[ReadRequest]] = {}
    group_order: list[tuple[str, str]] = []
    for request in ordered:
        boundary = (request.source, request.ref)
        if boundary not in grouped:
            grouped[boundary] = []
            group_order.append(boundary)
        grouped[boundary].append(request)

    plan: list[ReadBatch] = []
    for source, ref in group_order:
        members = grouped[(source, ref)]
        for offset in range(0, len(members), max_parallel):
            plan.append(
                ReadBatch(
                    source=source,
                    ref=ref,
                    requests=tuple(members[offset : offset + max_parallel]),
                )
            )
    return plan


def plan_metrics(plan: Iterable[ReadBatch]) -> dict[str, int]:
    batches = list(plan)
    return {
        "batch_count": len(batches),
        "request_count": sum(batch.size for batch in batches),
        "max_parallel_width": max((batch.size for batch in batches), default=0),
    }
