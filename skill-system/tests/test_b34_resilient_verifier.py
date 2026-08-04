from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_b34_resilient_task_harness.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_b34_resilient_task_harness_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class B34ResilientVerifierTest(unittest.TestCase):
    def test_partial_run_resumes_without_reexecuting_durable_pass(self) -> None:
        verifier = _load_verifier()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            checks = [
                {
                    "name": "check_one",
                    "command": [sys.executable, "-c", "print('one')"],
                    "cwd": workspace,
                    "timeout": 20,
                },
                {
                    "name": "check_two",
                    "command": [sys.executable, "-c", "print('two')"],
                    "cwd": workspace,
                    "timeout": 20,
                },
            ]
            with (
                mock.patch.object(verifier, "ROOT", workspace),
                mock.patch.object(verifier, "SERVICE", workspace / "service"),
                mock.patch.object(verifier, "check_definitions", return_value=checks),
                mock.patch.object(verifier, "source_fingerprint", return_value="source-a"),
            ):
                original_argv = sys.argv
                try:
                    sys.argv = [SCRIPT.name, "--reset", "--max-checks", "1"]
                    first_output = io.StringIO()
                    with contextlib.redirect_stdout(first_output):
                        self.assertEqual(verifier.main(), 0)

                    task_files = list((workspace / ".quality/task-runs").glob("*.json"))
                    self.assertEqual(len(task_files), 1)
                    partial = json.loads(task_files[0].read_text(encoding="utf-8"))
                    self.assertEqual(partial["status"], "VALIDATING")
                    self.assertTrue(partial["conditions"]["check_one"]["satisfied"])
                    self.assertFalse(partial["conditions"]["check_two"]["satisfied"])
                    self.assertEqual(len(partial["action_attempts"]), 1)

                    sys.argv = [SCRIPT.name, "--max-checks", "1"]
                    second_output = io.StringIO()
                    with contextlib.redirect_stdout(second_output):
                        self.assertEqual(verifier.main(), 0)
                    self.assertIn("SKIP check_one (durable PASS)", second_output.getvalue())

                    completed = json.loads(task_files[0].read_text(encoding="utf-8"))
                    self.assertEqual(completed["status"], "COMPLETED")
                    self.assertEqual(completed["phase"], "COMPLETED")
                    self.assertEqual(len(completed["action_attempts"]), 2)
                    self.assertTrue(completed["conditions"]["check_two"]["satisfied"])
                finally:
                    sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
