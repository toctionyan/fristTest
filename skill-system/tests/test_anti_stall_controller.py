from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from anti_stall import AtomicToolPolicy, RemoteCallBudgetExceeded, ToolCircuitOpen
from bounded_batch import BatchPlanError, ReadRequest, plan_read_batches
from fallback_state import AtomicFallbackStateMachine, InvalidFallbackTransition
from snapshot_cache import SnapshotCache
from task_harness import AntiStallTaskHarness, TaskHarnessError
from working_set import WorkingSetError, WorkingSetManifest


def _manifest(*paths: str, source: str = "github.fetch_file") -> WorkingSetManifest:
    manifest = WorkingSetManifest(goal="anti-stall controller")
    for path in paths:
        manifest.add(path, source=source)
    return manifest.freeze()


class AntiStallPolicyTests(unittest.TestCase):
    def test_remote_budget_and_open_circuit_fail_closed(self) -> None:
        policy = AtomicToolPolicy(max_remote_calls=2)
        policy.authorize("github.fetch_file")
        policy.record_success("github.fetch_file")
        policy.authorize("github.search")
        with self.assertRaises(RemoteCallBudgetExceeded):
            policy.authorize("github.get_pr")

        circuit_policy = AtomicToolPolicy()
        circuit_policy.authorize("github.search")
        circuit_policy.record_failure("github.search", "timeout")
        with self.assertRaises(ToolCircuitOpen):
            circuit_policy.authorize("github.search")
        circuit_policy.claim_fallback("github.search", "github.fetch_file")
        with self.assertRaises(ValueError):
            circuit_policy.claim_fallback("github.search", "github.fetch_file")

    def test_local_operation_does_not_consume_remote_budget(self) -> None:
        policy = AtomicToolPolicy()
        policy.authorize("cache.get", remote=False)
        self.assertEqual(policy.snapshot()["remote_calls"], 0)


class WorkingSetAndBatchTests(unittest.TestCase):
    def test_manifest_freezes_deduplicates_and_upgrades_required(self) -> None:
        manifest = WorkingSetManifest(goal="inspect")
        manifest.add("a.py", required=False)
        manifest.add("a.py", required=True)
        manifest.add("b.py")
        snapshot = manifest.snapshot()
        self.assertEqual(snapshot["declared_count"], 3)
        self.assertEqual(snapshot["unique_count"], 2)
        self.assertTrue(manifest.deduplicated_items()[0].required)
        manifest.freeze()
        with self.assertRaises(WorkingSetError):
            manifest.add("c.py")

    def test_same_resource_from_different_sources_is_not_collapsed(self) -> None:
        manifest = WorkingSetManifest(goal="inspect")
        manifest.add("needle", source="github.search")
        manifest.add("needle", source="github.fetch_file")
        self.assertEqual(len(manifest.deduplicated_items()), 2)

    def test_batches_are_connector_local_and_hard_capped(self) -> None:
        requests = [
            ReadRequest(key=f"r{i}", source="github", ref="sha-1", path=f"{i}.py")
            for i in range(7)
        ]
        plan = plan_read_batches(requests, max_parallel=4)
        self.assertEqual([batch.size for batch in plan], [4, 3])

        boundaries = plan_read_batches(
            [
                ReadRequest(key="a", source="github", ref="sha-1", path="a"),
                ReadRequest(key="b", source="github", ref="sha-2", path="b"),
                ReadRequest(key="c", source="drive", ref="doc-v1", path="c"),
            ]
        )
        self.assertEqual(
            [(batch.source, batch.ref, batch.keys) for batch in boundaries],
            [
                ("github", "sha-1", ("a",)),
                ("github", "sha-2", ("b",)),
                ("drive", "doc-v1", ("c",)),
            ],
        )
        with self.assertRaises(BatchPlanError):
            plan_read_batches(requests[:1], max_parallel=5)
        with self.assertRaises(BatchPlanError):
            plan_read_batches(
                [
                    ReadRequest(key="dup", source="github", ref="sha", path="a"),
                    ReadRequest(key="dup", source="github", ref="sha", path="b"),
                ]
            )


