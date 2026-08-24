from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import CapabilityBinding  # type: ignore  # noqa: E402
from concrete_host_bootstrap import (  # type: ignore  # noqa: E402
    CONCRETE_HOST_BOOTSTRAP_SCHEMA,
    seal_bootstrap,
)
from durable_human_gate import (  # type: ignore  # noqa: E402
    DurableHumanGateAdapter,
    DurableHumanGateError,
    seal_human_decision,
    write_human_decision,
)
from governed_write_authority import (  # type: ignore  # noqa: E402
    ChangePermitWriteAuthorityGuard,
    GovernedWriteAuthorityError,
)
from repair_governance import create_permit  # type: ignore  # noqa: E402
from workflow_graph_contract import WorkflowStepSpec  # type: ignore  # noqa: E402


def _json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WriteAuthorityHumanGateBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="write-authority-human-gate-"))
        self.addCleanup(lambda: shutil.rmtree(self.workspace, ignore_errors=True))

    def _governance(self) -> tuple[dict[str, object], str]:
        change_id = "repair-example"
        case = self.workspace / "governance/repair-cases" / change_id
        evidence = self.workspace / "evidence/red.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("reproduced\n", encoding="utf-8")
        source = self.workspace / "src/a.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("a = 1\n", encoding="utf-8")
        (self.workspace / "src/b.py").write_text("b = 1\n", encoding="utf-8")
        failure = {
            "schema_version": 1,
            "record_type": "failure-case",
            "change_id": change_id,
            "classification": "implementation-defect",
            "reproduction": {
                "status": "REPRODUCED",
                "expected": "a",
                "actual": "b",
                "evidence_refs": ["evidence/red.txt"],
            },
            "violated_invariants": ["exact scope"],
            "affected_boundaries": ["source"],
        }
        _json(case / "failure-case.json", failure)
        root = {
            "schema_version": 1,
            "record_type": "root-cause-proof",
            "change_id": change_id,
            "failure_case_sha256": _sha(case / "failure-case.json"),
            "decision": "PROVEN",
            "root_cause": "known",
            "causal_chain": ["one", "two"],
            "evidence_refs": ["evidence/red.txt"],
            "rejected_hypotheses": ["allow all"],
            "affected_boundaries": ["source"],
        }
        _json(case / "root-cause-proof.json", root)
        required_tests = {
            "focused": ["exact"],
            "counterexamples": ["deny"],
            "regression": ["existing"],
            "negative_path": ["merge"],
        }
        plan = {
            "schema_version": 1,
            "record_type": "repair-plan",
            "change_id": change_id,
            "root_cause_proof_sha256": _sha(case / "root-cause-proof.json"),
            "status": "APPROVED",
            "strategy": "bounded",
            "changes": [
                {"path": "src/a.py", "responsibility": "repair", "reason": "red"}
            ],
            "unchanged_boundaries": ["completion"],
            "forbidden_repairs": ["broad scope"],
            "required_invariants": ["exact"],
            "required_tests": required_tests,
            "risks": ["stale"],
            "rollback_plan": "restore",
        }
        _json(case / "repair-plan.json", plan)
        review = {
            "schema_version": 1,
            "record_type": "plan-review",
            "change_id": change_id,
            "repair_plan_sha256": _sha(case / "repair-plan.json"),
            "reviewer_role": "repair-plan-reviewer",
            "decision": "APPROVED",
            "approved_paths": ["src/a.py"],
            "review_findings": ["bounded"],
            "skill_rule_mappings": [
                {"rule": "scope", "assessment": "exact", "evidence": "src/a.py"}
            ],
        }
        _json(case / "plan-review.json", review)
        contract: dict[str, object] = {
            "schema_version": 1,
            "change_id": change_id,
            "target_kind": "repair",
            "status": "implementing",
            "repair_governance": f"governance/repair-cases/{change_id}",
            "allowed_paths": ["src/a.py"],
            "forbidden_paths": ["src/b.py"],
        }
        _json(self.workspace / "governance/active-change.json", contract)
        permit_path = create_permit(self.workspace, contract)
        permit = json.loads(permit_path.read_text(encoding="utf-8"))
        return contract, permit["permit_digest"]

    @staticmethod
    def _binding(capability_id: str, *, provider_type: str = "executor") -> CapabilityBinding:
        return CapabilityBinding(
            capability_id=capability_id,
            provider_id="local.workspace" if provider_type == "executor" else "github.code_review",
            provider_type=provider_type,
            activation_key="test",
            mutates=True,
            external_wait=False,
        )

    @staticmethod
    def _step(capability_id: str) -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id="mutate",
            step_type="executor",
            use=capability_id,
            routes={"green": "END", "blocked": "BLOCKED_UNRECOVERABLE"},
            max_attempts=2,
        )

    def test_change_permit_guard_checks_exact_workspace_commit_and_pr_paths(self) -> None:
        _, permit_digest = self._governance()
        guard = ChangePermitWriteAuthorityGuard(workspace=self.workspace)
        base = {
            "task_id": "task-1",
            "workflow_id": "repair-flow",
            "target_ref": {
                "change_id": "repair-example",
                "permit_digest": permit_digest,
            },
        }
        workspace_state = json.loads(json.dumps(base))
        workspace_state["target_ref"]["workspace_requests"] = {
            "workspace.write": {"operations": [{"path": "src/a.py"}]}
        }
        guard.assert_allowed(
            binding=self._binding("workspace.write"),
            step=self._step("workspace.write"),
            state=workspace_state,
        )
        self.assertTrue(
            (
                self.workspace
                / ".harness/runtime/authority-checks/task-1/mutate-workspace.write-1.json"
            ).is_file()
        )

        for capability, provider_type in (
            ("vcs.commit.create", "executor"),
            ("code_review.pull_request.create", "integration"),
        ):
            state = json.loads(json.dumps(base))
            state["target_ref"]["publication_requests"] = {
                capability: {"changed_paths": ["src/a.py"]}
            }
            guard.assert_allowed(
                binding=self._binding(capability, provider_type=provider_type),
                step=self._step(capability),
                state=state,
            )

    def test_guard_rejects_scope_drift_stale_identity_and_generic_merge(self) -> None:
        _, permit_digest = self._governance()
        guard = ChangePermitWriteAuthorityGuard(workspace=self.workspace)
        state = {
            "task_id": "task-1",
            "workflow_id": "repair-flow",
            "target_ref": {
                "change_id": "repair-example",
                "permit_digest": permit_digest,
                "workspace_requests": {
                    "workspace.write": {"operations": [{"path": "src/b.py"}]}
                },
            },
        }
        with self.assertRaisesRegex(GovernedWriteAuthorityError, "forbidden"):
            guard.assert_allowed(
                binding=self._binding("workspace.write"),
                step=self._step("workspace.write"),
                state=state,
            )
        state["target_ref"]["permit_digest"] = "0" * 64
        with self.assertRaisesRegex(GovernedWriteAuthorityError, "permit_digest"):
            guard.assert_allowed(
                binding=self._binding("workspace.write"),
                step=self._step("workspace.write"),
                state=state,
            )

        merge_state = {
            "task_id": "task-1",
            "workflow_id": "repair-flow",
            "target_ref": {
                "change_id": "repair-example",
                "permit_digest": permit_digest,
                "publication_requests": {
                    "code_review.pull_request.merge": {"changed_paths": ["src/a.py"]}
                },
            },
        }
        with self.assertRaisesRegex(GovernedWriteAuthorityError, "never authorizes"):
            guard.assert_allowed(
                binding=self._binding(
                    "code_review.pull_request.merge", provider_type="integration"
                ),
                step=self._step("code_review.pull_request.merge"),
                state=merge_state,
            )

    @staticmethod
    def _human_step() -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id="policy",
            step_type="human_gate",
            use=None,
            routes={
                "needs-human": "HUMAN_GATE",
                "approve": "END",
                "reject": "BLOCKED_UNRECOVERABLE",
            },
            max_attempts=3,
        )

    def test_human_gate_requires_same_sealed_durable_decision(self) -> None:
        adapter = DurableHumanGateAdapter(workspace=self.workspace)
        state = {"task_id": "task-1", "workflow_id": "full-dev"}
        waiting = adapter.invoke(step=self._human_step(), state=state)
        self.assertEqual(waiting.outcome, "needs-human")
        gate_ref = waiting.human_gate["gate_ref"]
        result = write_human_decision(
            workspace=self.workspace,
            gate_path=self.workspace / gate_ref.removeprefix("file:"),
            decision_root=".harness/runtime/human-decisions",
            selected_outcome="approve",
            actor="operator-1",
        )
        resumed = adapter.invoke(
            step=self._human_step(),
            state={**state, "human_decision": result["decision"]},
        )
        self.assertEqual(resumed.outcome, "approve")
        self.assertFalse(resumed.payload["authority_effect"])
        self.assertFalse(resumed.payload["merge_authority_changed"])

        tampered = dict(result["decision"])
        tampered["selected_outcome"] = "reject"
        with self.assertRaisesRegex(DurableHumanGateError, "fingerprint"):
            adapter.invoke(
                step=self._human_step(), state={**state, "human_decision": tampered}
            )

    def test_inline_or_other_gate_decision_cannot_resume(self) -> None:
        adapter = DurableHumanGateAdapter(workspace=self.workspace)
        state = {"task_id": "task-1", "workflow_id": "full-dev"}
        waiting = adapter.invoke(step=self._human_step(), state=state)
        gate_path = self.workspace / waiting.human_gate["gate_ref"].removeprefix("file:")
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        inline_only = seal_human_decision(
            gate, selected_outcome="approve", actor="operator-1"
        )
        with self.assertRaisesRegex(DurableHumanGateError, "missing or unsafe"):
            adapter.invoke(
                step=self._human_step(),
                state={**state, "human_decision": inline_only},
            )

        other = adapter.invoke(
            step=self._human_step(),
            state={"task_id": "task-2", "workflow_id": "full-dev", "human_decision": inline_only},
        )
        self.assertEqual(other.outcome, "needs-human")
        self.assertNotEqual(other.human_gate["gate_id"], gate["gate_id"])

    def test_authoring_cli_creates_decision_without_handwritten_json(self) -> None:
        adapter = DurableHumanGateAdapter(workspace=self.workspace)
        waiting = adapter.invoke(
            step=self._human_step(),
            state={"task_id": "task-1", "workflow_id": "full-dev"},
        )
        bootstrap = seal_bootstrap(
            {
                "schema": CONCRETE_HOST_BOOTSTRAP_SCHEMA,
                "starter": {"starter_id": "customer-agent", "package_sha256": "a" * 64},
                "registration": {"path": ".harness/runtime/registration.json", "sha256": "b" * 64},
                "checkpointer": {"type": "sqlite", "path": ".harness/runtime/checkpoints.sqlite3"},
                "providers": {
                    "execution_profiles": {"test.run": ["test"], "quality.evaluate": ["quality"]},
                    "process_timeout_seconds": 30,
                    "github": None,
                },
                "authority": {
                    "type": "repair-change-permit",
                    "active_contract_path": "governance/active-change.json",
                    "audit_root": ".harness/runtime/authority-checks",
                    "generic_merge_authority": False,
                },
                "human_gate": {
                    "type": "durable-local",
                    "gate_root": ".harness/runtime/human-gates",
                    "decision_root": ".harness/runtime/human-decisions",
                    "authority_effect": False,
                },
                "runtime": {
                    "session_root": ".harness/runtime/sessions",
                    "taskrun_root": ".harness/taskruns",
                    "workspace_fingerprint": None,
                },
                "policy": {
                    "configuration_grants_write_authority": False,
                    "configuration_completes_taskrun": False,
                    "automatic_merge": False,
                    "completion_authority": "TaskRun",
                    "authority_effect": False,
                },
            }
        )
        _json(self.workspace / ".harness/host/bootstrap.json", bootstrap)
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "skillctl.py"),
                "authoring",
                "human-decision",
                "--project-workspace",
                str(self.workspace),
                "--gate-ref",
                waiting.human_gate["gate_ref"],
                "--outcome",
                "approve",
                "--actor",
                "operator-1",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertTrue(
            (self.workspace / payload["decision_ref"].removeprefix("file:")).is_file()
        )


if __name__ == "__main__":
    unittest.main()
