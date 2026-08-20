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

from workflow_activation import WorkflowActivationError, activate_workflow  # type: ignore


class WorkflowActivationAuthorityTest(unittest.TestCase):
    def workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="workflow-activation-"))
        target = root / "skill-system/registry"
        target.mkdir(parents=True, exist_ok=True)
        for name in ("capabilities.json", "executors.json", "integrations.json"):
            shutil.copy2(REGISTRY_DIR / name, target / name)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_mutating_capability_requires_write_governed_workflow(self) -> None:
        workspace = self.workspace()
        workflow_registry = workspace / "skill-system/registry/dev-workflows.json"
        workflow_registry.write_text(
            json.dumps(
                {
                    "schema": "dev-workflow-registry@1",
                    "workflows": [
                        {
                            "workflow_id": "unsafe",
                            "request_class": "DESIGN",
                            "skills": ["architecture-options"],
                            "mode": "READ_ONLY",
                            "write_governed": False,
                            "requirements": {
                                "capabilities": {
                                    "required": ["code_review.pull_request.create"],
                                    "optional": [],
                                }
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(WorkflowActivationError, "mutating capabilities"):
            activate_workflow(
                workspace,
                workflow_id="unsafe",
                available_provider_ids=["github.code_review"],
            )

    def test_capability_binding_never_grants_write_authority(self) -> None:
        workspace = self.workspace()
        shutil.copy2(REGISTRY_DIR / "dev-workflows.json", workspace / "skill-system/registry/dev-workflows.json")
        activation = activate_workflow(
            workspace,
            workflow_id="governed-repair",
            available_provider_ids=[
                "local.workspace",
                "local.process",
                "local.git",
                "github.code_review",
                "github.actions",
            ],
        ).as_dict()
        self.assertEqual(activation["status"], "PASS")
        self.assertFalse(activation["policy"]["capability_resolution_grants_write_authority"])
        self.assertFalse(activation["policy"]["write_authority_changed"])


if __name__ == "__main__":
    unittest.main()
