from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from durable_external_event_scheduler import (  # type: ignore  # noqa: E402
    DurableExternalEventScheduler,
    DurableExternalEventSchedulerError,
    validate_ingest_request,
)


class FakeExternalWaitOrchestrator:
    host_id = "codex"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.sessions: dict[str, dict[str, object]] = {}
        self.resume_calls = 0
        self.reconcile_calls = 0
        self.crash_after_claim = False

    @staticmethod
    def _wait(correlation: str) -> dict[str, object]:
        return {
            "provider": "github.actions",
            "correlation_ref": correlation,
            "resume_event": "ci.completed",
            "authority_effect": False,
        }

    def add_wait(self, session_id: str, *, correlation: str) -> dict[str, object]:
        task_id = f"task-{session_id}"
        workflow_id = "customer-agent-repair-with-ci"
        wait = self._wait(correlation)
        taskrun_ref = f"file:.harness/taskruns/{task_id}.json"
        taskrun = {
            "schema_version": 1,
            "task_id": task_id,
            "status": "WAITING_EXTERNAL_RESULT",
            "phase": "WORKFLOW_WAITING_EXTERNAL",
            "checkpoints": [
                {
                    "sequence": 1,
                    "status": "WAITING_EXTERNAL_RESULT",
                    "phase": "WORKFLOW_WAITING_EXTERNAL",
                    "evidence_refs": [f"github-run:{correlation}:pending"],
                    "metadata": {
                        "workflow_id": workflow_id,
                        "external_wait": wait,
                    },
                }
            ],
        }
        path = self.workspace / taskrun_ref.removeprefix("file:")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(taskrun, indent=2), encoding="utf-8")
        session: dict[str, object] = {
            "session_id": session_id,
            "host_id": self.host_id,
            "task_id": task_id,
            "taskrun_ref": taskrun_ref,
            "revision": 7,
            "phase": "WAITING_EXTERNAL",
            "runtime_state": {
                "task_id": task_id,
                "workflow_id": workflow_id,
                "runtime_status": "WAITING_EXTERNAL",
                "external_wait": wait,
            },
            "taskrun_status": "WAITING_EXTERNAL_RESULT",
            "taskrun_phase": "WORKFLOW_WAITING_EXTERNAL",
            "pending_transition": None,
            "next_action": {"kind": "WAIT_EXTERNAL_EVENT"},
        }
        self.sessions[session_id] = session
        return copy.deepcopy(session)

    def read(self, session_id: str) -> dict[str, object]:
        return copy.deepcopy(self.sessions[session_id])

    def _complete(
        self,
        session_id: str,
        *,
        event: dict[str, object],
        evidence_refs: list[str],
        correlation_ref: str,
        revision_increment: int,
    ) -> dict[str, object]:
        session = self.sessions[session_id]
        taskrun = json.loads(
            (self.workspace / str(session["taskrun_ref"]).removeprefix("file:")).read_text(
                encoding="utf-8"
            )
        )
        taskrun["checkpoints"].append(
            {
                "sequence": len(taskrun["checkpoints"]) + 1,
                "status": "RUNNING",
                "phase": "WORKFLOW_RUNTIME_RESUMED",
                "evidence_refs": list(evidence_refs),
                "metadata": {
                    "workflow_id": session["runtime_state"]["workflow_id"],
                    "resume_kind": "EXTERNAL_EVENT",
                    "correlation_ref": correlation_ref,
                    "authority_effect": False,
                },
            }
        )
        taskrun["checkpoints"].append(
            {
                "sequence": len(taskrun["checkpoints"]) + 1,
                "status": "VALIDATING",
                "phase": "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY",
                "evidence_refs": list(evidence_refs),
                "metadata": {
                    "workflow_id": session["runtime_state"]["workflow_id"],
                    "graph_can_complete_task": False,
                },
            }
        )
        taskrun["status"] = "VALIDATING"
        taskrun["phase"] = "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY"
        (self.workspace / str(session["taskrun_ref"]).removeprefix("file:")).write_text(
            json.dumps(taskrun, indent=2), encoding="utf-8"
        )
        session["revision"] = int(session["revision"]) + revision_increment
        session["phase"] = "VALIDATING"
        session["runtime_state"] = {
            "task_id": session["task_id"],
            "workflow_id": session["runtime_state"]["workflow_id"],
            "runtime_status": "WORKFLOW_END",
        }
        session["taskrun_status"] = "VALIDATING"
        session["taskrun_phase"] = "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY"
        session["pending_transition"] = None
        session["next_action"] = {"kind": "EVALUATE_COMPLETION_POLICY"}
        return copy.deepcopy(session)

    def resume_external(
        self,
        *,
        session_id: str,
        expected_revision: int,
        event: dict[str, object],
        evidence_refs: list[str],
        correlation_ref: str,
    ) -> dict[str, object]:
        self.resume_calls += 1
        session = self.sessions[session_id]
        if session["revision"] != expected_revision:
            raise RuntimeError("revision conflict")
        session["revision"] = expected_revision + 1
        session["phase"] = "RESUMING_EXTERNAL"
        session["pending_transition"] = {
            "kind": "EXTERNAL_EVENT",
            "input": {"event": copy.deepcopy(event)},
            "evidence_refs": list(evidence_refs),
            "correlation_ref": correlation_ref,
        }
        session["next_action"] = {"kind": "RESUMING_EXTERNAL"}
        if self.crash_after_claim:
            raise RuntimeError("simulated process crash after durable claim")
        return self._complete(
            session_id,
            event=event,
            evidence_refs=evidence_refs,
            correlation_ref=correlation_ref,
            revision_increment=1,
        )

    def reconcile(self, *, session_id: str, expected_revision: int) -> dict[str, object]:
        self.reconcile_calls += 1
        session = self.sessions[session_id]
        if session["revision"] != expected_revision or session["phase"] != "RESUMING_EXTERNAL":
            raise RuntimeError("not reconcilable")
        pending = session["pending_transition"]
        return self._complete(
            session_id,
            event=pending["input"]["event"],
            evidence_refs=pending["evidence_refs"],
            correlation_ref=pending["correlation_ref"],
            revision_increment=2,
        )


class SchedulerExternalEventWakeupBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="external-event-scheduler-"))
        self.addCleanup(lambda: shutil.rmtree(self.workspace, ignore_errors=True))
        self.orchestrator = FakeExternalWaitOrchestrator(self.workspace)
        self.scheduler = DurableExternalEventScheduler(
            workspace=self.workspace,
            orchestrator=self.orchestrator,
            max_events_per_run=10,
        )

    @staticmethod
    def event(correlation: str, *, conclusion: str = "success") -> dict[str, object]:
        return {
            "provider": "github.actions",
            "correlation_ref": correlation,
            "event": "ci.completed",
            "conclusion": conclusion,
            "evidence_refs": [f"github-run:{correlation}:{conclusion}"],
        }

    def test_exact_event_wakes_once_and_duplicate_returns_same_receipt(self) -> None:
        self.orchestrator.add_wait("session-1", correlation="run-101")
        queued = self.scheduler.ingest(
            session_id="session-1", event=self.event("run-101")
        )
        first = self.scheduler.wake(event_ref=queued["event_ref"])
        second = self.scheduler.wake(event_ref=queued["event_ref"])
        self.assertEqual(first["status"], "DELIVERED")
        self.assertEqual(second, first)
        self.assertEqual(self.orchestrator.resume_calls, 1)
        self.assertEqual(
            self.orchestrator.read("session-1")["phase"], "VALIDATING"
        )
        receipt = json.loads(
            (self.workspace / first["receipt_ref"].removeprefix("file:")).read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(receipt["authority_effect"])
        self.assertFalse(receipt["completion_authority_changed"])
        self.assertFalse(receipt["merge_authority_changed"])

    def test_mismatched_or_undurable_event_never_enters_inbox(self) -> None:
        self.orchestrator.add_wait("session-1", correlation="run-101")
        cases = [
            {**self.event("run-101"), "provider": "gitlab.ci"},
            {**self.event("run-999")},
            {**self.event("run-101"), "event": "deployment.completed"},
            {**self.event("run-101"), "evidence_refs": []},
        ]
        for event in cases:
            with self.assertRaises(DurableExternalEventSchedulerError):
                self.scheduler.ingest(session_id="session-1", event=event)
        self.assertFalse(self.scheduler.event_root.exists())

    def test_competing_events_for_one_wait_are_serialized_and_second_is_stale(self) -> None:
        self.orchestrator.add_wait("session-1", correlation="run-101")
        green = self.scheduler.ingest(
            session_id="session-1", event=self.event("run-101", conclusion="success")
        )
        red = self.scheduler.ingest(
            session_id="session-1", event=self.event("run-101", conclusion="failure")
        )
        barrier = threading.Barrier(2)
        results: list[dict[str, object]] = []

        def wake(ref: str) -> None:
            barrier.wait(timeout=5)
            results.append(self.scheduler.wake(event_ref=ref))

        threads = [
            threading.Thread(target=wake, args=(green["event_ref"],)),
            threading.Thread(target=wake, args=(red["event_ref"],)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(self.orchestrator.resume_calls, 1)
        self.assertEqual(
            sorted(row["status"] for row in results),
            ["DELIVERED", "REJECTED_STALE"],
        )

    def test_interrupted_claim_resumes_only_through_existing_reconcile(self) -> None:
        self.orchestrator.add_wait("session-1", correlation="run-101")
        queued = self.scheduler.ingest(
            session_id="session-1", event=self.event("run-101")
        )
        self.orchestrator.crash_after_claim = True
        with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
            self.scheduler.wake(event_ref=queued["event_ref"])
        self.assertEqual(
            self.orchestrator.read("session-1")["phase"], "RESUMING_EXTERNAL"
        )
        self.orchestrator.crash_after_claim = False
        recovered = self.scheduler.wake(event_ref=queued["event_ref"])
        self.assertEqual(recovered["status"], "DELIVERED")
        self.assertEqual(self.orchestrator.resume_calls, 1)
        self.assertEqual(self.orchestrator.reconcile_calls, 1)
        receipt = json.loads(
            (self.workspace / recovered["receipt_ref"].removeprefix("file:")).read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(receipt["recovered"])
        self.assertEqual(receipt["delivery_method"], "RECONCILE")

    def test_crash_after_host_success_recovers_from_exact_taskrun_evidence(self) -> None:
        self.orchestrator.add_wait("session-1", correlation="run-101")
        queued = self.scheduler.ingest(
            session_id="session-1", event=self.event("run-101")
        )
        original = self.scheduler._write_receipt
        failed = False

        def crash_once(**kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("simulated crash before receipt")
            return original(**kwargs)

        self.scheduler._write_receipt = crash_once  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "before receipt"):
            self.scheduler.wake(event_ref=queued["event_ref"])
        self.scheduler._write_receipt = original  # type: ignore[method-assign]
        recovered = self.scheduler.wake(event_ref=queued["event_ref"])
        self.assertEqual(recovered["status"], "DELIVERED")
        self.assertEqual(self.orchestrator.resume_calls, 1)
        receipt = json.loads(
            (self.workspace / recovered["receipt_ref"].removeprefix("file:")).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            receipt["delivery_method"], "RECOVERED_FROM_TASKRUN_EVIDENCE"
        )

    def test_tampered_event_and_outside_reference_fail_closed(self) -> None:
        self.orchestrator.add_wait("session-1", correlation="run-101")
        queued = self.scheduler.ingest(
            session_id="session-1", event=self.event("run-101")
        )
        event_path = self.workspace / queued["event_ref"].removeprefix("file:")
        payload = json.loads(event_path.read_text(encoding="utf-8"))
        payload["event"]["conclusion"] = "failure"
        event_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(
            DurableExternalEventSchedulerError, "identity|fingerprint"
        ):
            self.scheduler.wake(event_ref=queued["event_ref"])
        outside = self.workspace / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
            DurableExternalEventSchedulerError, "outside"
        ):
            self.scheduler.wake(event_ref="file:outside.json")

    def test_run_once_is_bounded_and_never_polls_a_provider(self) -> None:
        self.orchestrator.add_wait("session-1", correlation="run-101")
        self.orchestrator.add_wait("session-2", correlation="run-202")
        self.scheduler.ingest(
            session_id="session-1", event=self.event("run-101")
        )
        self.scheduler.ingest(
            session_id="session-2", event=self.event("run-202")
        )
        bounded = DurableExternalEventScheduler(
            workspace=self.workspace,
            orchestrator=self.orchestrator,
            max_events_per_run=1,
        )
        result = bounded.run_once()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["limit"], 1)
        self.assertFalse(result["provider_polling"])
        self.assertEqual(self.orchestrator.resume_calls, 1)

    def test_ingest_contract_schema_and_root_cli_are_portable(self) -> None:
        request = validate_ingest_request(
            {
                "schema": "external-event-ingest-request@1",
                "host_id": "codex",
                "session_id": "session-1",
                "event": self.event("run-101"),
                "authority_effect": False,
            }
        )
        schema = json.loads(
            (ROOT / "skill-system/schemas/external-event-wakeup.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(request["schema"], "external-event-ingest-request@1")
        self.assertIn("oneOf", schema)
        self.assertIn("ingestRequest", schema["$defs"])
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / "skillctl.py"), "scheduler", "--help"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("run-once", completed.stdout)
        self.assertNotIn("poll", completed.stdout.lower().replace("polling", ""))


if __name__ == "__main__":
    unittest.main()
