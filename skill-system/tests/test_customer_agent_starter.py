from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
STARTER = ROOT / "skill-system" / "starters" / "customer-agent"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from harness_starter import (  # type: ignore  # noqa: E402
    HarnessStarterError,
    initialize_starter,
    list_builtin_starters,
    verify_starter,
)


class CustomerAgentStarterTest(unittest.TestCase):
    def copy_starter(self, directory: Path) -> Path:
        target = directory / "customer-agent"
        shutil.copytree(STARTER, target)
        return target

    @staticmethod
    def load(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def save(path: Path, payload: dict[str, object]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_bundled_starter_verifies_complete_inventory_and_provider_coverage(self) -> None:
        verification = verify_starter(STARTER, registry_workspace=ROOT)
        self.assertEqual(verification.starter.starter_id, "customer-agent")
        self.assertEqual(len(verification.skill_ids), 7)
        self.assertEqual(len(verification.workflow_ids), 6)
        self.assertEqual(len(verification.composed_workflow_ids), 2)
        self.assertEqual(
            set(verification.starter.entrypoints),
            {
                "overall_audit",
                "module_audit",
                "architecture_review",
                "repair_and_prove",
                "repair_with_ci",
                "full_dev",
            },
        )
        self.assertEqual(
            set(verification.required_capabilities),
            set(verification.provider_bindings),
        )
        self.assertEqual(verification.provider_bindings["test.run"], "local.process")
        self.assertEqual(verification.provider_bindings["ci.run.wait"], "github.actions")
        self.assertNotIn("code_review.pull_request.merge", verification.required_capabilities)
        policy = verification.as_dict()["policy"]
        self.assertFalse(policy["automatic_merge"])
        self.assertTrue(policy["standalone_application"])
        self.assertFalse(policy["verification_executes_workflow"])
        self.assertFalse(policy["verification_grants_write_authority"])

    def test_ci_workflows_create_pr_and_wait_but_never_merge(self) -> None:
        for name in ("customer-agent-repair-with-ci", "customer-agent-full-dev"):
            with self.subTest(workflow=name):
                row = self.load(STARTER / "workflows" / f"{name}.json")
                required = row["requirements"]["capabilities"]["required"]
                steps = row["graph"]["steps"]
                uses = {step["use"] for step in steps.values()}
                self.assertIn("code_review.pull_request.create", required)
                self.assertIn("ci.run.wait", required)
                self.assertIn("code_review.pull_request.create", uses)
                self.assertIn("ci.run.wait", uses)
                self.assertNotIn("code_review.pull_request.merge", required)
                self.assertNotIn("code_review.pull_request.merge", uses)
                self.assertEqual(row["completion"]["transition_to"], "VALIDATING")
                self.assertEqual(row["completion"]["authority"], "TaskRun")

    def test_list_and_cli_expose_verified_customer_agent_starter(self) -> None:
        listed = list_builtin_starters(registry_workspace=ROOT)
        customer = next(item for item in listed if item["starter_id"] == "customer-agent")
        self.assertEqual(customer["version"], "1.0.0")
        result = subprocess.run(
            [sys.executable, "-B", "skillctl.py", "authoring", "starter-list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertIn("customer-agent", {row["starter_id"] for row in payload["starters"]})

    def test_initialize_copies_only_verified_package_and_refuses_overwrite(self) -> None:
        expected = verify_starter(STARTER, registry_workspace=ROOT)
        with tempfile.TemporaryDirectory() as raw_dir:
            target = Path(raw_dir) / "installed"
            installed = initialize_starter(
                "customer-agent", target, registry_workspace=ROOT
            )
            self.assertEqual(installed.package_sha256, expected.package_sha256)
            self.assertEqual(
                verify_starter(target, registry_workspace=ROOT).package_sha256,
                expected.package_sha256,
            )
            with self.assertRaisesRegex(HarnessStarterError, "existing target"):
                initialize_starter("customer-agent", target, registry_workspace=ROOT)

    def test_cli_initializes_a_new_verified_package(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            target = Path(raw_dir) / "customer-agent-harness"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "skillctl.py",
                    "authoring",
                    "starter-init",
                    "--starter",
                    "customer-agent",
                    "--output",
                    str(target),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "PASS")
            self.assertTrue((target / "starter.json").is_file())

    def test_missing_and_undeclared_declarations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            (package / "skills" / "customer-agent-audit.json").unlink()
            with self.assertRaisesRegex(HarnessStarterError, "inventory mismatch"):
                verify_starter(package, registry_workspace=ROOT)
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            (package / "skills" / "undeclared.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(HarnessStarterError, "inventory mismatch"):
                verify_starter(package, registry_workspace=ROOT)

    def test_manifest_traversal_and_duplicate_skill_identity_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            manifest = self.load(package / "starter.json")
            manifest["project"] = "../harness-project.json"
            self.save(package / "starter.json", manifest)
            with self.assertRaisesRegex(HarnessStarterError, "bounded relative"):
                verify_starter(package, registry_workspace=ROOT)
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            first = self.load(package / "skills" / "customer-agent-audit.json")
            duplicate = package / "skills" / "customer-agent-module-audit.json"
            self.save(duplicate, first)
            with self.assertRaisesRegex(HarnessStarterError, "duplicate Skill identity"):
                verify_starter(package, registry_workspace=ROOT)

    def test_unknown_or_missing_provider_bindings_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            project_path = package / "harness-project.json"
            project = self.load(project_path)
            project["providers"]["test.run"] = "unknown.provider"
            self.save(project_path, project)
            with self.assertRaisesRegex(HarnessStarterError, "unknown Provider"):
                verify_starter(package, registry_workspace=ROOT)
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            project_path = package / "harness-project.json"
            project = self.load(project_path)
            del project["providers"]["ci.run.wait"]
            self.save(project_path, project)
            with self.assertRaisesRegex(HarnessStarterError, "no Provider binding"):
                verify_starter(package, registry_workspace=ROOT)

    def test_automatic_merge_policy_rejects_merge_capability(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            workflow_path = package / "workflows" / "customer-agent-repair-with-ci.json"
            workflow = self.load(workflow_path)
            workflow["requirements"]["capabilities"]["required"].append(
                "code_review.pull_request.merge"
            )
            self.save(workflow_path, workflow)
            with self.assertRaisesRegex(HarnessStarterError, "automatic_merge: false"):
                verify_starter(package, registry_workspace=ROOT)

    def test_unknown_entrypoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            package = self.copy_starter(Path(raw_dir))
            manifest_path = package / "starter.json"
            manifest = self.load(manifest_path)
            manifest["entrypoints"]["full_dev"] = "missing-workflow"
            self.save(manifest_path, manifest)
            with self.assertRaisesRegex(HarnessStarterError, "unknown Workflows"):
                verify_starter(package, registry_workspace=ROOT)


if __name__ == "__main__":
    unittest.main()
