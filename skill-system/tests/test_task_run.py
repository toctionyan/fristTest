from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from task_run import (  # type: ignore
    PrematureCompletionError,
    RUN_STATUSES,
    TASK_RUN_SCHEMA_VERSION,
    TaskRunBindingError,
    TaskRunConflictError,
    TaskRunDriftError,
    TaskRunStore,
    evaluate_completion,
    stable_task_id,
)


class TaskRunStoreTest(unittest.TestCase):
    def _store(self, root: Path, *, fingerprint: str = "workspace-a") -> TaskRunStore:
        return TaskRunStore.open_or_create(
            root / ".quality/task-runs/run.json",
            task_id="repair-b34-test",
            task_kind="repair-loop",
            binding={
                "target_identity": "target-a",
                "policy_fingerprint": "policy-a",
                "baseline_fingerprint": "baseline-a",
            },
            required_conditions=(
                "inputs_validated",
                "source_changed",
                "targeted_validation_resolved",
                "full_regression_passed",
                "issues_closed",
            ),
            current_workspace_fingerprint=fingerprint,
        )

    def test_completion_guard_rejects_missing_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            store.mark_condition("inputs_validated", evidence_refs=["baseline/run-summary.json"])
            decision = evaluate_completion(store.payload)
            self.assertEqual(decision.status, "RUNNING")
            self.assertIn("full_regression_passed", decision.missing_conditions)
            with self.assertRaises(PrematureCompletionError):
                store.complete(
                    workspace_fingerprint="workspace-a",
                    evidence_refs=["final/run-summary.json"],
                )

    def test_completion_requires_evidence_for_every_condition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            for condition in store.payload["required_conditions"]:
                store.mark_condition(condition, evidence_refs=[f"evidence/{condition}.json"])
            store.checkpoint(
                status="RUNNING",
                phase="READY_TO_COMPLETE",
                workspace_fingerprint="workspace-a",
                evidence_refs=["evidence/final.json"],
            )
            store.complete(
                workspace_fingerprint="workspace-a",
                evidence_refs=["evidence/final.json"],
            )
            self.assertEqual(store.payload["status"], "COMPLETED")
            self.assertEqual(store.payload["phase"], "COMPLETED")

    def test_same_action_switches_to_fallback_after_two_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            arguments = {"job_id": 91947138587}
            first = store.plan_action(
                action_name="fetch-workflow-job-logs",
                arguments=arguments,
                state_fingerprint="job-identified",
                strategies=("github-job-logs", "local-ci-reproduction"),
            )
            self.assertEqual(first.decision, "RUN")
            self.assertEqual(first.strategy, "github-job-logs")
            store.record_action_result(
                first,
                result={"status": "no-parseable-log"},
                produced_new_evidence=False,
            )
            second = store.plan_action(
                action_name="fetch-workflow-job-logs",
                arguments=arguments,
                state_fingerprint="job-identified",
                strategies=("github-job-logs", "local-ci-reproduction"),
            )
            self.assertEqual(second.decision, "RUN")
            self.assertEqual(second.strategy, "github-job-logs")
            store.record_action_result(
                second,
                result={"status": "no-parseable-log"},
                produced_new_evidence=False,
            )
            fallback = store.plan_action(
                action_name="fetch-workflow-job-logs",
                arguments=arguments,
                state_fingerprint="job-identified",
                strategies=("github-job-logs", "local-ci-reproduction"),
            )
            self.assertEqual(fallback.decision, "SWITCH_FALLBACK")
            self.assertEqual(fallback.strategy, "local-ci-reproduction")

    def test_all_strategy_budgets_exhaust_to_blocked_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            for _ in range(2):
                plan = store.plan_action(
                    action_name="inspect-failure",
                    arguments={"run": 1},
                    state_fingerprint="failed",
                    strategies=("job-logs",),
                )
                store.record_action_result(
                    plan,
                    result={"available": False},
                    produced_new_evidence=False,
                )
            blocked = store.plan_action(
                action_name="inspect-failure",
                arguments={"run": 1},
                state_fingerprint="failed",
                strategies=("job-logs",),
            )
            self.assertEqual(blocked.decision, "BLOCKED")
            self.assertIsNone(blocked.strategy)

    def test_resume_rejects_workspace_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            store.checkpoint(
                status="RUNNING",
                phase="FIXER_APPLIED",
                workspace_fingerprint="workspace-after-fix",
                evidence_refs=["evidence/fix.json"],
            )
            with self.assertRaises(TaskRunDriftError):
                self._store(root, fingerprint="unexpected-workspace")

    def test_resume_allows_reconciliation_only_while_fixer_was_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            store.checkpoint(
                status="RUNNING",
                phase="INPUTS_VALIDATED",
                workspace_fingerprint="workspace-before-fix",
                evidence_refs=["evidence/inputs.json"],
            )
            store.checkpoint(
                status="REPAIRING",
                phase="FIXER_RUNNING",
                workspace_fingerprint="workspace-before-fix",
                evidence_refs=["evidence/fixer-started.json"],
            )
            resumed = self._store(root, fingerprint="workspace-after-interrupted-fix")
            self.assertEqual(resumed.payload["phase"], "FIXER_RUNNING")

    def test_resume_rejects_immutable_binding_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store(root)
            with self.assertRaises(TaskRunBindingError):
                TaskRunStore.open_or_create(
                    root / ".quality/task-runs/run.json",
                    task_id="repair-b34-test",
                    task_kind="repair-loop",
                    binding={
                        "target_identity": "different-target",
                        "policy_fingerprint": "policy-a",
                        "baseline_fingerprint": "baseline-a",
                    },
                    required_conditions=(
                        "inputs_validated",
                        "source_changed",
                        "targeted_validation_resolved",
                        "full_regression_passed",
                        "issues_closed",
                    ),
                    current_workspace_fingerprint="workspace-a",
                )

    def test_blocked_run_is_not_success_and_can_be_resumed_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp))
            revision_before = store.payload["revision"]
            store.block(
                code="LOG_EVIDENCE_UNAVAILABLE",
                reason="all evidence acquisition strategies failed",
                attempted_strategies=("job-logs", "artifact"),
                next_action="local-ci-reproduction",
                workspace_fingerprint="workspace-a",
            )
            self.assertEqual(store.payload["revision"], revision_before + 1)
            self.assertEqual(store.payload["status"], "BLOCKED")
            self.assertEqual(store.payload["checkpoints"][-1]["phase"], "LOG_EVIDENCE_UNAVAILABLE")
            self.assertEqual(store.payload["blockers"][-1]["next_action"], "local-ci-reproduction")
            self.assertFalse(store.completion_decision().eligible)
            store.checkpoint(
                status="RUNNING",
                phase="RESUMED_AFTER_BLOCKER",
                workspace_fingerprint="workspace-a",
                evidence_refs=["evidence/environment-restored.json"],
            )
            self.assertEqual(store.payload["status"], "RUNNING")

    def test_stable_task_id_is_deterministic(self) -> None:
        first = stable_task_id("repair", {"target": "a", "version": 1})
        second = stable_task_id("repair", {"version": 1, "target": "a"})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("repair-"))

    def test_concurrent_stale_writer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._store(root)
            second = self._store(root)
            first.set_metadata(owner="first")
            with self.assertRaises(TaskRunConflictError):
                second.set_metadata(owner="second")

    def test_json_schema_statuses_match_runtime_contract(self) -> None:
        schema = json.loads((ROOT / "governance/task-run.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], TASK_RUN_SCHEMA_VERSION)
        self.assertEqual(set(schema["properties"]["status"]["enum"]), RUN_STATUSES)
        self.assertIn("revision", schema["required"])
        self.assertIn("action_attempts", schema["required"])

    def test_persisted_payload_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertGreaterEqual(payload["revision"], 1)
            self.assertEqual(payload["status"], "CREATED")
            self.assertEqual(payload["checkpoints"][-1]["phase"], "CREATED")


if __name__ == "__main__":
    unittest.main()
