from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import composition_bootstrap as composition_module  # type: ignore
from composition_bootstrap import (  # type: ignore
    CompositionBootstrap,
    CompositionBootstrapError,
    load_composition_registry,
)


class CompositionBootstrapTest(unittest.TestCase):
    @property
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def temp_workspace(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="composition-bootstrap-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_repo_composition_resolves_exact_workflow_and_required_provider_bindings(self) -> None:
        assembly = CompositionBootstrap(self.repo_root).assemble("repair-and-prove-local")
        self.assertTrue(assembly.ready)
        self.assertEqual(assembly.composition.workflow_id, "repair-and-prove")

        bindings = {
            row.capability_id: row.provider_id
            for row in assembly.activation.capability_preflight.required_bindings
        }
        self.assertEqual(bindings["workspace.write"], "local.workspace")
        self.assertEqual(bindings["test.run"], "local.process")
        self.assertEqual(bindings["quality.evaluate"], "local.process")

    def test_runtime_input_preserves_authority_boundaries_and_profile_allowlists(self) -> None:
        runtime = CompositionBootstrap(self.repo_root).build_runtime_input("repair-and-prove-local")
        self.assertEqual(runtime["status"], "PASS")
        self.assertEqual(runtime["completion_authority"], "TaskRun")
        self.assertFalse(runtime["authority_effect"])
        self.assertFalse(runtime["write_authority_granted"])
        self.assertFalse(runtime["provider_activation_granted"])
        self.assertFalse(runtime["completion_authority_changed"])

        allowed_profiles = runtime["allowed_profiles"]
        self.assertIn("product-quality-quick", allowed_profiles["quality.evaluate"])
        for profiles in allowed_profiles.values():
            for profile_id in profiles:
                profile_path = self.repo_root / "skill-system" / "profiles" / f"{profile_id}.json"
                payload = json.loads(profile_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["id"], profile_id)

    def test_unknown_composition_fails_closed_without_similarity_fallback(self) -> None:
        with self.assertRaisesRegex(CompositionBootstrapError, "unknown composition_id"):
            CompositionBootstrap(self.repo_root).resolve("repair-and-prove-loca")

    def test_registry_rejects_non_taskrun_completion_authority(self) -> None:
        workspace = self.temp_workspace()
        registry_path = workspace / "bad-compositions.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema": "harness-composition-registry@1",
                    "compositions": [
                        {
                            "composition_id": "bad",
                            "workflow_id": "repair-and-prove",
                            "available_provider_ids": ["local.workspace"],
                            "provider_preferences": {},
                            "allowed_profiles": {},
                            "write_authority_required": True,
                            "completion_authority": "LangGraph",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with patch.object(composition_module, "COMPOSITION_REGISTRY_PATH", Path("bad-compositions.json")):
            with self.assertRaisesRegex(CompositionBootstrapError, "must remain 'TaskRun'"):
                load_composition_registry(workspace)


if __name__ == "__main__":
    unittest.main()