class SnapshotCacheTests(unittest.TestCase):
    def test_cache_is_ref_bound_and_digest_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory))
            entry = cache.put(
                source="github.fetch_file",
                ref="sha-1",
                path="a.py",
                content="payload",
            )
            self.assertEqual(
                cache.get(source="github.fetch_file", ref="sha-1", path="a.py"),
                b"payload",
            )
            self.assertIsNone(
                cache.get(source="github.fetch_file", ref="sha-2", path="a.py")
            )
            object_path = Path(directory) / entry.cache_path
            object_path.write_bytes(b"tampered")
            self.assertIsNone(
                cache.get(source="github.fetch_file", ref="sha-1", path="a.py")
            )


class FallbackStateTests(unittest.TestCase):
    def test_primary_success_and_one_fallback(self) -> None:
        primary = AtomicFallbackStateMachine()
        primary.begin("github.fetch_file")
        self.assertEqual(primary.authorize_remote(), "github.fetch_file")
        primary.remote_succeeded()
        self.assertEqual(primary.snapshot()["state"], "SUCCEEDED")
        self.assertEqual(primary.snapshot()["policy"]["remote_calls"], 1)

        fallback = AtomicFallbackStateMachine()
        fallback.begin("github.search")
        fallback.authorize_remote()
        fallback.remote_failed("timeout", fallback_tool="github.fetch_file")
        self.assertEqual(fallback.snapshot()["state"], "FALLBACK")
        self.assertEqual(fallback.authorize_remote(), "github.fetch_file")
        fallback.remote_succeeded()
        self.assertEqual(fallback.snapshot()["state"], "SUCCEEDED")
        self.assertEqual(fallback.snapshot()["policy"]["remote_calls"], 2)

    def test_same_tool_or_third_path_is_rejected(self) -> None:
        same = AtomicFallbackStateMachine()
        same.begin("github.search")
        same.authorize_remote()
        with self.assertRaises(ValueError):
            same.remote_failed("empty_result", fallback_tool="github.search")

        machine = AtomicFallbackStateMachine()
        machine.begin("github.search")
        machine.authorize_remote()
        machine.remote_failed("503", fallback_tool="github.fetch_file")
        machine.authorize_remote()
        machine.remote_failed("timeout")
        self.assertEqual(machine.snapshot()["state"], "STOPPED")
        self.assertTrue(
            any(
                item["event"] == "fallback_exhausted"
                for item in machine.snapshot()["history"]
            )
        )

    def test_invalid_transition_fails_closed(self) -> None:
        machine = AtomicFallbackStateMachine()
        with self.assertRaises(InvalidFallbackTransition):
            machine.authorize_remote()


