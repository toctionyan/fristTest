from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_SYSTEM = Path(__file__).resolve().parents[1]
CONTROLLER = SKILL_SYSTEM / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from composition_bootstrap import CompositionBootstrap  # type: ignore  # noqa: E402
from full_development_workflow import (  # type: ignore  # noqa: E402
    FullDevelopmentWorkflowError,
    load_full_development_workflow,
)
from harness_invocation import (  # type: ignore  # noqa: E402
    HarnessInvocationError,
    OPEN_MODE,
    SKILL_MODE,
    WORKFLOW_MODE,
    build_route,
    infer_mode,
)
from scope_guard import bootstrap_command_allowed  # type: ignore  # noqa: E402


ROOT = SKILL_SYSTEM.parent


class HarnessInvocationTest(unittest.TestCase):
    def registry_workspace(self) -> Path:
        workspace = Path(tempfile.mkdtemp(prefix="harness-invoke-registry-"))
        self.addCleanup(lambda: shutil.rmtree(workspace, ignore_errors=True))
        target = workspace / "skill-system/registry"
        target.mkdir(parents=True, exist_ok=True)
        for name in (
            "active-skills.json",
            "dev-workflows.json",
            "full-development-workflow.json",
        ):
            shutil.copy2(SKILL_SYSTEM / "registry" / name, target / name)
        return workspace

    def test_mode_is_selected_only_by_explicit_selector(self) -> None:
        self.assertEqual(infer_mode(), OPEN_MODE)
        self.assertEqual(infer_mode(skill="architecture-options"), SKILL_MODE)
        self.assertEqual(infer_mode(workflow="harness-full-dev"), WORKFLOW_MODE)
        with self.assertRaisesRegex(HarnessInvocationError, "at most one"):
            infer_mode(skill="architecture-options", workflow="harness-full-dev")

    def test_open_mode_does_not_guess_from_payload(self) -> None:
        route = build_route(ROOT, payload="repair and publish this repository")
        self.assertEqual(route["mode"], OPEN_MODE)
        self.assertIsNone(route["selected_skill"])
        self.assertIsNone(route["selected_workflow"])
        self.assertFalse(route["policy"]["automatic_skill_selection_allowed"])

    def test_skill_selection_requires_exact_active_skill_and_creates_no_receipt(self) -> None:
        route = build_route(ROOT, skill="architecture-options", payload="review")
        self.assertEqual(route["mode"], SKILL_MODE)
        self.assertEqual(route["selected_skill"], "architecture-options")
        self.assertIsNone(route["receipt"])
        self.assertTrue(route["policy"]["host_execution_required_before_receipt"])
        with self.assertRaisesRegex(HarnessInvocationError, "active Skill not found"):
            build_route(ROOT, skill="architecture", payload="review")

    def test_full_development_composition_activates_exact_provider_bindings(self) -> None:
        assembly = CompositionBootstrap(ROOT).assemble("harness-full-dev-github")
        self.assertTrue(assembly.ready)
        self.assertEqual(assembly.composition.workflow_id, "harness-full-dev")
        self.assertEqual(assembly.composition.completion_authority, "TaskRun")
        self.assertTrue(assembly.composition.write_authority_required)

        route = build_route(
            ROOT,
            workflow="harness-full-dev",
            composition_id="harness-full-dev-github",
            task_id="task-full-dev-1",
            payload="continue the Harness project",
        )
        self.assertEqual(route["mode"], WORKFLOW_MODE)
        self.assertEqual(route["selected_workflow"], "harness-full-dev")
        self.assertEqual(route["next_action"], "diagnose")
        self.assertEqual(route["runtime_state"]["status"], "RUNNING")
        self.assertEqual(route["runtime_state"]["completion_authority"], "TaskRun")
        self.assertFalse(route["runtime_state"]["authority_effect"])
        self.assertFalse(route["composition_activation"]["write_authority_granted"])
        self.assertFalse(route["policy"]["activation_completes_taskrun"])

    def test_workflow_selection_fails_closed_on_missing_or_mismatched_composition(self) -> None:
        with self.assertRaisesRegex(HarnessInvocationError, "explicit --composition-id"):
            build_route(ROOT, workflow="harness-full-dev", task_id="task-1")
        with self.assertRaisesRegex(HarnessInvocationError, "composition/workflow mismatch"):
            build_route(
                ROOT,
                workflow="harness-full-dev",
                composition_id="repair-and-prove-local",
                task_id="task-1",
            )

    def test_full_development_activation_projection_cannot_drift_from_children(self) -> None:
        workspace = self.registry_workspace()
        path = workspace / "skill-system/registry/dev-workflows.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        full = next(row for row in payload["workflows"] if row["workflow_id"] == "harness-full-dev")
        full["requirements"]["capabilities"]["required"].remove(
            "publication.post_merge.validation.wait"
        )
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(FullDevelopmentWorkflowError, "activation projection"):
            load_full_development_workflow(workspace)

    def test_cli_unknown_composition_returns_structured_failure(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "skillctl.py",
                "invoke",
                "--workflow",
                "harness-full-dev",
                "--composition-id",
                "harness-full",
                "--task-id",
                "task-1",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "FAIL")
        self.assertIn("unknown composition_id", payload["error"])

    def test_skillctl_invoke_exposes_the_same_exact_activation_route(self) -> None:
        command = [
            sys.executable,
            "-B",
            "skillctl.py",
            "invoke",
            "--workflow",
            "harness-full-dev",
            "--composition-id",
            "harness-full-dev-github",
            "--task-id",
            "task-cli-1",
            "--payload",
            "continue",
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["selected_workflow"], "harness-full-dev")
        self.assertEqual(payload["next_action"], "diagnose")
        self.assertFalse(payload["composition_activation"]["write_authority_granted"])
        self.assertTrue(bootstrap_command_allowed("python3 -B skillctl.py invoke --workflow harness-full-dev"))


if __name__ == "__main__":
    unittest.main()
