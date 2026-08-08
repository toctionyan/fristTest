from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import profile_runner  # type: ignore


class ProfileWorkspaceAuthorityTest(unittest.TestCase):
    def test_product_profiles_explicitly_bind_product_workspace(self) -> None:
        for profile_id in ("product-contract", "product-quality-quick"):
            profile = profile_runner.load_profile(profile_id)
            commands = profile.get("commands") or []
            self.assertTrue(commands)
            flattened = [str(value) for command in commands for value in command]
            self.assertIn("--workspace-root", flattened)
            self.assertIn("{workspace_root}", flattened)

    def test_run_resolves_workspace_placeholder_but_keeps_controller_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw).resolve()
            fake_profile = {
                "id": "workspace-aware",
                "commands": [[
                    "{python}", "-B", "bridge.py",
                    "--workspace-root", "{workspace_root}",
                ]],
            }
            completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with patch.object(profile_runner, "expand_profiles", return_value=[fake_profile]), \
                 patch.object(profile_runner.subprocess, "run", return_value=completed) as run:
                result = profile_runner.run("workspace-aware", workspace=workspace)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["workspace_root"], str(workspace))
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], sys.executable)
            self.assertEqual(argv[-2:], ["--workspace-root", str(workspace)])
            self.assertEqual(run.call_args.kwargs["cwd"], profile_runner.ROOT)

    def test_run_defaults_workspace_to_callers_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw).resolve()
            fake_profile = {
                "id": "workspace-aware",
                "commands": [["{python}", "-c", "print('ok')", "{workspace_root}"]],
            }
            completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            old = Path.cwd()
            try:
                os.chdir(workspace)
                with patch.object(profile_runner, "expand_profiles", return_value=[fake_profile]), \
                     patch.object(profile_runner.subprocess, "run", return_value=completed) as run:
                    result = profile_runner.run("workspace-aware")
            finally:
                os.chdir(old)
            self.assertEqual(result["workspace_root"], str(workspace))
            self.assertEqual(run.call_args.args[0][-1], str(workspace))

    def test_missing_explicit_workspace_fails_closed(self) -> None:
        missing = Path(tempfile.gettempdir()) / "profile-runner-missing-product-workspace"
        if missing.exists():
            self.fail(f"unexpected test path exists: {missing}")
        with self.assertRaisesRegex(ValueError, "profile workspace root does not exist"):
            profile_runner.run("product-contract", workspace=missing)


if __name__ == "__main__":
    unittest.main()
