from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from capability_registry import CapabilityBinding  # type: ignore
from provider_adapters import (  # type: ignore
    EventDrivenCIProviderAdapter,
    LocalProcessProviderAdapter,
    ProviderAdapterError,
)
from workflow_graph_contract import WorkflowStepSpec  # type: ignore


class FakeProfileRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, profile, *, state_file):
        self.calls.append((profile, state_file))
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text('{"status":"RUNNING"}\n', encoding="utf-8")
        if not self.results:
            raise AssertionError("no fake profile result")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class ProviderAdaptersTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="provider-adapters-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    @staticmethod
    def step(step_id: str, step_type: str, capability: str) -> WorkflowStepSpec:
        return WorkflowStepSpec(
            step_id=step_id,
            step_type=step_type,
            use=capability,
            routes={
                "green": "END",
                "red": "repair",
                "blocked": "BLOCKED_UNRECOVERABLE",
                "pending": "WAITING_EXTERNAL",
            },
            max_attempts=8,
        )

    @staticmethod
    def binding(
        capability: str,
        *,
        provider_id: str,
        provider_type: str,
        external_wait: bool = False,
    ) -> CapabilityBinding:
        return CapabilityBinding(
            capability_id=capability,
            provider_id=provider_id,
            provider_type=provider_type,
            activation_key=provider_id,
            mutates=False,
            external_wait=external_wait,
        )

    def test_local_process_runs_only_allowlisted_target_profile_and_returns_green_evidence(self) -> None:
        workspace = self.workspace()
        runner = FakeProfileRunner([{"status": "PASS"}])
        adapter = LocalProcessProviderAdapter(
            workspace=workspace,
            allowed_profiles={"test.run": ["focused-tests"]},
            runner=runner,
        )
        result = adapter.invoke(
            binding=self.binding(
                "test.run",
                provider_id="local.process",
                provider_type="executor",
            ),
            step=self.step("focused-test", "executor", "test.run"),
            state={
                "task_id": "task-1",
                "step_attempts": {},
                "target_ref": {
                    "execution_profiles": {
                        "focused-test": "focused-tests",
                    }
                },
            },
        )

        self.assertEqual(result.outcome, "green")
        self.assertEqual(runner.calls[0][0], "focused-tests")
        self.assertEqual(len(result.evidence_refs), 2)
        summary_ref = next(ref for ref in result.evidence_refs if ref.endswith(".provider.json"))
        summary = json.loads((workspace / summary_ref.removeprefix("file:")).read_text(encoding="utf-8"))
        self.assertEqual(summary["capability_id"], "test.run")
        self.assertFalse(summary["authority_effect"])
        self.assertFalse(summary["completion_authority_changed"])

    def test_local_quality_fail_maps_to_red_without_claiming_quality_authority(self) -> None:
        workspace = self.workspace()
        adapter = LocalProcessProviderAdapter(
            workspace=workspace,
            allowed_profiles={"quality.evaluate": ["quality-quick"]},
            runner=FakeProfileRunner([{"status": "FAIL", "results": [{"status": "FAIL"}]}]),
        )
        result = adapter.invoke(
            binding=self.binding(
                "quality.evaluate",
                provider_id="local.process",
                provider_type="executor",
            ),
            step=self.step("quality", "gate", "quality.evaluate"),
            state={
                "task_id": "task-2",
                "step_attempts": {},
                "target_ref": {"execution_profiles": {"quality.evaluate": "quality-quick"}},
            },
        )
        self.assertEqual(result.outcome, "red")
        self.assertFalse(result.payload["quality_authority_changed"])

    def test_local_process_runner_exception_maps_to_blocked_with_durable_evidence(self) -> None:
        workspace = self.workspace()
        adapter = LocalProcessProviderAdapter(
            workspace=workspace,
            allowed_profiles={"test.run": ["focused-tests"]},
            runner=FakeProfileRunner([RuntimeError("runner unavailable")]),
        )
        result = adapter.invoke(
            binding=self.binding(
                "test.run",
                provider_id="local.process",
                provider_type="executor",
            ),
            step=self.step("focused-test", "executor", "test.run"),
            state={
                "task_id": "task-3",
                "step_attempts": {},
                "target_ref": {"execution_profiles": {"test.run": "focused-tests"}},
            },
        )
        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(len(result.evidence_refs), 1)
        self.assertIn("runner unavailable", result.payload["error"])

    def test_local_process_rejects_target_selected_profile_outside_composition_policy(self) -> None:
        adapter = LocalProcessProviderAdapter(
            workspace=self.workspace(),
            allowed_profiles={"test.run": ["safe-profile"]},
            runner=FakeProfileRunner([{"status": "PASS"}]),
        )
        with self.assertRaisesRegex(ProviderAdapterError, "not allowed"):
            adapter.invoke(
                binding=self.binding(
                    "test.run",
                    provider_id="local.process",
                    provider_type="executor",
                ),
                step=self.step("focused-test", "executor", "test.run"),
                state={
                    "task_id": "task-4",
                    "target_ref": {"execution_profiles": {"test.run": "arbitrary-shell-profile"}},
                },
            )

    def test_ci_adapter_yields_once_without_polling(self) -> None:
        workspace = self.workspace()
        adapter = EventDrivenCIProviderAdapter(workspace=workspace, provider_id="github.actions")
        result = adapter.invoke(
            binding=self.binding(
                "ci.run.wait",
                provider_id="github.actions",
                provider_type="integration",
                external_wait=True,
            ),
            step=self.step("wait-ci", "external_wait", "ci.run.wait"),
            state={
                "task_id": "task-ci",
                "step_attempts": {},
                "target_ref": {
                    "external_handles": {
                        "ci.run.wait": {
                            "correlation_ref": "run-123",
                            "resume_event": "ci.completed",
                        }
                    }
                },
            },
        )
        self.assertEqual(result.outcome, "pending")
        self.assertEqual(result.external_wait["provider"], "github.actions")
        self.assertEqual(result.external_wait["correlation_ref"], "run-123")
        self.assertEqual(len(result.evidence_refs), 1)

    def test_ci_adapter_interprets_one_matching_resume_event_as_green(self) -> None:
        workspace = self.workspace()
        adapter = EventDrivenCIProviderAdapter(workspace=workspace, provider_id="github.actions")
        result = adapter.invoke(
            binding=self.binding(
                "ci.run.wait",
                provider_id="github.actions",
                provider_type="integration",
                external_wait=True,
            ),
            step=self.step("wait-ci", "external_wait", "ci.run.wait"),
            state={
                "task_id": "task-ci",
                "step_attempts": {"wait-ci": 1},
                "target_ref": {
                    "external_handles": {
                        "ci.run.wait": {
                            "correlation_ref": "run-123",
                            "resume_event": "ci.completed",
                        }
                    }
                },
                "external_event": {
                    "provider": "github.actions",
                    "correlation_ref": "run-123",
                    "event": "ci.completed",
                    "conclusion": "success",
                    "evidence_refs": ["github-run:123"],
                },
            },
        )
        self.assertEqual(result.outcome, "green")
        self.assertIn("github-run:123", result.evidence_refs)
        self.assertIsNone(result.external_wait)
        self.assertFalse(result.payload["completion_authority_changed"])

    def test_ci_resume_must_match_durable_correlation(self) -> None:
        adapter = EventDrivenCIProviderAdapter(
            workspace=self.workspace(),
            provider_id="github.actions",
        )
        with self.assertRaisesRegex(ProviderAdapterError, "correlation_ref"):
            adapter.invoke(
                binding=self.binding(
                    "ci.run.wait",
                    provider_id="github.actions",
                    provider_type="integration",
                    external_wait=True,
                ),
                step=self.step("wait-ci", "external_wait", "ci.run.wait"),
                state={
                    "task_id": "task-ci",
                    "target_ref": {
                        "external_handles": {
                            "ci.run.wait": {
                                "correlation_ref": "run-123",
                                "resume_event": "ci.completed",
                            }
                        }
                    },
                    "external_event": {
                        "provider": "github.actions",
                        "correlation_ref": "run-999",
                        "event": "ci.completed",
                        "conclusion": "success",
                        "evidence_refs": ["github-run:999"],
                    },
                },
            )

    def test_same_ci_contract_can_use_gitlab_without_workflow_change(self) -> None:
        workspace = self.workspace()
        adapter = EventDrivenCIProviderAdapter(workspace=workspace, provider_id="gitlab.ci")
        result = adapter.invoke(
            binding=self.binding(
                "ci.run.wait",
                provider_id="gitlab.ci",
                provider_type="integration",
                external_wait=True,
            ),
            step=self.step("wait-ci", "external_wait", "ci.run.wait"),
            state={
                "task_id": "task-gitlab",
                "target_ref": {
                    "external_handles": {
                        "ci.run.wait": {"correlation_ref": "pipeline-7", "resume_event": "ci.completed"}
                    }
                },
            },
        )
        self.assertEqual(result.outcome, "pending")
        self.assertEqual(result.external_wait["provider"], "gitlab.ci")
        self.assertEqual(result.external_wait["correlation_ref"], "pipeline-7")


if __name__ == "__main__":
    unittest.main()
