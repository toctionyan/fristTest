from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from langgraph_workflow_runtime import (  # type: ignore
    RUNTIME_STATUS_BLOCKED,
    RUNTIME_STATUS_END,
    RUNTIME_STATUS_WAITING_EXTERNAL,
    StepDispatchResult,
    build_langgraph_workflow,
    initial_workflow_state,
)
from workflow_activation import activate_workflow  # type: ignore
from workflow_registry import require_workflow  # type: ignore


class ScriptedDispatcher:
    def __init__(self, outcomes: dict[str, list[str]]) -> None:
        self.outcomes = {key: list(values) for key, values in outcomes.items()}
        self.calls: list[dict[str, object]] = []

    def run(self, *, step, state, capability_binding):
        history = self.outcomes.setdefault(step.step_id, [])
        if not history:
            raise AssertionError(f"no scripted outcome for {step.step_id}")
        outcome = history.pop(0)
        self.calls.append(
            {
                "step_id": step.step_id,
                "step_type": step.step_type,
                "use": step.use,
                "provider_id": capability_binding.provider_id if capability_binding else None,
            }
        )
        attempt = int((state.get("step_attempts") or {}).get(step.step_id) or 0) + 1
        kwargs = {}
        if step.step_id == "repair":
            kwargs["problem_ledger_ref"] = "ledger:repair-and-prove"
        return StepDispatchResult(
            outcome=outcome,
            evidence_refs=(f"evidence:{step.step_id}:{attempt}:{outcome}",),
            payload={"attempt": attempt, "outcome": outcome},
            **kwargs,
        )


class ExternalWaitDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, step, state, capability_binding):
        self.calls += 1
        self.provider_id = capability_binding.provider_id if capability_binding else None
        return StepDispatchResult(
            outcome="pending",
            evidence_refs=("evidence:ci-run-123",),
            external_wait={
                "provider": self.provider_id,
                "correlation_ref": "run-123",
                "resume_event": "ci.completed",
            },
        )


class AlwaysRedDispatcher:
    def run(self, *, step, state, capability_binding):
        outcome = "success" if step.step_id == "repair" else "red"
        return StepDispatchResult(
            outcome=outcome,
            evidence_refs=(f"evidence:{step.step_id}:{outcome}",),
        )


class LangGraphWorkflowRuntimeTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="langgraph-workflow-"))
        target = root / "skill-system/registry"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("capabilities.json", "executors.json", "integrations.json", "dev-workflows.json"):
            shutil.copy2(REGISTRY_DIR / name, target / name)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_repair_and_prove_loops_on_red_and_retains_attempt_history(self) -> None:
        workspace = self.workspace()
        workflow = require_workflow(workspace, "repair-and-prove")
        activation = activate_workflow(
            workspace,
            workflow_id="repair-and-prove",
            available_provider_ids=["local.workspace", "local.process"],
        )
        dispatcher = ScriptedDispatcher(
            {
                "repair": ["success", "success"],
                "focused-test": ["red", "green"],
                "adversarial": ["clean"],
                "quality": ["green"],
            }
        )
        graph = build_langgraph_workflow(
            workflow=workflow,
            activation=activation,
            dispatcher=dispatcher,
        )
        result = graph.invoke(
            initial_workflow_state(
                workflow_id="repair-and-prove",
                task_id="task-1",
                target_ref={"kind": "workspace", "ref": "candidate"},
            ),
            config={"recursion_limit": 50},
        )

        self.assertEqual(result["runtime_status"], RUNTIME_STATUS_END)
        self.assertEqual(result["next_action"], "EVALUATE_COMPLETION_POLICY")
        self.assertEqual(result["step_attempts"]["repair"], 2)
        self.assertEqual(result["step_attempts"]["focused-test"], 2)
        self.assertEqual(len(result["step_results"]["repair"]), 2)
        self.assertEqual(len(result["step_results"]["focused-test"]), 2)
        self.assertEqual(result["problem_ledger_ref"], "ledger:repair-and-prove")
        self.assertEqual(len(result["evidence_refs"]), 6)

        bound = {(row["step_id"], row["provider_id"]) for row in dispatcher.calls}
        self.assertIn(("focused-test", "local.process"), bound)
        self.assertIn(("quality", "local.process"), bound)
        self.assertIn(("repair", None), bound)
        self.assertIn(("adversarial", None), bound)

    def test_external_wait_yields_once_instead_of_polling(self) -> None:
        workspace = self.workspace()
        registry_path = workspace / "skill-system/registry/dev-workflows.json"
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        payload["workflows"].append(
            {
                "workflow_id": "ci-wait-test",
                "request_class": "STATUS_QUERY",
                "skills": [],
                "mode": "WAIT_EXTERNAL",
                "write_governed": False,
                "requirements": {
                    "capabilities": {
                        "required": ["ci.run.wait"],
                        "optional": [],
                    }
                },
                "graph": {
                    "start": "wait-ci",
                    "steps": {
                        "wait-ci": {
                            "type": "external_wait",
                            "use": "ci.run.wait",
                            "routes": {
                                "pending": "WAITING_EXTERNAL",
                                "blocked": "BLOCKED_UNRECOVERABLE",
                            },
                        }
                    },
                },
            }
        )
        registry_path.write_text(json.dumps(payload), encoding="utf-8")

        workflow = require_workflow(workspace, "ci-wait-test")
        activation = activate_workflow(
            workspace,
            workflow_id="ci-wait-test",
            available_provider_ids=["github.actions"],
        )
        dispatcher = ExternalWaitDispatcher()
        graph = build_langgraph_workflow(
            workflow=workflow,
            activation=activation,
            dispatcher=dispatcher,
        )
        result = graph.invoke(
            initial_workflow_state(
                workflow_id="ci-wait-test",
                task_id="task-ci",
                target_ref={"kind": "ci", "ref": "run-123"},
            )
        )

        self.assertEqual(dispatcher.calls, 1)
        self.assertEqual(dispatcher.provider_id, "github.actions")
        self.assertEqual(result["runtime_status"], RUNTIME_STATUS_WAITING_EXTERNAL)
        self.assertEqual(result["next_action"], "RESUME_ON_EXTERNAL_EVENT")
        self.assertEqual(result["external_wait"]["correlation_ref"], "run-123")

    def test_repair_loop_has_hard_attempt_budget_and_blocks_instead_of_running_forever(self) -> None:
        workspace = self.workspace()
        workflow = require_workflow(workspace, "repair-and-prove")
        activation = activate_workflow(
            workspace,
            workflow_id="repair-and-prove",
            available_provider_ids=["local.workspace", "local.process"],
        )
        graph = build_langgraph_workflow(
            workflow=workflow,
            activation=activation,
            dispatcher=AlwaysRedDispatcher(),
        )
        result = graph.invoke(
            initial_workflow_state(
                workflow_id="repair-and-prove",
                task_id="task-loop",
                target_ref={"kind": "workspace", "ref": "candidate"},
            ),
            config={"recursion_limit": 80},
        )

        self.assertEqual(result["runtime_status"], RUNTIME_STATUS_BLOCKED)
        self.assertEqual(result["next_action"], "INSPECT_BLOCKER")
        self.assertIn("exceeded max_attempts=8", result["runtime_error"])
        self.assertEqual(result["step_attempts"]["repair"], 9)
        self.assertEqual(result["step_attempts"]["focused-test"], 8)


if __name__ == "__main__":
    unittest.main()
