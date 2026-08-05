from __future__ import annotations

import importlib.util
import json
import os
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


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _process_is_live(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        try:
            fields = stat_path.read_text(encoding="utf-8").split()
        except OSError:
            return False
        return len(fields) >= 3 and fields[2] != "Z"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class LocalFirstLoopCLITests(unittest.TestCase):
    def _workspace_and_spec(self, root: Path) -> tuple[Path, Path]:
        workspace = root / "repo"
        workspace.mkdir(parents=True)
        _git(workspace, "init", "-q")
        _git(workspace, "config", "user.email", "local-first@example.invalid")
        _git(workspace, "config", "user.name", "Local First Test")
        (workspace / "source.txt").write_text("baseline\n", encoding="utf-8")
        _git(workspace, "add", "source.txt")
        _git(workspace, "commit", "-q", "-m", "baseline")
        _git(workspace, "switch", "-q", "-c", "agent/cli-local-first")
        base_sha = _git(workspace, "rev-parse", "HEAD")

        gate_command = [sys.executable, "-c", "raise SystemExit(0)"]
        payload = {
            "schema_version": 1,
            "task_id": "cli-local-first",
            "change_id": "change-cli-local-first",
            "base_sha": base_sha,
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
        path = root / "task.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return workspace, path

    def _init(self, workspace: Path, spec: Path, state: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "init",
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

    def _run_local(self, workspace: Path, spec: Path, state: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
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

    def test_cli_runs_local_gates_then_admits_clean_committed_upload(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, spec = self._workspace_and_spec(root)
            state = workspace / ".quality/task-run.json"

            initialized = self._init(workspace, spec, state)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (workspace / "source.txt").write_text("candidate-updated\n", encoding="utf-8")

            local = self._run_local(workspace, spec, state)
            self.assertEqual(local.returncode, 0, local.stderr)
            _git(workspace, "add", "source.txt")
            _git(workspace, "commit", "-q", "-m", "candidate")
            candidate_sha = _git(workspace, "rev-parse", "HEAD")

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
                    candidate_sha,
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
            root = Path(directory)
            workspace, spec = self._workspace_and_spec(root)
            payload = json.loads(spec.read_text(encoding="utf-8"))
            payload["gates"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(7)"]
            spec.write_text(json.dumps(payload), encoding="utf-8")
            state = workspace / ".quality/task-run.json"
            initialized = self._init(workspace, spec, state)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            (workspace / "source.txt").write_text("candidate-updated\n", encoding="utf-8")
            local = self._run_local(workspace, spec, state)
            self.assertEqual(local.returncode, 1)
            payload = json.loads(local.stdout)
            self.assertEqual(payload["phase"], "LOCAL_TARGETED_FAILED")
            self.assertFalse(payload["conditions"]["local_targeted_green"])

    def test_cli_rejects_task_spec_drift_after_init(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, spec = self._workspace_and_spec(root)
            state = workspace / ".quality/task-run.json"
            initialized = self._init(workspace, spec, state)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            payload = json.loads(spec.read_text(encoding="utf-8"))
            payload["gates"][0]["timeout_seconds"] = 99
            spec.write_text(json.dumps(payload), encoding="utf-8")
            run = self._run_local(workspace, spec, state)
            self.assertEqual(run.returncode, 78)
            result = json.loads(run.stdout)
            self.assertIn("task spec changed", result["error"])

    def test_cli_init_rejects_non_git_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "plain"
            workspace.mkdir(parents=True)
            (workspace / "source.txt").write_text("baseline\n", encoding="utf-8")
            _, template = self._workspace_and_spec(root / "template")
            spec = root / "task.json"
            spec.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            state = workspace / ".quality/task-run.json"
            initialized = self._init(workspace, spec, state)
            self.assertEqual(initialized.returncode, 78)
            payload = json.loads(initialized.stdout)
            self.assertIn("Git identity check failed", payload["error"])

    def test_cli_init_rejects_base_sha_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, spec = self._workspace_and_spec(root)
            payload = json.loads(spec.read_text(encoding="utf-8"))
            payload["base_sha"] = "a" * 40
            spec.write_text(json.dumps(payload), encoding="utf-8")
            initialized = self._init(workspace, spec, workspace / ".quality/task-run.json")
            self.assertEqual(initialized.returncode, 78)
            result = json.loads(initialized.stdout)
            self.assertIn("Git identity check failed", result["error"])

    def test_cli_init_rejects_branch_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, spec = self._workspace_and_spec(root)
            payload = json.loads(spec.read_text(encoding="utf-8"))
            payload["branch"] = "agent/different-branch"
            spec.write_text(json.dumps(payload), encoding="utf-8")
            initialized = self._init(workspace, spec, workspace / ".quality/task-run.json")
            self.assertEqual(initialized.returncode, 78)
            result = json.loads(initialized.stdout)
            self.assertIn("branch binding mismatch", result["error"])

    def test_cli_admit_rejects_dirty_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, spec = self._workspace_and_spec(root)
            state = workspace / ".quality/task-run.json"
            self.assertEqual(self._init(workspace, spec, state).returncode, 0)
            (workspace / "source.txt").write_text("candidate-updated\n", encoding="utf-8")
            self.assertEqual(self._run_local(workspace, spec, state).returncode, 0)
            head_sha = _git(workspace, "rev-parse", "HEAD")
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
                    head_sha,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(admitted.returncode, 78)
            payload = json.loads(admitted.stdout)
            self.assertIn("worktree must be clean", payload["error"])

    def test_gate_reaps_orphan_descendants_without_hanging(self) -> None:
        module = _load_script()
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            evidence = workspace / "evidence"
            child_pid_path = workspace / "child.pid"
            row = {
                "id": "targeted",
                "argv": [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib, subprocess, sys; "
                        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
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
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2.0
            while _process_is_live(child_pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(_process_is_live(child_pid), "orphan descendant is still running")


if __name__ == "__main__":
    unittest.main()
