from __future__ import annotations

import importlib.util
import json
import subprocess
import time
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "local_first_loop.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("local_first_loop_test_module", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalFirstLoopCLITests(unittest.TestCase):
    def _spec(self, workspace: Path) -> Path:
        gate_command = [sys.executable, "-c", "raise SystemExit(0)"]
        payload = {
            "schema_version": 1,
            "task_id": "cli-local-first",
            "change_id": "change-cli-local-first",
            "base_sha": "a" * 40,
            "branch": "agent/cli-local-first",
            "patch_owner": "product-implementer",
            "allowed_paths": ["source.txt"],
            "expected_changed_paths": ["source.txt"],
            "target": {"goal": "prove local-first CLI"},
            "gates": [
                {"id": gate, "argv": gate_command, "timeout_seconds": 20}
                for gate in ("targeted", "module", "static", "quick", "review")
            ],
        }
        path = workspace / "task.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_cli_runs_local_gates_then_admits_upload(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "source.txt").write_text("candidate\n", encoding="utf-8")
            spec = self._spec(workspace)
            state = workspace / ".quality/task-run.json"

            init = subprocess.run(
                [sys.executable, str(SCRIPT), "init", "--workspace", str(workspace), "--spec", str(spec), "--state", str(state)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            (workspace / "source.txt").write_text("candidate-updated\n", encoding="utf-8")

            local = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "run-local",
                    "--workspace",
                    str(workspace),
                    "--spec",
                    str(spec),
                    "--state",
                    str(state),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(local.returncode, 0, local.stderr)

            admitted = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "admit-upload",
                    "--workspace",
                    str(workspace),
                    "--state",
                    str(state),
                    "--head-sha",
                    "b" * 40,
                    "--changed-path",
                    "source.txt",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(admitted.returncode, 0, admitted.stderr)
            payload = json.loads(admitted.stdout)
            self.assertTrue(payload["decision"]["allowed"])
            self.assertEqual(payload["status"]["phase"], "READY_FOR_CI")

    def test_cli_stops_on_first_failed_local_gate(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "source.txt").write_text("candidate\n", encoding="utf-8")
            spec = self._spec(workspace)
            payload = json.loads(spec.read_text(encoding="utf-8"))
            payload["gates"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(7)"]
            spec.write_text(json.dumps(payload), encoding="utf-8")
            state = workspace / ".quality/task-run.json"
            subprocess.run(
                [sys.executable, str(SCRIPT), "init", "--workspace", str(workspace), "--spec", str(spec), "--state", str(state)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            (workspace / "source.txt").write_text("candidate-updated\n", encoding="utf-8")
            local = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "run-local",
                    "--workspace",
                    str(workspace),
                    "--spec",
                    str(spec),
                    "--state",
                    str(state),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(local.returncode, 1)
            payload = json.loads(local.stdout)
            self.assertEqual(payload["phase"], "LOCAL_TARGETED_FAILED")
            self.assertFalse(payload["conditions"]["local_targeted_green"])

    def test_cli_rejects_task_spec_drift_after_init(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "source.txt").write_text("baseline\n", encoding="utf-8")
            spec = self._spec(workspace)
            state = workspace / ".quality/task-run.json"
            initialized = subprocess.run(
                [sys.executable, str(SCRIPT), "init", "--workspace", str(workspace), "--spec", str(spec), "--state", str(state)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            payload = json.loads(spec.read_text(encoding="utf-8"))
            payload["gates"][0]["timeout_seconds"] = 99
            spec.write_text(json.dumps(payload), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(SCRIPT), "run-local", "--workspace", str(workspace), "--spec", str(spec), "--state", str(state)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(run.returncode, 78)
            result = json.loads(run.stdout)
            self.assertIn("task spec changed", result["error"])

    def test_gate_reaps_orphan_descendants_without_hanging(self) -> None:
        module = _load_script()
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            evidence = workspace / "evidence"
            row = {
                "id": "targeted",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import subprocess, sys; "
                        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                        "raise SystemExit(0)"
                    ),
                ],
                "timeout_seconds": 20,
            }
            started = time.monotonic()
            passed, refs, result = module._run_gate(workspace, row, evidence)
            elapsed = time.monotonic() - started
            self.assertTrue(passed, result)
            self.assertFalse(result["timed_out"])
            self.assertLess(elapsed, 5.0)
            self.assertEqual(len(refs), 3)


if __name__ == "__main__":
    unittest.main()