class HarnessTests(unittest.TestCase):
    def test_cache_dedupe_and_bounded_parallel_reduce_remote_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "cache")
            cache.put(
                source="github.fetch_file",
                ref="sha-1",
                path="a.py",
                content="cached-a",
            )
            barrier = threading.Barrier(4)
            calls: Counter[str] = Counter()

            def reader(ref: str, path: str) -> str:
                self.assertEqual(ref, "sha-1")
                calls[path] += 1
                barrier.wait(timeout=3)
                return f"content:{path}"

            manifest = WorkingSetManifest(goal="compare acquisition")
            manifest.add("a.py")
            manifest.add("a.py")
            for path in ("b.py", "c.py", "d.py", "e.py"):
                manifest.add(path)
            manifest.freeze()

            result = AntiStallTaskHarness(
                cache=cache,
                readers={"github.fetch_file": reader},
                ref_by_source={"github.fetch_file": "sha-1"},
                max_parallel=4,
            ).execute(manifest)

            self.assertEqual(result.status, "DONE")
            self.assertEqual(result.metrics["legacy_remote_calls"], 6)
            self.assertEqual(result.metrics["total_remote_calls"], 4)
            self.assertEqual(result.metrics["legacy_serial_depth"], 6)
            self.assertEqual(result.metrics["optimized_serial_depth"], 1)
            self.assertEqual(result.metrics["duplicate_reads_avoided"], 1)
            self.assertEqual(result.metrics["cache_hits"], 1)
            self.assertEqual(
                calls,
                Counter({"b.py": 1, "c.py": 1, "d.py": 1, "e.py": 1}),
            )

    def test_timeout_uses_one_fallback_and_fallback_failure_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: Counter[str] = Counter()

            def timeout(_ref: str, _path: str):
                calls["primary"] += 1
                raise TimeoutError("connector timeout")

            def recovered(_ref: str, _path: str) -> str:
                calls["fallback"] += 1
                return "recovered"

            result = AntiStallTaskHarness(
                cache=SnapshotCache(Path(directory) / "cache-a"),
                readers={"github.search": timeout, "github.fetch_file": recovered},
                ref_by_source={
                    "github.search": "sha-1",
                    "github.fetch_file": "sha-1",
                },
                fallback_by_source={"github.search": "github.fetch_file"},
            ).execute(_manifest("quality_loop.py", source="github.search"))
            self.assertEqual(result.status, "DONE")
            self.assertEqual(calls, Counter({"primary": 1, "fallback": 1}))
            self.assertEqual(result.metrics["total_remote_calls"], 2)

            calls.clear()

            def failed_fallback(_ref: str, _path: str):
                calls["fallback"] += 1
                raise TimeoutError("fallback timeout")

            stopped = AntiStallTaskHarness(
                cache=SnapshotCache(Path(directory) / "cache-b"),
                readers={"github.search": timeout, "github.fetch_file": failed_fallback},
                ref_by_source={
                    "github.search": "sha-1",
                    "github.fetch_file": "sha-1",
                },
                fallback_by_source={"github.search": "github.fetch_file"},
            ).execute(_manifest("controller", source="github.search"))
            self.assertEqual(stopped.status, "STOPPED")
            self.assertEqual(calls, Counter({"primary": 1, "fallback": 1}))
            self.assertEqual(stopped.metrics["total_remote_calls"], 2)

    def test_empty_result_uses_declared_fallback_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: Counter[str] = Counter()

            def empty(_ref: str, _path: str):
                calls["primary"] += 1
                return None

            def fallback(_ref: str, _path: str) -> bytes:
                calls["fallback"] += 1
                return b"fallback-content"

            result = AntiStallTaskHarness(
                cache=SnapshotCache(Path(directory) / "cache"),
                readers={"github.search": empty, "github.fetch_file": fallback},
                ref_by_source={
                    "github.search": "sha-1",
                    "github.fetch_file": "sha-1",
                },
                fallback_by_source={"github.search": "github.fetch_file"},
            ).execute(_manifest("needle", source="github.search"))
            self.assertEqual(result.status, "DONE")
            self.assertEqual(calls, Counter({"primary": 1, "fallback": 1}))

    def test_same_ref_reuses_cache_and_new_ref_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: Counter[str] = Counter()

            def reader(ref: str, path: str) -> str:
                calls[ref] += 1
                return f"{ref}:{path}"

            cache = SnapshotCache(Path(directory) / "cache")
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
            self.assertEqual(first.metrics["total_remote_calls"], 1)
            self.assertEqual(second.metrics["total_remote_calls"], 0)
            self.assertEqual(refreshed.metrics["total_remote_calls"], 1)
            self.assertEqual(calls, Counter({"sha-1": 1, "sha-2": 1}))

    def test_missing_ref_and_process_control_fail_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TaskHarnessError):
                AntiStallTaskHarness(
                    cache=SnapshotCache(Path(directory) / "cache-a"),
                    readers={"github.fetch_file": lambda _ref, _path: "unused"},
                    ref_by_source={},
                ).execute(_manifest("a.py"))

            for exc in (KeyboardInterrupt(), SystemExit(17)):
                def reader(_ref: str, _path: str, exc=exc):
                    raise exc

                harness = AntiStallTaskHarness(
                    cache=SnapshotCache(Path(directory) / "cache-b"),
                    readers={"github.fetch_file": reader},
                    ref_by_source={"github.fetch_file": "sha-1"},
                )
                with self.assertRaises(type(exc)):
                    harness.execute(_manifest("a.py"))


if __name__ == "__main__":
    unittest.main()
