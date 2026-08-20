from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from langgraph_workflow_runtime import (  # type: ignore
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_HUMAN_GATE,
    RUNTIME_STATUS_WAITING_EXTERNAL,
)
from task_run import TaskRunStore  # type: ignore
from workflow_taskrun_bridge import (  # type: ignore
    checkpoint_workflow_start,
    checkpoint_workflow_state,
)


class WorkflowTaskRunBridgeTest(unittest.TestCase):
    def store(self, name: str) -> TaskRunStore:
        root = Path(tempfile.mkdtemp(prefix="workflow-taskrun-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        return TaskRunStore.open_or_create(
            root / f"{name}.json",
            task_id=f"task-{name}",
            task_kind="workflow-test",
            binding={"target": name},
            required_conditions=["quality-green", "problems-closed"],
            current_workspace_fingerprint="fp-1",
        )

    def test_graph_end_moves_taskrun_to_validating_not_completed(self) -> None:
        store = self.store("end")
        checkpoint_workflow_start(
            store,
            workflow_id="repair-and-prove",
            workspace_fingerprint="fp-1",
            evidence_refs=["workflow:activated"],
        )
        checkpoint_workflow_state(
            store,
            state={
                "workflow_id": "repair-and-prove",
                "runtime_status": RUNTIME_STATUS_END,
                "current_stage": "quality",
                "next_action": "EVALUATE_COMPLETION_POLICY",
                "evidence_refs": ["quality:green"],
                "problem_ledger_ref": "ledger:1",
            },
            workspace_fingerprint="fp-1",
        )

        self.assertEqual(store.payload["status"], "VALIDATING")
        self.assertEqual(store.payload["phase"], "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY")
        self.assertFalse(store.completion_decision().eligible)
        self.assertFalse(store.payload["conditions"]["quality-green"]["satisfied"])
        self.assertFalse(store.payload["conditions"]["problems-closed"]["satisfied"])
        latest = store.payload["checkpoints"][-1]
        self.assertFalse(latest["metadata"]["graph_can_complete_task"])
        self.assertFalse(latest["metadata"]["completion_authority_changed"])

    def test_external_wait_maps_to_durable_waiting_external_result(self) -> None:
        store = self.store("wait")
        checkpoint_workflow_start(
            store,
            workflow_id="ci-workflow",
            workspace_fingerprint="fp-1",
        )
        checkpoint_workflow_state(
            store,
            state={
                "workflow_id": "ci-workflow",
                "runtime_status": RUNTIME_STATUS_WAITING_EXTERNAL,
                "current_stage": "wait-ci",
                "next_action": "RESUME_ON_EXTERNAL_EVENT",
                "evidence_refs": ["ci:run-123"],
                "external_wait": {
                    "provider": "github.actions",
                    "correlation_ref": "run-123",
                    "resume_event": "ci.completed",
                },
            },
            workspace_fingerprint="fp-1",
        )

        self.assertEqual(store.payload["status"], "WAITING_EXTERNAL_RESULT")
        self.assertEqual(store.payload["phase"], "WORKFLOW_WAITING_EXTERNAL")
        latest = store.payload["checkpoints"][-1]
        self.assertEqual(latest["metadata"]["external_wait"]["correlation_ref"], "run-123")

    def test_human_gate_maps_to_blocked_with_explicit_human_required_contract(self) -> None:
        store = self.store("human")
        checkpoint_workflow_start(
            store,
            workflow_id="policy-decision",
            workspace_fingerprint="fp-1",
        )
        checkpoint_workflow_state(
            store,
            state={
                "workflow_id": "policy-decision",
                "runtime_status": RUNTIME_STATUS_HUMAN_GATE,
                "current_stage": "policy-choice",
                "next_action": "AWAIT_HUMAN_DECISION",
                "evidence_refs": ["decision:conflict"],
                "human_gate": {
                    "question": "Choose the authoritative refund policy.",
                    "options": ["7-days", "15-days"],
                },
            },
            workspace_fingerprint="fp-1",
        )

        self.assertEqual(store.payload["status"], "BLOCKED")
        self.assertEqual(store.payload["phase"], "WORKFLOW_HUMAN_GATE")
        latest = store.payload["checkpoints"][-1]
        self.assertTrue(latest["metadata"]["human_required"])
        self.assertEqual(latest["metadata"]["human_gate"]["options"], ["7-days", "15-days"])


if __name__ == "__main__":
    unittest.main()
