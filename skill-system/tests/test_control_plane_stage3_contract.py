from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_repair_stage3 as stage3  # noqa: E402


class ControlPlaneStage3ContractTests(unittest.TestCase):
    def test_engineering_verifier_maps_to_skill_control_plane_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            components = stage3._target_components(
                ["scripts/verify_engineering_bounded_autonomy_closure.py"],
                Path(temp),
            )
        self.assertEqual(components, ["skill-control-plane"])

    def test_skill_control_plane_target_runs_existing_profile(self) -> None:
        workspace = Path("/tmp/example-workspace")
        command, cwd = stage3._component_command("skill-control-plane", workspace)
        self.assertEqual(cwd, workspace)
        self.assertEqual(
            command,
            [
                str(workspace / "services/agent-service/.venv/bin/python"),
                "-B",
                "skill-system/controller/profile_runner.py",
                "skill-control-plane",
            ],
        )
        env = stage3._targeted_env("skill-control-plane")
        self.assertIsInstance(env, dict)


if __name__ == "__main__":
    unittest.main()
