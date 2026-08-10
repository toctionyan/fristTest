from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from anti_stall import AtomicToolPolicy
from bounded_batch import MAX_PARALLEL_REMOTE_READS, ReadRequest, plan_metrics, plan_read_batches
from fallback_state import AtomicFallbackStateMachine
from snapshot_cache import SnapshotCache
from working_set import WorkingSetItem, WorkingSetManifest

RemoteReader = Callable[[str, str], str | bytes | None]


class TaskHarnessError(RuntimeError):
    """Raised when a frozen anti-stall task cannot be executed safely."""


class RemoteEmptyResult(RuntimeError):
    """Explicit empty-result failure for search/discovery style remote reads."""


@dataclass(frozen=True)
class ResourceResult:
    key: str
    source: str
    ref: str
    path: str
    required: bool
    status: str
    content: bytes | None
    primary_remote_calls: int
    fallback_remote_calls: int
    failure_kind: str | None
    state: dict[str, Any]


@dataclass(frozen=True)
class TaskHarnessResult:
    status: str
    resources: tuple[ResourceResult, ...]
    contents: Mapping[tuple[str, str], bytes]
    metrics: Mapping[str, int]


def _failure_kind(exc: Exception) -> str:
    if isinstance(exc, RemoteEmptyResult):
        return "empty_result"
    if isinstance(exc, TimeoutError):
        return "timeout"
    text = str(exc).casefold()
    if "503" in text:
        return "503"
    if isinstance(exc, ConnectionError):
        return "connection_error"
    return exc.__class__.__name__.casefold() or "failure"


def _payload(value: str | bytes | None) -> bytes:
    if value is None:
        raise RemoteEmptyResult("remote reader returned no result")
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


