from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class Stage2B1AcceptanceE2ETests(unittest.TestCase):
    def test_real_cli_requires_explicit_evidence_and_never_creates_taskrun(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage2b1-cli-") as raw:
            workspace = Path(raw)
            before = sorted(path.name for path in workspace.iterdir())
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "stage2b1_acceptance.py"),
                    "--workspace", str(workspace),
                    "--producer-bundle", str(workspace / "producer-bundle"),
                    "--artifact-archive", str(workspace / "payload.zip"),
                    "--source-run-id", "17",
                    "--source-run-attempt", "1",
                    "--artifact-id", "123",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("BLOCKED", result.stderr)
            self.assertEqual(before, sorted(path.name for path in workspace.iterdir()))
            self.assertFalse((workspace / "task-run.json").exists())

    def test_old_taskrun_writer_arguments_are_not_a_cli_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage2b1-cli-old-") as raw:
            workspace = Path(raw)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "stage2b1_acceptance.py"),
                    "--workspace", str(workspace),
                    "--task-run", str(workspace / "task-run.json"),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("required", result.stderr)


if __name__ == "__main__":
    unittest.main()
