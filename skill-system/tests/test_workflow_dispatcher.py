from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import CapabilityBinding  # type: ignore
from langgraph_workflow_runtime import StepDispatchResult  # type: ignore
from workflow_dispatcher import (  # type: ignore
    CanonicalSkillInvocationAdapter,
    ProviderAdapterRegistry,
    SkillHostResult,
    WorkflowAdapterDispatcher,
    WorkflowDispatchError,
)
from workflow_graph_contract import WorkflowStepSpec  # type: ignore


class FakeSkillHost:
    def execute(self, *, skill_name, request_class, step, state):
        return SkillHostResult(
            outcome="success",
            output_schema="repair-plan@1",
            output_content='{"status":"PASS"}',
            output_evidence_ref="host:repair-plan",
            evidence_refs=("host:skill-executed",),
            payload={"skill": skill_name, "request_class": request_class},
            problem_ledger_ref="ledger:1",
        )


@dataclass
class FakeProviderAdapter:
    provider_id: str
    provider_type: str
    outcome: str = "green"
    external_wait: bool = False

    def __post_init__(self):
        self.calls = []

    def invoke(self, *, binding, step, state):
        self.calls.append((binding.capability_id, step.step_id))
        wait = None
        if self.external_wait:
            wait = {
                "provider": self.provider_id,
                "correlation_ref": "run-1",
                "resume_event": "ci.completed",
            }
        return StepDispatchResult(
            outcome=self.outcome,
            evidence_refs=(f"provider:{self.provider_id}:{binding.capability_id}",),
            external_wait=wait,
        )


class FakeWriteGuard:
    def __init__(self):
        self.calls = []

    def assert_allowed(self, *, binding, step, state):
        self.calls.append((binding.capability_id, step.step_id, state.get("task_id")))


class FakeHumanGateAdapter:
    def invoke(self, *, step, state):
        return StepDispatchResult(
            outcome="needs-human",
            evidence_refs=("human:gate-contract",),
            human_gate={"question": "Choose policy", "options": ["a", "b"]},
        )


class WorkflowDispatcherTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="workflow-dispatcher-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        skill = root / "skill-system/skills/repair/SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("# repair\n", encoding="utf-8")
        return root

    @staticmethod
    def step(step_type: str, use: str | None, *, step_id: str = "step") -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id=step_id,
            step_type=step_type,
            use=use,
            routes={"success": "END", "green": "END", "pending": "WAITING_EXTERNAL", "needs-human": "HUMAN_GATE"},
            max_attempts=8,
        )

    @staticmethod
    def binding(
        capability_id: str,
        *,
        provider_id: str = "local.process",
        provider_type: str = "executor",
        mutates: bool = False,
        external_wait: bool = False,
    ) -> CapabilityBinding:
        return CapabilityBinding(
            capability_id=capability_id,
            provider_id=provider_id,
            provider_type=provider_type,
            activation_key=provider_id,
            mutates=mutates,
            external_wait=external_wait,
        )

    def test_skill_dispatch_writes_existing_canonical_invocation_receipt(self) -> None:
        workspace = self.workspace()
        adapter = CanonicalSkillInvocationAdapter(
            workspace=workspace,
            request_class="REPAIR",
            host=FakeSkillHost(),
        )
        dispatcher = WorkflowAdapterDispatcher(skill_adapter=adapter)
        result = dispatcher.run(
            step=self.step("skill", "repair", step_id="repair"),
            state={
                "workflow_id": "repair-and-prove",
                "task_id": "task-1",
                "change_id": "change-1",
                "step_attempts": {},
            },
            capability_binding=None,
        )

        self.assertEqual(result.outcome, "success")
        self.assertEqual(result.problem_ledger_ref, "ledger:1")
        receipt_refs = [ref for ref in result.evidence_refs if ref.startswith("file:.quality/skill-invocations/")]
        self.assertEqual(len(receipt_refs), 1)
        receipt_path = workspace / receipt_refs[0].removeprefix("file:")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], "skill-invocation-receipt@1")
        self.assertEqual(receipt["selected_skill"], "repair")
        self.assertEqual(receipt["subject"]["task_id"], "task-1")
        self.assertEqual(receipt["subject"]["change_id"], "change-1")
        self.assertFalse(receipt["authority_effect"])

    def test_dispatcher_uses_provider_selected_by_capability_binding(self) -> None:
        local = FakeProviderAdapter("local.process", "executor")
        github = FakeProviderAdapter("github.actions", "integration", external_wait=True, outcome="pending")
        registry = ProviderAdapterRegistry([local, github])
        dispatcher = WorkflowAdapterDispatcher(provider_adapters=registry)

        local_result = dispatcher.run(
            step=self.step("executor", "test.run", step_id="focused-test"),
            state={"task_id": "task-1"},
            capability_binding=self.binding("test.run"),
        )
        wait_result = dispatcher.run(
            step=self.step("external_wait", "ci.run.wait", step_id="wait-ci"),
            state={"task_id": "task-1"},
            capability_binding=self.binding(
                "ci.run.wait",
                provider_id="github.actions",
                provider_type="integration",
                external_wait=True,
            ),
        )

        self.assertEqual(local_result.outcome, "green")
        self.assertEqual(local.calls, [("test.run", "focused-test")])
        self.assertEqual(wait_result.outcome, "pending")
        self.assertEqual(wait_result.external_wait["provider"], "github.actions")
        self.assertEqual(github.calls, [("ci.run.wait", "wait-ci")])

    def test_registered_capability_binding_without_runtime_adapter_fails_closed(self) -> None:
        dispatcher = WorkflowAdapterDispatcher(provider_adapters=ProviderAdapterRegistry())
        with self.assertRaisesRegex(WorkflowDispatchError, "no runtime adapter"):
            dispatcher.run(
                step=self.step("executor", "test.run"),
                state={"task_id": "task-1"},
                capability_binding=self.binding("test.run"),
            )

    def test_mutating_capability_does_not_gain_write_authority_from_binding(self) -> None:
        adapter = FakeProviderAdapter("local.workspace", "executor")
        dispatcher = WorkflowAdapterDispatcher(
            provider_adapters=ProviderAdapterRegistry([adapter])
        )
        with self.assertRaisesRegex(WorkflowDispatchError, "requires existing write authority"):
            dispatcher.run(
                step=self.step("executor", "workspace.write"),
                state={"task_id": "task-1"},
                capability_binding=self.binding(
                    "workspace.write",
                    provider_id="local.workspace",
                    mutates=True,
                ),
            )
        self.assertEqual(adapter.calls, [])

    def test_mutating_capability_calls_existing_write_guard_before_provider(self) -> None:
        adapter = FakeProviderAdapter("local.workspace", "executor")
        guard = FakeWriteGuard()
        dispatcher = WorkflowAdapterDispatcher(
            provider_adapters=ProviderAdapterRegistry([adapter]),
            write_authority_guard=guard,
        )
        result = dispatcher.run(
            step=self.step("executor", "workspace.write", step_id="apply-patch"),
            state={"task_id": "task-1"},
            capability_binding=self.binding(
                "workspace.write",
                provider_id="local.workspace",
                mutates=True,
            ),
        )
        self.assertEqual(result.outcome, "green")
        self.assertEqual(guard.calls, [("workspace.write", "apply-patch", "task-1")])
        self.assertEqual(adapter.calls, [("workspace.write", "apply-patch")])

    def test_provider_adapter_type_mismatch_fails_closed(self) -> None:
        registry = ProviderAdapterRegistry([FakeProviderAdapter("github.actions", "executor")])
        dispatcher = WorkflowAdapterDispatcher(provider_adapters=registry)
        with self.assertRaisesRegex(WorkflowDispatchError, "type mismatch"):
            dispatcher.run(
                step=self.step("external_wait", "ci.run.wait"),
                state={"task_id": "task-1"},
                capability_binding=self.binding(
                    "ci.run.wait",
                    provider_id="github.actions",
                    provider_type="integration",
                    external_wait=True,
                ),
            )

    def test_external_wait_step_rejects_non_wait_capability(self) -> None:
        registry = ProviderAdapterRegistry([FakeProviderAdapter("local.process", "executor")])
        dispatcher = WorkflowAdapterDispatcher(provider_adapters=registry)
        with self.assertRaisesRegex(WorkflowDispatchError, "external-wait capability"):
            dispatcher.run(
                step=self.step("external_wait", "test.run"),
                state={"task_id": "task-1"},
                capability_binding=self.binding("test.run"),
            )

    def test_human_gate_is_injected_and_not_a_capability_provider(self) -> None:
        dispatcher = WorkflowAdapterDispatcher(human_gate_adapter=FakeHumanGateAdapter())
        result = dispatcher.run(
            step=self.step("human_gate", None, step_id="policy-choice"),
            state={"task_id": "task-1"},
            capability_binding=None,
        )
        self.assertEqual(result.outcome, "needs-human")
        self.assertEqual(result.human_gate["options"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