class AntiStallTaskHarness:
    """Execute one frozen remote working set without open-ended connector chains.

    Each unique resource is an atomic acquisition step with a two-call ceiling:
    one primary call and, only after failure, one explicitly declared fallback.
    Independent acquisition steps may run in a bounded parallel batch. Cache
    hits require no remote call. Required acquisition failure stops the task;
    optional failures are retained as evidence but do not erase successful work.

    The harness is intentionally independent from Quality Loop gate, claim,
    repair-round and convergence semantics. It only acquires immutable inputs.
    Connector execution policy is therefore an outer acquisition concern, not
    a replacement for the existing automated Quality Loop control plane.
    """

    def __init__(
        self,
        *,
        cache: SnapshotCache,
        readers: Mapping[str, RemoteReader],
        ref_by_source: Mapping[str, str],
        fallback_by_source: Mapping[str, str] | None = None,
        max_parallel: int = MAX_PARALLEL_REMOTE_READS,
    ) -> None:
        if max_parallel < 1 or max_parallel > MAX_PARALLEL_REMOTE_READS:
            raise ValueError(
                f"max_parallel must be between 1 and {MAX_PARALLEL_REMOTE_READS}"
            )
        self.cache = cache
        self.readers = dict(readers)
        self.ref_by_source = {str(key): str(value) for key, value in ref_by_source.items()}
        self.fallback_by_source = {
            str(key): str(value) for key, value in (fallback_by_source or {}).items()
        }
        self.max_parallel = int(max_parallel)

    def _ref(self, source: str) -> str:
        ref = str(self.ref_by_source.get(source) or "").strip()
        if not ref:
            raise TaskHarnessError(f"remote source has no immutable ref: {source}")
        return ref

    def _reader(self, source: str) -> RemoteReader:
        reader = self.readers.get(source)
        if reader is None:
            raise TaskHarnessError(f"remote source has no reader: {source}")
        return reader

    def _acquire(self, request: ReadRequest, item: WorkingSetItem) -> ResourceResult:
        machine = AtomicFallbackStateMachine(policy=AtomicToolPolicy(max_remote_calls=2))
        machine.begin(request.source)
        primary_calls = 0
        fallback_calls = 0
        content: bytes | None = None

        machine.authorize_remote()
        primary_calls += 1
        try:
            content = _payload(self._reader(request.source)(request.ref, request.path))
        except Exception as exc:
            fallback = self.fallback_by_source.get(request.source)
            machine.remote_failed(_failure_kind(exc), fallback_tool=fallback)
            if machine.state == "FALLBACK":
                fallback_source = str(machine.current_tool)
                fallback_ref = str(self.ref_by_source.get(fallback_source) or request.ref)
                machine.authorize_remote()
                fallback_calls += 1
                try:
                    content = _payload(
                        self._reader(fallback_source)(fallback_ref, request.path)
                    )
                except Exception as fallback_exc:
                    machine.remote_failed(_failure_kind(fallback_exc))
                else:
                    machine.remote_succeeded()
            if machine.state == "STOPPED":
                return ResourceResult(
                    key=request.key,
                    source=request.source,
                    ref=request.ref,
                    path=request.path,
                    required=item.required,
                    status="STOPPED",
                    content=None,
                    primary_remote_calls=primary_calls,
                    fallback_remote_calls=fallback_calls,
                    failure_kind=machine.failure_kind,
                    state=machine.snapshot(),
                )
        else:
            machine.remote_succeeded()

        if machine.state != "SUCCEEDED" or content is None:
            raise TaskHarnessError(
                f"resource acquisition ended in unexpected state {machine.state}: {request.key}"
            )
        self.cache.put(
            source=request.source,
            ref=request.ref,
            path=request.path,
            content=content,
        )
        return ResourceResult(
            key=request.key,
            source=request.source,
            ref=request.ref,
            path=request.path,
            required=item.required,
            status="DONE",
            content=content,
            primary_remote_calls=primary_calls,
            fallback_remote_calls=fallback_calls,
            failure_kind=None,
            state=machine.snapshot(),
        )

    def execute(self, manifest: WorkingSetManifest) -> TaskHarnessResult:
        if not manifest.frozen:
            raise TaskHarnessError("working set must be frozen before execution")

        unique = manifest.deduplicated_items()
        item_by_key: dict[str, WorkingSetItem] = {}
        cached_results: list[ResourceResult] = []
        requests: list[ReadRequest] = []
        contents: dict[tuple[str, str], bytes] = {}

        for index, item in enumerate(unique):
            ref = self._ref(item.source)
            key = f"r{index}:{item.source}:{item.resource}"
            cached = self.cache.get(source=item.source, ref=ref, path=item.resource)
            if cached is not None:
                cached_results.append(
                    ResourceResult(
                        key=key,
                        source=item.source,
                        ref=ref,
                        path=item.resource,
                        required=item.required,
                        status="CACHE_HIT",
                        content=cached,
                        primary_remote_calls=0,
                        fallback_remote_calls=0,
                        failure_kind=None,
                        state={"state": "DONE", "cache_hit": True},
                    )
                )
                contents[(item.source, item.resource)] = cached
                continue
            request = ReadRequest(
                key=key,
                source=item.source,
                ref=ref,
                path=item.resource,
            )
            requests.append(request)
            item_by_key[key] = item

        plan = plan_read_batches(requests, max_parallel=self.max_parallel)
        fetched_results: list[ResourceResult] = []
        optimized_serial_depth = 0

        for batch in plan:
            # All futures start together; collecting in request order keeps the
            # evidence/result order deterministic without serializing execution.
            with ThreadPoolExecutor(max_workers=batch.size) as pool:
                futures = [
                    pool.submit(self._acquire, request, item_by_key[request.key])
                    for request in batch.requests
                ]
                batch_results = [future.result() for future in futures]
            fetched_results.extend(batch_results)
            optimized_serial_depth += 1
            if any(result.fallback_remote_calls for result in batch_results):
                optimized_serial_depth += 1
            for result in batch_results:
                if result.content is not None:
                    contents[(result.source, result.path)] = result.content

        all_by_key = {result.key: result for result in cached_results + fetched_results}
        ordered_results = tuple(
            all_by_key[f"r{index}:{item.source}:{item.resource}"]
            for index, item in enumerate(unique)
        )
        required_failures = [
            result for result in ordered_results if result.required and result.status == "STOPPED"
        ]
        status = "STOPPED" if required_failures else "DONE"

        plan_stats = plan_metrics(plan)
        primary_calls = sum(result.primary_remote_calls for result in ordered_results)
        fallback_calls = sum(result.fallback_remote_calls for result in ordered_results)
        declared_count = len(manifest.items)
        unique_count = len(unique)
        cache_hits = sum(result.status == "CACHE_HIT" for result in ordered_results)
        metrics = {
            "declared_resources": declared_count,
            "unique_resources": unique_count,
            "duplicate_reads_avoided": declared_count - unique_count,
            "cache_hits": cache_hits,
            "cache_misses": unique_count - cache_hits,
            "primary_remote_calls": primary_calls,
            "fallback_remote_calls": fallback_calls,
            "total_remote_calls": primary_calls + fallback_calls,
            "batch_count": plan_stats["batch_count"],
            "max_parallel_width": plan_stats["max_parallel_width"],
            # Naive baseline: one serial remote read for every declaration.
            "legacy_remote_calls": declared_count,
            "legacy_serial_depth": declared_count,
            "optimized_serial_depth": optimized_serial_depth,
        }
        return TaskHarnessResult(
            status=status,
            resources=ordered_results,
            contents=contents,
            metrics=metrics,
        )
