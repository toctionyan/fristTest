from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

SKILL_SYSTEM = Path(__file__).resolve().parents[1]
CONTROLLER = SKILL_SYSTEM / "controller"
ROOT = SKILL_SYSTEM.parent
STARTER = SKILL_SYSTEM / "starters" / "customer-agent"
for search_path in (CONTROLLER, SKILL_SYSTEM):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from host_skill_bridge import (  # type: ignore  # noqa: E402
    HOST_RESULT_SCHEMA,
    HOST_TOOL_RECEIPT_SCHEMA,
)
from langgraph_workflow_runtime import StepDispatchResult  # type: ignore  # noqa: E402
from starter_host_orchestrator import (  # type: ignore  # noqa: E402
    PHASE_AWAITING_CONFIRMATION,
    PHASE_BLOCKED,
    PHASE_HUMAN_GATE,
    PHASE_READY_TO_START,
    PHASE_RESUMING_EXTERNAL,
    PHASE_STARTING,
    PHASE_VALIDATING,
    PHASE_WAITING_EXTERNAL,
    PHASE_WAITING_HOST,
    StarterHostOrchestrationError,
    StarterHostOrchestrator,
    project_runtime_action,
)
from starter_runtime import (  # type: ignore  # noqa: E402
    STARTER_HOST_CONFIRMATION_SCHEMA,
    STARTER_HOST_SELECTION_SCHEMA,
    StarterWorkflowRuntime,
    register_starter_runtime,
)
from workflow_dispatcher import ProviderAdapterRegistry  # type: ignore  # noqa: E402
from workflow_taskrun_bridge import checkpoint_workflow_resume  # type: ignore  # noqa: E402


class GreenLocalProcessAdapter:
    provider_id = "local.process"
    provider_type = "executor"

    def invoke(self, *, binding, step, state):
        return StepDispatchResult(
            outcome="green",
            evidence_refs=(f"test:green:{state['task_id']}:{step.step_id}",),
            payload={
                "schema": "test-provider-result@1",
                "capability_id": binding.capability_id,
                "authority_effect": False,
            },
        )


class EventDrivenCIAdapter:
    provider_id = "github.actions"
    provider_type = "integration"

    def invoke(self, *, binding, step, state):
        event = state.get("external_event") or {}
        if (
            event.get("event") == "ci.completed"
            and event.get("correlation_ref") == "ci-host-session-1"
            and event.get("status") == "success"
        ):
            return StepDispatchResult(
                outcome="green",
                evidence_refs=("ci:run:ci-host-session-1:green",),
                payload={"status": "success", "authority_effect": False},
            )
        return StepDispatchResult(
            outcome="pending",
            evidence_refs=("ci:run:ci-host-session-1:pending",),
            external_wait={
                "correlation_ref": "ci-host-session-1",
                "resume_event": "ci.completed",
                "authority_effect": False,
            },
        )


class ExplicitHumanGateAdapter:
    def invoke(self, *, step, state):
        decision = state.get("human_decision") or {}
        if decision.get("decision") == "approve":
            return StepDispatchResult(
                outcome="approved",
                evidence_refs=("human:decision:approve",),
                payload={"decision": "approve", "authority_effect": False},
            )
        return StepDispatchResult(
            outcome="needs-human",
            evidence_refs=("human:gate:policy-choice",),
            human_gate={
                "gate_id": "policy-choice",
                "question": "Approve the explicit policy?",
                "options": ["approve", "reject"],
                "authority_effect": False,
            },
        )


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterStartRuntime(StarterWorkflowRuntime):
    def start(self, *, target_ref):
        super().start(target_ref=target_ref)
        raise SimulatedProcessCrash("after durable start execution")


class CrashAfterResumeRuntime(StarterWorkflowRuntime):
    def resume(self, **kwargs):
        super().resume(**kwargs)
        raise SimulatedProcessCrash("after durable resume execution")


