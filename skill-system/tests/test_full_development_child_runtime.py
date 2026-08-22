from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import InMemorySaver

SKILL_SYSTEM = Path(__file__).resolve().parents[1]
CONTROLLER = SKILL_SYSTEM / "controller"
if str(SKILL_SYSTEM) not in sys.path:
    sys.path.insert(0, str(SKILL_SYSTEM))
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from full_development_child_runtime import FullDevelopmentChildRuntime  # type: ignore  # noqa: E402
from full_development_child_runtime import FullDevelopmentChildRuntimeError  # type: ignore  # noqa: E402
from langgraph_workflow_runtime import (  # type: ignore  # noqa: E402
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    StepDispatchResult,
)
from runtime import HarnessRuntimeEngine, HarnessRuntimeStatus  # type: ignore  # noqa: E402
from task_run import TaskRunStore  # type: ignore  # noqa: E402


ROOT = SKILL_SYSTEM.parent


class RepairDispatcher:
    outcomes = {
        "repair": "success",
        "focused-test": "green",
        "adversarial": "clean",
        "quality": "green",
    }

    def run(self, *, step, state, capability_binding):
        return StepDispatchResult(
            outcome=self.outcomes[step.step_id],
            evidence_refs=(f"evidence:{step.step_id}:pass",),
        )


class PublicationDispatcher:
    def __init__(self, *, wait_for_ci: bool) -> None:
        self.wait_for_ci = wait_for_ci

    def run(self, *, step, state, capability_binding):
        if step.step_id == "wait-ci" and self.wait_for_ci and not state.get("external_event"):
            return StepDispatchResult(
                outcome="pending",
                evidence_refs=("evidence:ci:pending",),
                external_wait={
                    "provider": capability_binding.provider_id,
                    "correlation_ref": "run-2088",
                    "resume_event": "ci.completed",
                },
            )
        return StepDispatchResult(
            outcome="green",
            evidence_refs=(f"evidence:{step.step_id}:green",),
        )


class FullDevelopmentChildRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="full-development-child-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.connection = sqlite3.connect(
            self.root / "langgraph-checkpoints.sqlite",
            check_same_thread=False,
        )
        self.addCleanup(self.connection.close)
        self.checkpointer = SqliteSaver(self.connection)

    def store(self, name: str) -> TaskRunStore:
        return TaskRunStore.open_or_create(
            self.root / f"{name}.json",
            task_id=f"task-{name}",
            task_kind="harness-full-dev",
            binding={"target": name},
            required_conditions=["quality-green", "problems-closed"],
            current_workspace_fingerprint="fp-1",
        )

    def runtime(self, *, store: TaskRunStore, dispatcher) -> FullDevelopmentChildRuntime:
        return FullDevelopmentChildRuntime(
            workspace=ROOT,
            composition_id="harness-full-dev-github",
            dispatcher=dispatcher,
            checkpointer=self.checkpointer,
            taskrun_store=store,
            workspace_fingerprint="fp-1",
        )

    def test_child_end_advances_parent_without_validating_or_completing_taskrun(self) -> None:
        store = self.store("repair")
        parent = HarnessRuntimeEngine().start(
            task_id="task-repair",
            workflow_id="harness-full-dev",
            start_step="repair-and-prove",
        )
        result = self.runtime(store=store, dispatcher=RepairDispatcher()).invoke(
            parent_state=parent,
            target_ref={"kind": "workspace", "ref": "candidate"},
        )

        self.assertEqual(result.child_state["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(result.parent_state.status, HarnessRuntimeStatus.RUNNING)
        self.assertEqual(result.parent_state.current_step, "publication-e2e")
        self.assertEqual(store.payload["status"], "RUNNING")
        self.assertEqual(store.payload["phase"], "CHILD_WORKFLOW_ENDED")
        self.assertNotEqual(store.payload["status"], "COMPLETED")
        latest = store.payload["checkpoints"][-1]
        self.assertEqual(latest["metadata"]["parent_workflow_id"], "harness-full-dev")
        self.assertFalse(latest["metadata"]["graph_can_complete_task"])

        persisted = self.checkpointer.get_tuple(
            {"configurable": {"thread_id": result.thread_id}}
        )
        self.assertIsNotNone(persisted)

    def test_in_memory_checkpointer_is_rejected_as_non_durable(self) -> None:
        store = self.store("memory")
        with self.assertRaisesRegex(FullDevelopmentChildRuntimeError, "durable checkpointer"):
            FullDevelopmentChildRuntime(
                workspace=ROOT,
                composition_id="harness-full-dev-github",
                dispatcher=RepairDispatcher(),
                checkpointer=InMemorySaver(),
                taskrun_store=store,
                workspace_fingerprint="fp-1",
            )

    def test_only_final_parent_end_moves_taskrun_to_validating(self) -> None:
        store = self.store("publication")
        parent = HarnessRuntimeEngine().start(
            task_id="task-publication",
            workflow_id="harness-full-dev",
            start_step="publication-e2e",
        )
        result = self.runtime(
            store=store,
            dispatcher=PublicationDispatcher(wait_for_ci=False),
        ).invoke(
            parent_state=parent,
            target_ref={"kind": "repository", "ref": "candidate"},
        )

        self.assertEqual(result.child_state["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(result.parent_state.status, HarnessRuntimeStatus.FLOW_ENDED)
        self.assertEqual(store.payload["status"], "VALIDATING")
        self.assertEqual(
            store.payload["phase"],
            "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY",
        )
        self.assertFalse(store.completion_decision().eligible)

    def test_external_wait_resumes_same_child_with_durable_event_evidence(self) -> None:
        store = self.store("resume")
        parent = HarnessRuntimeEngine().start(
            task_id="task-resume",
            workflow_id="harness-full-dev",
            start_step="publication-e2e",
        )
        runtime = self.runtime(
            store=store,
            dispatcher=PublicationDispatcher(wait_for_ci=True),
        )
        waiting = runtime.invoke(
            parent_state=parent,
            target_ref={"kind": "repository", "ref": "candidate"},
        )

        self.assertEqual(waiting.child_state["runtime_status"], RUNTIME_STATUS_WAITING_EXTERNAL)
        self.assertEqual(waiting.parent_state.status, HarnessRuntimeStatus.WAITING_EXTERNAL)
        self.assertEqual(store.payload["status"], "WAITING_EXTERNAL_RESULT")

        resumed = runtime.resume(
            parent_state=waiting.parent_state,
            child_state=waiting.child_state,
            external_event={
                "event": "ci.completed",
                "status": "success",
                "correlation_ref": "run-2088",
                "evidence_refs": ["event:ci.completed:run-2088"],
            },
            evidence_refs=("event:ci.completed:run-2088",),
            correlation_ref="run-2088",
        )

        self.assertEqual(resumed.child_state["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(resumed.parent_state.status, HarnessRuntimeStatus.FLOW_ENDED)
        self.assertEqual(store.payload["status"], "VALIDATING")
        phases = [row["phase"] for row in store.payload["checkpoints"]]
        self.assertIn("WORKFLOW_WAITING_EXTERNAL", phases)
        self.assertIn("WORKFLOW_RUNTIME_RESUMED", phases)
        self.assertIn("CHILD_WORKFLOW_ENDED", phases)


if __name__ == "__main__":
    unittest.main()
