from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_wp08_certification.py"


def _runner():
    spec = importlib.util.spec_from_file_location("wp08_certification_liveness_test_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env(state_file: Path, **overrides: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "WP08_CERTIFICATION_STATE_FILE": str(state_file),
            "WP08_CURRENT_BATCH_ID": "test-batch",
            "WP08_CURRENT_BATCH_TITLE": "Test batch",
            "WP08_CURRENT_BATCH_TIMEOUT": "5",
            "WP08_BATCH_INDEX": "2",
            "WP08_BATCH_TOTAL": "4",
            "WP08_HEARTBEAT_SECONDS": "0.03",
            "WP08_STALL_WARNING_SECONDS": "0.20",
            "WP08_STALL_TIMEOUT_SECONDS": "0.60",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.update(overrides)
    return env


class Wp08CertificationLivenessTests(unittest.TestCase):
    def test_heartbeat_proves_process_alive_and_streams_child_progress(self) -> None:
        runner = _runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_file = root / "state" / "wp08-state.json"
            runner._atomic_json(state_file, {"contract": runner.CONTRACT, "status": "RUNNING"})
            capture = io.StringIO()
            command = [
                sys.executable,
                "-u",
                "-c",
                "import time; print('child-start', flush=True); time.sleep(0.12); print('child-done', flush=True)",
            ]
            with redirect_stdout(capture):
                returncode, stdout, stderr, timed_out = runner._run_process(
                    command,
                    cwd=root,
                    env=_env(state_file),
                    timeout_seconds=3,
                )

            self.assertEqual(returncode, 0)
            self.assertFalse(timed_out)
            self.assertEqual(stderr, "")
            self.assertIn("child-start", stdout)
            self.assertIn("child-done", stdout)
            visible = capture.getvalue()
            self.assertIn("[WP08 HEARTBEAT]", visible)
            self.assertIn("child-start", visible)
            self.assertIn('"index": 2', visible)
            self.assertIn('"total": 4', visible)

            liveness = json.loads(
                state_file.with_name("wp08-liveness.json").read_text(encoding="utf-8")
            )
            self.assertEqual(liveness["liveness_status"], "BATCH_COMPLETED")
            self.assertFalse(liveness["child_process_alive"])
            self.assertGreaterEqual(liveness["progress_event_count"], 2)
            self.assertEqual(liveness["current_batch"]["id"], "test-batch")

    def test_no_progress_becomes_suspected_stall_then_fails_closed(self) -> None:
        runner = _runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_file = root / "state" / "wp08-state.json"
            runner._atomic_json(state_file, {"contract": runner.CONTRACT, "status": "RUNNING"})
            capture = io.StringIO()
            env = _env(
                state_file,
                WP08_HEARTBEAT_SECONDS="0.02",
                WP08_STALL_WARNING_SECONDS="0.06",
                WP08_STALL_TIMEOUT_SECONDS="0.14",
            )
            command = [sys.executable, "-u", "-c", "import time; time.sleep(2)"]
            with redirect_stdout(capture):
                returncode, stdout, stderr, timed_out = runner._run_process(
                    command,
                    cwd=root,
                    env=env,
                    timeout_seconds=3,
                )

            self.assertEqual(returncode, 124)
            self.assertTrue(timed_out)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")
            visible = capture.getvalue()
            self.assertIn("SUSPECTED_STALL", visible)
            self.assertIn("[WP08 STALL]", visible)
            self.assertIn("no_progress_stall", visible)

            liveness = json.loads(
                state_file.with_name("wp08-liveness.json").read_text(encoding="utf-8")
            )
            self.assertEqual(liveness["liveness_status"], "STALL_TIMEOUT")
            self.assertEqual(liveness["termination_reason"], "no_progress_stall")
            self.assertFalse(liveness["child_process_alive"])

    def test_run_certification_persists_batch_progress_and_final_completion(self) -> None:
        runner = _runner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "deployment" / "ci" / "wp08-certification-batches.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "contract": "wp08-certification-batches@1",
                        "batches": [
                            {
                                "id": "one",
                                "title": "One",
                                "timeout_seconds": 3,
                                "required": True,
                                "command": [
                                    sys.executable,
                                    "-u",
                                    "-c",
                                    "import json,time; print('working', flush=True); time.sleep(0.05); print(json.dumps({'status':'PASS'}), flush=True)",
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            evidence = root / "evidence"
            state_file = root / "state" / "wp08-state.json"
            env = _env(
                state_file,
                WP08_HEARTBEAT_SECONDS="0.02",
                WP08_STALL_WARNING_SECONDS="0.20",
                WP08_STALL_TIMEOUT_SECONDS="0.60",
            )
            capture = io.StringIO()
            with redirect_stdout(capture):
                state, exit_code = runner.run_certification(
                    workspace=root,
                    config_path=config,
                    evidence_dir=evidence,
                    state_file=state_file,
                    resume=False,
                    environment=env,
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(state["status"], "PASS")
            self.assertEqual(state["liveness_status"], "COMPLETED")
            self.assertIsNone(state["current_batch"])
            self.assertFalse(state["child_process_alive"])
            self.assertIn("batch_started", capture.getvalue())
            self.assertIn("batch_completed", capture.getvalue())

            persisted = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "PASS")
            self.assertEqual(persisted["liveness_status"], "COMPLETED")
            summary = json.loads(
                (evidence / "wp08-certification-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["summary"]["PASS"], 1)
            final_liveness = json.loads(
                state_file.with_name("wp08-liveness.json").read_text(encoding="utf-8")
            )
            self.assertEqual(final_liveness["liveness_status"], "COMPLETED")
            self.assertEqual(final_liveness["certification_status"], "PASS")


if __name__ == "__main__":
    unittest.main()