class CrashAfterTaskRunResumeRuntime(StarterWorkflowRuntime):
    def resume(self, **kwargs):
        checkpoint_workflow_resume(
            self.store,
            workflow_id=self.resolved.workflow.workflow_id,
            resume_kind="EXTERNAL_EVENT",
            workspace_fingerprint=self.workspace_fingerprint,
            evidence_refs=kwargs["evidence_refs"],
            correlation_ref=kwargs.get("correlation_ref"),
        )
        raise SimulatedProcessCrash("after TaskRun resume before graph checkpoint")


class StarterHostOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="starter-host-orchestrator-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.package = self.project / ".harness/customer-agent"
        self.package.parent.mkdir(parents=True)
        shutil.copytree(STARTER, self.package)
        self.install_boundary_test_workflows()
        self.registration = self.project / ".harness/runtime/customer-agent.registration.json"
        register_starter_runtime(
            project_workspace=self.project,
            starter_directory=self.package,
            output=self.registration,
            registry_workspace=ROOT,
        )
        self.connection = sqlite3.connect(
            self.project / ".harness/runtime/host-sessions.sqlite",
            check_same_thread=False,
        )
        self.addCleanup(self.connection.close)
        self.orchestrator = StarterHostOrchestrator(
            registry_workspace=ROOT,
            project_workspace=self.project,
            registration=self.registration,
            host_id="codex",
            provider_adapters=ProviderAdapterRegistry(
                [GreenLocalProcessAdapter(), EventDrivenCIAdapter()]
            ),
            checkpointer=SqliteSaver(self.connection),
            workspace_fingerprint="fp-host-session-1",
            human_gate_adapter=ExplicitHumanGateAdapter(),
        )
        self.schema = json.loads(
            (SKILL_SYSTEM / "schemas/starter-host-session.schema.json").read_text(
                encoding="utf-8"
            )
        )

    def install_boundary_test_workflows(self) -> None:
        workflows = {
            "customer-agent-ci-wait-test.json": {
                "schema": "harness-workflow@1",
                "id": "customer-agent-ci-wait-test",
                "version": "1.0.0",
                "request_class": "DIAGNOSIS",
                "skills": [],
                "mode": "READ_ONLY",
                "status_first": False,
                "deterministic_response": True,
                "write_governed": False,
                "requirements": {
                    "capabilities": {"required": ["ci.run.wait"], "optional": []}
                },
                "graph": {
                    "start": "wait-ci",
                    "max_attempts_per_step": 2,
                    "steps": {
                        "wait-ci": {
                            "type": "external_wait",
                            "use": "ci.run.wait",
                            "routes": {
                                "pending": "WAITING_EXTERNAL",
                                "green": "END",
                                "blocked": "BLOCKED_UNRECOVERABLE",
                            },
                        }
                    },
                },
                "completion": {
                    "transition_to": "VALIDATING",
                    "policy": "ci-wait-test-complete@1",
                    "authority": "TaskRun",
                },
            },
            "customer-agent-human-gate-test.json": {
                "schema": "harness-workflow@1",
                "id": "customer-agent-human-gate-test",
                "version": "1.0.0",
                "request_class": "DIAGNOSIS",
                "skills": ["customer-agent-audit"],
                "mode": "READ_ONLY",
                "status_first": False,
                "deterministic_response": True,
                "write_governed": False,
                "requirements": {
                    "capabilities": {
                        "required": ["workspace.read", "vcs.diff.read"],
                        "optional": [],
                    }
                },
                "graph": {
                    "start": "policy-choice",
                    "max_attempts_per_step": 2,
                    "steps": {
                        "policy-choice": {
                            "type": "human_gate",
                            "routes": {
                                "needs-human": "HUMAN_GATE",
                                "approved": "END",
                                "rejected": "BLOCKED_UNRECOVERABLE",
                            },
                        }
                    },
                },
                "completion": {
                    "transition_to": "VALIDATING",
                    "policy": "human-gate-test-complete@1",
                    "authority": "TaskRun",
                },
            },
        }
        workflow_dir = self.package / "workflows"
        for name, payload in workflows.items():
            (workflow_dir / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        starter_path = self.package / "starter.json"
        starter = json.loads(starter_path.read_text(encoding="utf-8"))
        starter["workflows"].extend(f"workflows/{name}" for name in workflows)
        starter["entrypoints"].update(
            {
                "architecture_review": "customer-agent-ci-wait-test",
                "full_dev": "customer-agent-human-gate-test",
            }
        )
        starter_path.write_text(
            json.dumps(starter, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def assert_session_schema(self, session):
        self.assertEqual(session["schema"], "starter-host-session@1")
        self.assertEqual(set(session), set(self.schema["required"]))
        self.assertIn(session["phase"], self.schema["properties"]["phase"]["enum"])
        self.assertEqual(
            session["next_action"]["schema"], "starter-host-next-action@1"
        )
        self.assertIn(
            session["next_action"]["kind"],
            self.schema["$defs"]["nextAction"]["properties"]["kind"]["enum"],
        )

    @staticmethod
    def selection(session, entrypoint):
        request = session["selection_request"]
        return {
            "schema": STARTER_HOST_SELECTION_SCHEMA,
            "host_id": request["host_id"],
            "request_fingerprint_sha256": request["request_fingerprint_sha256"],
            "selected_entrypoint": entrypoint,
            "authority_effect": False,
        }

    def host_result(self, session, *, outcome):
        wait = session["runtime_state"]["host_wait"]
        request_path = self.project / wait["request_ref"].removeprefix("file:")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        content = json.dumps(
            {"skill": request["skill"]["name"], "outcome": outcome},
            ensure_ascii=False,
            sort_keys=True,
        )
        return {
            "schema": HOST_RESULT_SCHEMA,
            "execution_id": wait["execution_id"],
            "request_fingerprint_sha256": request["request_fingerprint_sha256"],
            "host_id": "codex",
            "status": "PASS",
            "loaded_skill": dict(request["skill"]),
            "outcome": outcome,
            "output": {
                "schema": "starter-skill-output@1",
                "content": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "evidence_ref": f"host:output:{wait['execution_id']}",
            },
            "tool_receipts": [
                {
                    "schema": HOST_TOOL_RECEIPT_SCHEMA,
                    "tool_call_id": f"tool-{wait['execution_id']}",
                    "tool_name": "workspace.read",
                    "arguments_sha256": hashlib.sha256(b"args").hexdigest(),
                    "result_sha256": hashlib.sha256(b"result").hexdigest(),
                    "evidence_ref": f"host:tool:{wait['execution_id']}",
                    "mutates": False,
                    "write_authority_checked": False,
                }
            ],
            "evidence_refs": [f"host:execution:{wait['execution_id']}"],
            "payload": {"skill": request["skill"]["name"]},
            "problem_ledger_ref": None,
            "authority_effect": False,
        }

    def open_selected_audit(self, session_id="audit-session"):
        opened = self.orchestrator.open(
            session_id=session_id,
            user_request="检查客服 Agent 总体还有哪些问题",
        )
        return self.orchestrator.select(
            session_id=session_id,
            expected_revision=opened["revision"],
            selection=self.selection(opened, "overall_audit"),
        )

    def open_selected_entrypoint(self, session_id, entrypoint):
        opened = self.orchestrator.open(
            session_id=session_id,
            user_request=f"run exact test entrypoint {entrypoint}",
        )
        return self.orchestrator.select(
            session_id=session_id,
            expected_revision=opened["revision"],
            selection=self.selection(opened, entrypoint),
        )

    def test_read_only_session_reuses_one_taskrun_across_two_host_waits_and_validation(self) -> None:
        ready = self.open_selected_audit()
        self.assertEqual(ready["phase"], PHASE_READY_TO_START)
        started = self.orchestrator.start(
            session_id="audit-session",
            expected_revision=ready["revision"],
            target_ref={"kind": "project", "ref": "customer-agent"},
        )
        self.assert_session_schema(started)
        self.assertEqual(started["phase"], PHASE_WAITING_HOST)
        self.assertEqual(started["next_action"]["kind"], "EXECUTE_HOST_SKILL")
        task_id = started["task_id"]
        first_execution = started["runtime_state"]["host_wait"]["execution_id"]
        self.assertEqual(
            len(list((self.project / ".harness/taskruns").glob("*.json"))), 1
        )
        self.assertFalse((self.project / ".quality/skill-invocations").exists())

        second_wait = self.orchestrator.submit_host_result(
            session_id="audit-session",
            expected_revision=started["revision"],
            result=self.host_result(started, outcome="findings"),
        )
        self.assertEqual(second_wait["phase"], PHASE_WAITING_HOST)
        self.assertEqual(second_wait["task_id"], task_id)
        self.assertNotEqual(
            second_wait["runtime_state"]["host_wait"]["execution_id"],
            first_execution,
        )
        self.assertEqual(
            len(list((self.project / ".quality/skill-invocations").glob("wf-*.json"))),
            1,
        )

        finished = self.orchestrator.submit_host_result(
            session_id="audit-session",
            expected_revision=second_wait["revision"],
            result=self.host_result(second_wait, outcome="continue"),
        )
        self.assert_session_schema(finished)
        self.assertEqual(finished["phase"], PHASE_VALIDATING)
        self.assertEqual(finished["task_id"], task_id)
        self.assertEqual(finished["taskrun_status"], "VALIDATING")
        self.assertNotEqual(finished["taskrun_status"], "COMPLETED")
        self.assertEqual(
            finished["next_action"]["kind"], "EVALUATE_COMPLETION_POLICY"
        )
        self.assertFalse(
            finished["next_action"]["policy"]["graph_end_completes_taskrun"]
        )
        self.assertEqual(
            len(list((self.project / ".quality/skill-invocations").glob("wf-*.json"))),
            2,
        )
        taskrun = json.loads(
            (self.project / finished["taskrun_ref"].removeprefix("file:")).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(taskrun["status"], "VALIDATING")
        self.assertEqual(taskrun["binding"]["host_session_id"], "audit-session")

        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "does not allow|not executable"
        ):
            self.orchestrator.start(
                session_id="audit-session",
                expected_revision=finished["revision"],
                target_ref={"kind": "project", "ref": "customer-agent"},
            )

    def test_mutating_selection_requires_exact_confirmation_but_grants_no_write_authority(self) -> None:
        opened = self.orchestrator.open(
            session_id="repair-session",
            user_request="修复 finding-17，测试后提交 GitHub CI",
        )
        preview = self.orchestrator.select(
            session_id="repair-session",
            expected_revision=opened["revision"],
            selection=self.selection(opened, "repair_with_ci"),
        )
        self.assertEqual(preview["phase"], PHASE_AWAITING_CONFIRMATION)
        self.assertEqual(
            preview["next_action"]["kind"], "CONFIRM_EXACT_EFFECT_PREVIEW"
        )
        self.assertFalse(preview["policy"]["write_authority_granted"])
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "not executable"
        ):
            self.orchestrator.start(
                session_id="repair-session",
                expected_revision=preview["revision"],
                target_ref={"kind": "finding", "ref": "finding-17"},
            )

        confirmation = {
            "schema": STARTER_HOST_CONFIRMATION_SCHEMA,
            "request_fingerprint_sha256": opened["selection_request"][
                "request_fingerprint_sha256"
            ],
            "selected_entrypoint": "repair_with_ci",
            "effect_preview_sha256": preview["resolution"]["effect_preview_sha256"],
            "confirmed": True,
            "authority_effect": False,
        }
        ready = self.orchestrator.confirm(
            session_id="repair-session",
            expected_revision=preview["revision"],
            confirmation=confirmation,
        )
        self.assertEqual(ready["phase"], PHASE_READY_TO_START)
        self.assertTrue(ready["resolution"]["confirmed"])
        self.assertFalse(ready["policy"]["write_authority_granted"])
        self.assertFalse(ready["next_action"]["policy"]["automatic_merge"])

        bad = dict(confirmation)
        bad["effect_preview_sha256"] = "0" * 64
        another = self.orchestrator.open(
            session_id="bad-confirmation",
            user_request="修复 finding-17，测试后提交 GitHub CI",
        )
        another = self.orchestrator.select(
            session_id="bad-confirmation",
            expected_revision=another["revision"],
            selection=self.selection(another, "repair_with_ci"),
        )
        with self.assertRaisesRegex(Exception, "exact preview"):
            self.orchestrator.confirm(
                session_id="bad-confirmation",
                expected_revision=another["revision"],
                confirmation=bad,
            )

    def test_stale_revision_wrong_host_result_and_tampered_registration_fail_closed(self) -> None:
        ready = self.open_selected_audit("closed-session")
        started = self.orchestrator.start(
            session_id="closed-session",
            expected_revision=ready["revision"],
            target_ref={"kind": "project", "ref": "customer-agent"},
        )
        with self.assertRaisesRegex(StarterHostOrchestrationError, "revision conflict"):
            self.orchestrator.submit_host_result(
                session_id="closed-session",
                expected_revision=ready["revision"],
                result=self.host_result(started, outcome="findings"),
            )
        wrong = self.host_result(started, outcome="findings")
        wrong["execution_id"] = "host-unrelated"
        with self.assertRaisesRegex(StarterHostOrchestrationError, "does not match"):
            self.orchestrator.submit_host_result(
                session_id="closed-session",
                expected_revision=started["revision"],
                result=wrong,
            )
        unchanged = self.orchestrator.read("closed-session")
        self.assertEqual(unchanged["revision"], started["revision"])
        self.assertEqual(unchanged["phase"], PHASE_WAITING_HOST)
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "does not allow this transition"
        ):
            self.orchestrator.resume_external(
                session_id="closed-session",
                expected_revision=started["revision"],
                event={"event": "ci.completed"},
                evidence_refs=("ci:run:unrelated",),
                correlation_ref="ci-unrelated",
            )

        session_path = (
            self.project / ".harness/runtime/host-sessions/closed-session.json"
        )
        tampered = json.loads(session_path.read_text(encoding="utf-8"))
        tampered["registration_sha256"] = "0" * 64
        session_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "registration digest drifted"
        ):
            self.orchestrator.read("closed-session")

    def test_concurrent_selection_has_one_cas_winner(self) -> None:
        opened = self.orchestrator.open(
            session_id="concurrent-selection",
            user_request="检查总体问题",
        )
        barrier = threading.Barrier(2)
        successes = []
        failures = []

        def select(entrypoint):
            barrier.wait(timeout=5)
            try:
                successes.append(
                    self.orchestrator.select(
                        session_id="concurrent-selection",
                        expected_revision=0,
                        selection=self.selection(opened, entrypoint),
                    )
                )
            except Exception as exc:  # counterexample result is asserted below
                failures.append(exc)

        threads = [
            threading.Thread(target=select, args=("overall_audit",)),
            threading.Thread(target=select, args=("architecture_review",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], StarterHostOrchestrationError)
        persisted = self.orchestrator.read("concurrent-selection")
        self.assertEqual(persisted["revision"], 1)
        self.assertEqual(
            persisted["selection"]["selected_entrypoint"],
            successes[0]["selection"]["selected_entrypoint"],
        )

    def test_persisted_session_tampering_is_rejected_even_with_recomputed_digest(self) -> None:
        opened = self.orchestrator.open(
            session_id="tamper-action",
            user_request="检查总体问题",
        )
        path = self.project / ".harness/runtime/host-sessions/tamper-action.json"
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["next_action"]["kind"] = "AUTOMATIC_MERGE"
        tampered["next_action"]["policy"]["automatic_merge"] = True
        body = dict(tampered)
        body.pop("state_digest_sha256")
        tampered["state_digest_sha256"] = hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(
            StarterHostOrchestrationError,
            "phase or authority policy|next_action",
        ):
            self.orchestrator.read("tamper-action")

        clean = self.orchestrator.open(
            session_id="tamper-fields",
            user_request="检查模块问题",
        )
        second = self.project / ".harness/runtime/host-sessions/tamper-fields.json"
        unknown = dict(clean)
        unknown["future_authority"] = True
        body = dict(unknown)
        body.pop("state_digest_sha256")
        unknown["state_digest_sha256"] = hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        second.write_text(json.dumps(unknown), encoding="utf-8")
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "fields are not closed"
        ):
            self.orchestrator.read("tamper-fields")

    def test_external_and_human_boundaries_resume_same_taskrun_to_validation(self) -> None:
        external_ready = self.open_selected_entrypoint(
            "external-session", "architecture_review"
        )
        external = self.orchestrator.start(
            session_id="external-session",
            expected_revision=external_ready["revision"],
            target_ref={"kind": "ci", "ref": "ci-host-session-1"},
        )
        self.assertEqual(external["phase"], PHASE_WAITING_EXTERNAL)
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "requires durable evidence"
        ):
            self.orchestrator.resume_external(
                session_id="external-session",
                expected_revision=external["revision"],
                event={
                    "event": "ci.completed",
                    "correlation_ref": "ci-host-session-1",
                    "status": "success",
                },
                evidence_refs=(),
                correlation_ref="ci-host-session-1",
            )
        external_done = self.orchestrator.resume_external(
            session_id="external-session",
            expected_revision=external["revision"],
            event={
                "event": "ci.completed",
                "correlation_ref": "ci-host-session-1",
                "status": "success",
            },
            evidence_refs=("ci:event:ci-host-session-1",),
            correlation_ref="ci-host-session-1",
        )
        self.assertEqual(external_done["phase"], PHASE_VALIDATING)
        self.assertEqual(external_done["task_id"], external["task_id"])
        self.assertEqual(external_done["taskrun_status"], "VALIDATING")

        human_ready = self.open_selected_entrypoint("human-session", "full_dev")
        human = self.orchestrator.start(
            session_id="human-session",
            expected_revision=human_ready["revision"],
            target_ref={"kind": "policy", "ref": "policy-choice"},
        )
        self.assertEqual(human["phase"], PHASE_HUMAN_GATE)
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "requires durable evidence"
        ):
            self.orchestrator.resume_human(
                session_id="human-session",
                expected_revision=human["revision"],
                decision={"decision": "approve"},
                evidence_refs=(),
            )
        human_done = self.orchestrator.resume_human(
            session_id="human-session",
            expected_revision=human["revision"],
            decision={"decision": "approve"},
            evidence_refs=("human:decision:policy-choice:approve",),
        )
        self.assertEqual(human_done["phase"], PHASE_VALIDATING)
        self.assertEqual(human_done["task_id"], human["task_id"])
        self.assertEqual(human_done["taskrun_status"], "VALIDATING")

    def test_interrupted_start_and_resume_reconcile_without_reexecuting_completed_state(self) -> None:
        ready = self.open_selected_entrypoint(
            "reconcile-session", "architecture_review"
        )
        self.orchestrator.runtime_factory = CrashAfterStartRuntime
        with self.assertRaises(SimulatedProcessCrash):
            self.orchestrator.start(
                session_id="reconcile-session",
                expected_revision=ready["revision"],
                target_ref={"kind": "ci", "ref": "ci-host-session-1"},
            )
        interrupted_start = self.orchestrator.read("reconcile-session")
        self.assertEqual(interrupted_start["phase"], PHASE_STARTING)
        self.orchestrator.runtime_factory = StarterWorkflowRuntime
        waiting = self.orchestrator.reconcile(
            session_id="reconcile-session",
            expected_revision=interrupted_start["revision"],
        )
        self.assertEqual(waiting["phase"], PHASE_WAITING_EXTERNAL)
        task_id = waiting["task_id"]

        self.orchestrator.runtime_factory = CrashAfterResumeRuntime
        with self.assertRaises(SimulatedProcessCrash):
            self.orchestrator.resume_external(
                session_id="reconcile-session",
                expected_revision=waiting["revision"],
                event={
                    "event": "ci.completed",
                    "correlation_ref": "ci-host-session-1",
                    "status": "success",
                },
                evidence_refs=("ci:event:ci-host-session-1",),
                correlation_ref="ci-host-session-1",
            )
        interrupted_resume = self.orchestrator.read("reconcile-session")
        self.assertEqual(interrupted_resume["phase"], PHASE_RESUMING_EXTERNAL)
        self.orchestrator.runtime_factory = StarterWorkflowRuntime
        recovered = self.orchestrator.reconcile(
            session_id="reconcile-session",
            expected_revision=interrupted_resume["revision"],
        )
        self.assertEqual(recovered["phase"], PHASE_VALIDATING)
        self.assertEqual(recovered["task_id"], task_id)
        self.assertEqual(recovered["taskrun_status"], "VALIDATING")

    def test_ambiguous_resuming_state_blocks_instead_of_blind_replay(self) -> None:
        ready = self.open_selected_entrypoint(
            "ambiguous-session", "architecture_review"
        )
        waiting = self.orchestrator.start(
            session_id="ambiguous-session",
            expected_revision=ready["revision"],
            target_ref={"kind": "ci", "ref": "ci-host-session-1"},
        )
        self.orchestrator.runtime_factory = CrashAfterTaskRunResumeRuntime
        with self.assertRaises(SimulatedProcessCrash):
            self.orchestrator.resume_external(
                session_id="ambiguous-session",
                expected_revision=waiting["revision"],
                event={
                    "event": "ci.completed",
                    "correlation_ref": "ci-host-session-1",
                    "status": "success",
                },
                evidence_refs=("ci:event:ci-host-session-1",),
                correlation_ref="ci-host-session-1",
            )
        interrupted = self.orchestrator.read("ambiguous-session")
        self.orchestrator.runtime_factory = StarterWorkflowRuntime
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "did not advance|reconciliation blocked"
        ):
            self.orchestrator.reconcile(
                session_id="ambiguous-session",
                expected_revision=interrupted["revision"],
            )
        blocked = self.orchestrator.read("ambiguous-session")
        self.assertEqual(blocked["phase"], PHASE_BLOCKED)
        self.assertIn("did not advance", blocked["last_error"])

    def test_next_action_projection_is_closed_and_never_completes(self) -> None:
        base = {"workflow_id": "wf", "task_id": "task-1"}
        cases = [
            (
                {**base, "runtime_status": "WAITING_EXTERNAL", "external_wait": {"correlation_ref": "ci-1"}},
                "WAIT_EXTERNAL_EVENT",
                PHASE_WAITING_EXTERNAL,
            ),
            (
                {**base, "runtime_status": "HUMAN_GATE", "human_gate": {"gate_id": "g-1"}},
                "REQUEST_HUMAN_DECISION",
                PHASE_HUMAN_GATE,
            ),
            (
                {**base, "runtime_status": "BLOCKED_UNRECOVERABLE", "runtime_error": "blocked"},
                "INSPECT_BLOCKER",
                PHASE_BLOCKED,
            ),
        ]
        for state, kind, expected_phase in cases:
            phase, action = project_runtime_action(
                {"runtime_state": state, "taskrun_status": "BLOCKED"},
                session_id="session-1",
                task_id="task-1",
            )
            self.assertEqual(phase, expected_phase)
            self.assertEqual(action["kind"], kind)
            self.assertFalse(action["policy"]["authority_effect"])

        phase, action = project_runtime_action(
            {
                "runtime_state": {**base, "runtime_status": "WORKFLOW_END"},
                "taskrun_status": "VALIDATING",
                "taskrun_phase": "WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY",
            },
            session_id="session-1",
            task_id="task-1",
        )
        self.assertEqual(phase, PHASE_VALIDATING)
        self.assertEqual(action["kind"], "EVALUATE_COMPLETION_POLICY")
        with self.assertRaisesRegex(
            StarterHostOrchestrationError, "TaskRun VALIDATING"
        ):
            project_runtime_action(
                {
                    "runtime_state": {**base, "runtime_status": "WORKFLOW_END"},
                    "taskrun_status": "COMPLETED",
                },
                session_id="session-1",
                task_id="task-1",
            )


if __name__ == "__main__":
    unittest.main()
