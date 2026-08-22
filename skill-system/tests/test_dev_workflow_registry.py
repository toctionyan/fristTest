from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "dev-workflows.json"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from workflow_registry import (  # type: ignore
    WORKFLOW_REGISTRY_SCHEMA,
    WorkflowRegistryError,
    load_workflow_registry,
)


class DevWorkflowRegistryTest(unittest.TestCase):
    def workspace(self, payload: dict | None = None) -> Path:
        root = Path(tempfile.mkdtemp(prefix="workflow-registry-"))
        path = root / "skill-system/registry/dev-workflows.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            path.write_text(REGISTRY.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_registry_loads_explicit_and_reusable_workflows(self) -> None:
        workflows = load_workflow_registry(self.workspace())
        self.assertEqual(len(workflows), 13)
        self.assertEqual(workflows["architecture-review"].skills, ("architecture-options",))
        self.assertEqual(
            workflows["governed-repair"].skills,
            ("product-code-governance", "red-baseline-repair"),
        )
        self.assertEqual(
            workflows["governed-repair"].required_capabilities,
            ("workspace.write", "test.run", "quality.evaluate"),
        )
        self.assertIn(
            "code_review.pull_request.create",
            workflows["governed-repair"].optional_capabilities,
        )
        self.assertTrue(workflows["governed-repair"].write_governed)
        self.assertTrue(workflows["status-project"].status_first)

        repair_and_prove = workflows["repair-and-prove"]
        self.assertIsNotNone(repair_and_prove.graph)
        self.assertEqual(repair_and_prove.graph.start, "repair")
        self.assertEqual(repair_and_prove.graph.max_attempts_per_step, 8)
        self.assertEqual(repair_and_prove.graph.steps["focused-test"].use, "test.run")
        self.assertEqual(repair_and_prove.graph.steps["quality"].routes["red"], "repair")

        governed_merge = workflows["governed-merge"]
        self.assertEqual(governed_merge.skills, ())
        self.assertTrue(governed_merge.write_governed)
        self.assertEqual(
            governed_merge.required_capabilities,
            ("ci.run.wait", "code_review.pull_request.merge"),
        )
        self.assertEqual(governed_merge.optional_capabilities, ())
        self.assertIsNotNone(governed_merge.graph)
        self.assertEqual(governed_merge.graph.start, "wait-ci")
        self.assertEqual(governed_merge.graph.steps["wait-ci"].step_type, "external_wait")
        self.assertEqual(governed_merge.graph.steps["wait-ci"].use, "ci.run.wait")
        self.assertEqual(
            governed_merge.graph.steps["wait-ci"].routes["pending"],
            "WAITING_EXTERNAL",
        )
        self.assertEqual(governed_merge.graph.steps["wait-ci"].routes["green"], "merge")
        self.assertEqual(governed_merge.graph.steps["merge"].use, "code_review.pull_request.merge")
        self.assertEqual(governed_merge.graph.steps["merge"].routes["green"], "END")

        publication = workflows["publication-e2e"]
        self.assertEqual(publication.skills, ())
        self.assertTrue(publication.write_governed)
        self.assertEqual(
            publication.required_capabilities,
            (
                "vcs.commit.create",
                "code_review.pull_request.create",
                "ci.run.wait",
                "code_review.pull_request.merge",
                "publication.post_merge.validation.request",
                "publication.post_merge.validation.wait",
            ),
        )
        self.assertIsNotNone(publication.graph)
        self.assertEqual(publication.graph.start, "commit")
        self.assertEqual(publication.graph.steps["commit"].routes["green"], "create-pr")
        self.assertEqual(publication.graph.steps["create-pr"].routes["green"], "wait-ci")
        self.assertEqual(publication.graph.steps["wait-ci"].step_type, "external_wait")
        self.assertEqual(publication.graph.steps["wait-ci"].routes["green"], "merge")
        self.assertEqual(publication.graph.steps["merge"].routes["green"], "request-post-merge")
        self.assertEqual(
            publication.graph.steps["request-post-merge"].use,
            "publication.post_merge.validation.request",
        )
        self.assertEqual(
            publication.graph.steps["request-post-merge"].routes["green"],
            "wait-post-merge",
        )
        self.assertEqual(publication.graph.steps["wait-post-merge"].step_type, "external_wait")
        self.assertEqual(
            publication.graph.steps["wait-post-merge"].use,
            "publication.post_merge.validation.wait",
        )
        self.assertEqual(publication.graph.steps["wait-post-merge"].routes["green"], "END")

        full_development = workflows["harness-full-dev"]
        self.assertEqual(
            full_development.skills,
            ("product-code-governance", "architecture-options"),
        )
        self.assertTrue(full_development.write_governed)
        self.assertIsNone(full_development.graph)
        self.assertIn("workspace.write", full_development.required_capabilities)
        self.assertIn(
            "publication.post_merge.validation.wait",
            full_development.required_capabilities,
        )

    def test_registry_rejects_target_binding_fields(self) -> None:
        payload = {
            "schema": WORKFLOW_REGISTRY_SCHEMA,
            "workflows": [
                {
                    "workflow_id": "bad",
                    "request_class": "DESIGN",
                    "skills": ["architecture-options"],
                    "mode": "READ_ONLY",
                    "target_id": "repo:hard-coded",
                }
            ],
        }
        with self.assertRaisesRegex(WorkflowRegistryError, "target-independent"):
            load_workflow_registry(self.workspace(payload))

    def test_registry_rejects_duplicate_skill_authority(self) -> None:
        payload = {
            "schema": WORKFLOW_REGISTRY_SCHEMA,
            "workflows": [
                {
                    "workflow_id": "bad",
                    "request_class": "DESIGN",
                    "skills": ["architecture-options", "architecture-options"],
                    "mode": "READ_ONLY",
                }
            ],
        }
        with self.assertRaisesRegex(WorkflowRegistryError, "must contain unique values"):
            load_workflow_registry(self.workspace(payload))

    def test_registry_rejects_direct_provider_requirements(self) -> None:
        payload = {
            "schema": WORKFLOW_REGISTRY_SCHEMA,
            "workflows": [
                {
                    "workflow_id": "bad",
                    "request_class": "DESIGN",
                    "skills": ["architecture-options"],
                    "mode": "READ_ONLY",
                    "requirements": {"integrations": ["github.actions"]},
                }
            ],
        }
        with self.assertRaisesRegex(WorkflowRegistryError, "provider-neutral"):
            load_workflow_registry(self.workspace(payload))

    def test_registry_rejects_provider_named_capabilities(self) -> None:
        payload = {
            "schema": WORKFLOW_REGISTRY_SCHEMA,
            "workflows": [
                {
                    "workflow_id": "bad",
                    "request_class": "DESIGN",
                    "skills": ["architecture-options"],
                    "mode": "READ_ONLY",
                    "requirements": {
                        "capabilities": {
                            "required": ["github.actions.wait"],
                            "optional": [],
                        }
                    },
                }
            ],
        }
        with self.assertRaisesRegex(WorkflowRegistryError, "provider-neutral capabilities"):
            load_workflow_registry(self.workspace(payload))

    def test_registry_allows_capability_only_workflow_contract(self) -> None:
        payload = {
            "schema": WORKFLOW_REGISTRY_SCHEMA,
            "workflows": [
                {
                    "workflow_id": "publish-only",
                    "request_class": "PUBLISH",
                    "skills": [],
                    "mode": "WRITE_GOVERNED",
                    "requirements": {
                        "capabilities": {
                            "required": ["code_review.pull_request.create"],
                            "optional": ["ci.run.wait"],
                        }
                    },
                }
            ],
        }
        workflow = load_workflow_registry(self.workspace(payload))["publish-only"]
        self.assertEqual(workflow.skills, ())
        self.assertEqual(workflow.required_capabilities, ("code_review.pull_request.create",))
        self.assertEqual(workflow.optional_capabilities, ("ci.run.wait",))

    def test_registry_rejects_graph_capability_that_is_only_optional(self) -> None:
        payload = {
            "schema": WORKFLOW_REGISTRY_SCHEMA,
            "workflows": [
                {
                    "workflow_id": "bad-graph",
                    "request_class": "PUBLISH",
                    "skills": [],
                    "mode": "WRITE_GOVERNED",
                    "write_governed": True,
                    "requirements": {
                        "capabilities": {
                            "required": [],
                            "optional": ["ci.run.wait"],
                        }
                    },
                    "graph": {
                        "start": "wait",
                        "steps": {
                            "wait": {
                                "type": "external_wait",
                                "use": "ci.run.wait",
                                "routes": {"pending": "WAITING_EXTERNAL"},
                            }
                        },
                    },
                }
            ],
        }
        with self.assertRaisesRegex(WorkflowRegistryError, "must be declared required"):
            load_workflow_registry(self.workspace(payload))

    def test_registry_rejects_wrong_schema(self) -> None:
        payload = {"schema": "other@1", "workflows": []}
        with self.assertRaisesRegex(WorkflowRegistryError, "schema"):
            load_workflow_registry(self.workspace(payload))


if __name__ == "__main__":
    unittest.main()
