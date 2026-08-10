from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[4]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from snapshot_cache import SnapshotCache
from task_harness import AntiStallTaskHarness, TaskHarnessError
from working_set import WorkingSetManifest


def _manifest(*paths: str, source: str = "github.fetch_file") -> WorkingSetManifest:
    manifest = WorkingSetManifest(goal="anti-stall integration")
    for path in paths:
        manifest.add(path, source=source)
    return manifest.freeze()


def test_working_set_cache_and_batch_reduce_calls_and_serial_depth(tmp_path: Path) -> None:
    cache = SnapshotCache(tmp_path / "cache")
    cache.put(source="github.fetch_file", ref="sha-1", path="a.py", content="cached-a")

    # Four misses are forced to overlap.  This both verifies bounded parallel
    # execution and stresses concurrent cache index writes.
    barrier = threading.Barrier(4)
    calls: Counter[str] = Counter()

    def reader(ref: str, path: str) -> str:
        assert ref == "sha-1"
        calls[path] += 1
        barrier.wait(timeout=3)
        return f"content:{path}"

    manifest = WorkingSetManifest(goal="compare old and optimized acquisition")
    manifest.add("a.py")
    manifest.add("a.py")  # duplicate declaration must not fetch twice
    for path in ("b.py", "c.py", "d.py", "e.py"):
        manifest.add(path)
    manifest.freeze()

    result = AntiStallTaskHarness(
        cache=cache,
        readers={"github.fetch_file": reader},
        ref_by_source={"github.fetch_file": "sha-1"},
        max_parallel=4,
    ).execute(manifest)

    assert result.status == "DONE"
    assert result.metrics == {
        "declared_resources": 6,
        "unique_resources": 5,
        "duplicate_reads_avoided": 1,
        "cache_hits": 1,
        "cache_misses": 4,
        "primary_remote_calls": 4,
        "fallback_remote_calls": 0,
        "total_remote_calls": 4,
        "batch_count": 1,
        "max_parallel_width": 4,
        "legacy_remote_calls": 6,
        "legacy_serial_depth": 6,
        "optimized_serial_depth": 1,
    }
    assert calls == Counter({"b.py": 1, "c.py": 1, "d.py": 1, "e.py": 1})
    assert cache.snapshot()["entry_count"] == 5


def test_timeout_opens_primary_circuit_and_uses_exactly_one_fallback(tmp_path: Path) -> None:
    calls: Counter[str] = Counter()

    def primary(_ref: str, _path: str):
        calls["primary"] += 1
        raise TimeoutError("connector timeout")

    def fallback(_ref: str, _path: str) -> str:
        calls["fallback"] += 1
        return "recovered"

    result = AntiStallTaskHarness(
        cache=SnapshotCache(tmp_path / "cache"),
        readers={"github.search": primary, "github.fetch_file": fallback},
        ref_by_source={"github.search": "sha-1", "github.fetch_file": "sha-1"},
        fallback_by_source={"github.search": "github.fetch_file"},
    ).execute(_manifest("quality_loop.py", source="github.search"))

    resource = result.resources[0]
    assert result.status == "DONE"
    assert calls == Counter({"primary": 1, "fallback": 1})
    assert result.metrics["total_remote_calls"] == 2
    assert resource.state["policy"]["circuits"]["github.search"]["state"] == "open"
    assert resource.state["policy"]["circuits"]["github.fetch_file"]["state"] == "closed"


def test_503_then_fallback_failure_stops_without_third_remote_path(tmp_path: Path) -> None:
    calls: Counter[str] = Counter()

    def primary(_ref: str, _path: str):
        calls["primary"] += 1
        raise RuntimeError("HTTP 503 Service Unavailable")

    def fallback(_ref: str, _path: str):
        calls["fallback"] += 1
        raise TimeoutError("fallback timed out")

    result = AntiStallTaskHarness(
        cache=SnapshotCache(tmp_path / "cache"),
        readers={"github.search": primary, "github.fetch_file": fallback},
        ref_by_source={"github.search": "sha-1", "github.fetch_file": "sha-1"},
        fallback_by_source={"github.search": "github.fetch_file"},
    ).execute(_manifest("controller", source="github.search"))

    assert result.status == "STOPPED"
    assert calls == Counter({"primary": 1, "fallback": 1})
    assert result.metrics["primary_remote_calls"] == 1
    assert result.metrics["fallback_remote_calls"] == 1
    assert result.metrics["total_remote_calls"] == 2
    assert result.resources[0].state["state"] == "STOPPED"


def test_empty_result_is_failure_and_does_not_trigger_same_tool_retry(tmp_path: Path) -> None:
    calls: Counter[str] = Counter()

    def empty(_ref: str, _path: str):
        calls["primary"] += 1
        return None

    def fallback(_ref: str, _path: str) -> bytes:
        calls["fallback"] += 1
        return b"fallback-content"

    result = AntiStallTaskHarness(
        cache=SnapshotCache(tmp_path / "cache"),
        readers={"github.search": empty, "github.fetch_file": fallback},
        ref_by_source={"github.search": "sha-1", "github.fetch_file": "sha-1"},
        fallback_by_source={"github.search": "github.fetch_file"},
    ).execute(_manifest("needle", source="github.search"))

    assert result.status == "DONE"
    assert calls == Counter({"primary": 1, "fallback": 1})
    assert result.metrics["total_remote_calls"] == 2


def test_second_run_uses_snapshot_only_but_new_ref_forces_refresh(tmp_path: Path) -> None:
    calls: Counter[str] = Counter()

    def reader(ref: str, path: str) -> str:
        calls[ref] += 1
        return f"{ref}:{path}"

    cache = SnapshotCache(tmp_path / "cache")
    manifest = _manifest("a.py")

    first = AntiStallTaskHarness(
        cache=cache,
        readers={"github.fetch_file": reader},
        ref_by_source={"github.fetch_file": "sha-1"},
    ).execute(manifest)
    second = AntiStallTaskHarness(
        cache=cache,
        readers={"github.fetch_file": reader},
        ref_by_source={"github.fetch_file": "sha-1"},
    ).execute(manifest)
    refreshed = AntiStallTaskHarness(
        cache=cache,
        readers={"github.fetch_file": reader},
        ref_by_source={"github.fetch_file": "sha-2"},
    ).execute(manifest)

    assert first.metrics["total_remote_calls"] == 1
    assert second.metrics["total_remote_calls"] == 0
    assert second.metrics["cache_hits"] == 1
    assert refreshed.metrics["total_remote_calls"] == 1
    assert calls == Counter({"sha-1": 1, "sha-2": 1})


def test_missing_immutable_ref_fails_before_remote_execution(tmp_path: Path) -> None:
    with pytest.raises(TaskHarnessError, match="immutable ref"):
        AntiStallTaskHarness(
            cache=SnapshotCache(tmp_path / "cache"),
            readers={"github.fetch_file": lambda _ref, _path: "unused"},
            ref_by_source={},
        ).execute(_manifest("a.py"))
