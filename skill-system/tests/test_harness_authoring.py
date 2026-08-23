from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from harness_authoring import (  # type: ignore  # noqa: E402
    HarnessAuthoringError,
    compile_workflow_declaration,
    explain_workflow,
    load_declaration,
    parse_project_declaration,
    parse_skill_contract,
)


def valid_workflow() -> dict[str, object]:
    return {
        "schema": "harness-workflow@1",
        "id": "audit-customer-agent",
        "version": "1.0.0",
        "request_class": "DIAGNOSIS",
        "skills": ["architecture-options"],
        "mode": "READ_ONLY",
        "status_first": False,
        "deterministic_response": False,
        "write_governed": False,
        "requirements": {
            "capabilities": {"required": ["quality.evaluate"], "optional": ["vcs.diff.read"]}
        },
        "graph": {
            "start": "inspect",
            "steps": {
                "inspect": {
                    "type": "skill",
                    "use": "architecture-options",
                    "routes": {"issues": "quality", "clean": "END"},
                },
                "quality": {
                    "type": "gate",
                    "use": "quality.evaluate",
                    "routes": {"pass": "END", "fail": "BLOCKED_UNRECOVERABLE"},
                },
            },
        },
        "completion": {
            "transition_to": "VALIDATING",
            "policy": "audit-report-produced@1",
            "authority": "TaskRun",
        },
    }


class HarnessAuthoringTest(unittest.TestCase):
    def test_json_and_yaml_load_to_the_same_workflow_plan(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            json_path = directory / "workflow.json"
            yaml_path = directory / "workflow.yaml"
            json_path.write_text(json.dumps(valid_workflow()), encoding="utf-8")
            yaml_path.write_text(
                """schema: harness-workflow@1
id: audit-customer-agent
version: 1.0.0
request_class: DIAGNOSIS
skills: [architecture-options]
mode: READ_ONLY
status_first: false
deterministic_response: false
write_governed: false
requirements:
  capabilities:
    required: [quality.evaluate]
    optional: [vcs.diff.read]
graph:
  start: inspect
  steps:
    inspect:
      type: skill
      use: architecture-options
      routes: {issues: quality, clean: END}
    quality:
      type: gate
      use: quality.evaluate
      routes: {pass: END, fail: BLOCKED_UNRECOVERABLE}
completion:
  transition_to: VALIDATING
  policy: audit-report-produced@1
  authority: TaskRun
""",
                encoding="utf-8",
            )
            json_plan = compile_workflow_declaration(load_declaration(json_path))
            yaml_plan = compile_workflow_declaration(load_declaration(yaml_path))
            self.assertEqual(json_plan.as_dict(), yaml_plan.as_dict())
            self.assertEqual(json_plan.spec.graph.start, "inspect")

    def test_compile_and_explain_preserve_runtime_and_completion_authority(self) -> None:
        plan = compile_workflow_declaration(valid_workflow())
        self.assertEqual(plan.spec.workflow_id, "audit-customer-agent")
        self.assertEqual(plan.spec.required_capabilities, ("quality.evaluate",))
        self.assertEqual(plan.as_dict()["completion"]["transition_to"], "VALIDATING")
        explanation = explain_workflow(plan)
        self.assertEqual(explanation["completion"]["authority"], "TaskRun")
        self.assertEqual(explanation["completion"]["graph_end_means"], "TaskRun VALIDATING")
        self.assertEqual([step["step_id"] for step in explanation["steps"]], ["inspect", "quality"])

    def test_unknown_keys_fail_closed(self) -> None:
        declaration = valid_workflow()
        declaration["github_repo"] = "owner/repo"
        with self.assertRaisesRegex(HarnessAuthoringError, "unsupported keys"):
            compile_workflow_declaration(declaration)

    def test_yaml_scalar_types_are_not_coerced_into_contract_strings(self) -> None:
        declaration = valid_workflow()
        declaration["request_class"] = 123
        with self.assertRaisesRegex(HarnessAuthoringError, "request_class must be a non-empty string"):
            compile_workflow_declaration(declaration)

        declaration = valid_workflow()
        declaration["skills"] = [123]
        with self.assertRaisesRegex(HarnessAuthoringError, "skills item must be a non-empty string"):
            compile_workflow_declaration(declaration)

    def test_provider_bound_capability_reuses_registry_rejection(self) -> None:
        declaration = valid_workflow()
        declaration["requirements"] = {
            "capabilities": {"required": ["github.actions.run"], "optional": []}
        }
        declaration["graph"] = {
            "start": "ci",
            "steps": {
                "ci": {
                    "type": "external_wait",
                    "use": "github.actions.run",
                    "routes": {"waiting": "WAITING_EXTERNAL"},
                }
            },
        }
        with self.assertRaisesRegex(HarnessAuthoringError, "provider-neutral"):
            compile_workflow_declaration(declaration)

    def test_graph_end_cannot_claim_completed(self) -> None:
        declaration = valid_workflow()
        declaration["completion"] = {
            "transition_to": "COMPLETED",
            "policy": "audit-report-produced@1",
            "authority": "Graph",
        }
        with self.assertRaisesRegex(HarnessAuthoringError, "must be VALIDATING"):
            compile_workflow_declaration(declaration)

    def test_write_workflow_must_be_governed(self) -> None:
        declaration = valid_workflow()
        declaration["mode"] = "WRITE"
        with self.assertRaisesRegex(HarnessAuthoringError, "write_governed"):
            compile_workflow_declaration(declaration)

    def test_mutating_skill_requires_workspace_write(self) -> None:
        with self.assertRaisesRegex(HarnessAuthoringError, "workspace.write"):
            parse_skill_contract(
                {
                    "schema": "harness-skill-contract@1",
                    "skill": "customer-agent-repair",
                    "version": "1.0.0",
                    "mode": "mutating",
                    "inputs": ["finding-set@1"],
                    "capabilities": ["vcs.diff.read"],
                    "outputs": ["patch-set@1"],
                    "extension_type": "procedure",
                }
            )

    def test_project_write_scope_is_bounded(self) -> None:
        base = {
            "schema": "harness-project@1",
            "project_id": "customer-agent",
            "project_type": "agent",
            "commands": {"test": "python -m unittest"},
            "providers": {},
            "defaults": {},
        }
        for scope in ("/tmp/project", "../secret", "**", "."):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(HarnessAuthoringError, "bounded relative"):
                    parse_project_declaration({**base, "write_scope": [scope]})

    def test_unsafe_yaml_tag_fails_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            marker = Path(raw_dir) / "marker"
            path = Path(raw_dir) / "unsafe.yaml"
            path.write_text(
                f"!!python/object/apply:pathlib.Path.write_text ['{marker}', 'owned']\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(HarnessAuthoringError, "invalid"):
                load_declaration(path)
            self.assertFalse(marker.exists())

    def test_root_cli_initializes_validates_compiles_and_explains_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            project = directory / "harness-project.yaml"
            init = subprocess.run(
                [
                    sys.executable, "-B", "skillctl.py", "authoring", "project-init",
                    "--output", str(project), "--project-id", "customer-agent",
                    "--project-type", "agent", "--command", "test=python -m unittest",
                    "--write-scope", "src/**", "--default", "audit_workflow=audit-customer-agent",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            validation = subprocess.run(
                [sys.executable, "-B", "skillctl.py", "authoring", "validate", str(project)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validation.returncode, 0, validation.stderr)
            self.assertEqual(json.loads(validation.stdout)["status"], "PASS")

            workflow = directory / "workflow.json"
            workflow.write_text(json.dumps(valid_workflow()), encoding="utf-8")
            compile_result = subprocess.run(
                [
                    sys.executable, "-B", "skillctl.py", "authoring", "compile",
                    "--workflow", str(workflow),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            self.assertEqual(json.loads(compile_result.stdout)["schema"], "compiled-workflow-plan@1")
            explain_result = subprocess.run(
                [
                    sys.executable, "-B", "skillctl.py", "authoring", "explain",
                    "--workflow", str(workflow),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(explain_result.returncode, 0, explain_result.stderr)
            self.assertEqual(json.loads(explain_result.stdout)["completion"]["authority"], "TaskRun")


if __name__ == "__main__":
    unittest.main()
