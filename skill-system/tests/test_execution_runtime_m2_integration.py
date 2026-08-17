from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for entry in (CONTROLLER, SCRIPTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import execution_runtime  # noqa: E402
from quality_control import environment as quality_environment  # noqa: E402
import run_wp08_certification as wp08  # noqa: E402


def _lease(*, scope_kind: str, scope_id: str, expired: bool = False) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    heartbeat = now - timedelta(seconds=2) if expired else now - timedelta(milliseconds=10)
    expires = now - timedelta(seconds=1) if expired else now + timedelta(seconds=2)
    return {
        "contract": execution_runtime.EXTERNAL_WAIT_CONTRACT,
        "status": "WAITING_EXTERNAL",
        "heartbeat_at": heartbeat.isoformat(),
        "expires_at": expires.isoformat(),
        "external_ref": {"kind": "provider_request", "id": "req-123"},
        "scope": {"kind": scope_kind, "id": scope_id},
    }


class ExecutionRuntimeM2Tests(unittest.TestCase):
    def test_valid_external_wait_suppresses_stall_but_not_overall_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lease_path = root / "wait.json"
            lease_path.write_text(json.dumps(_lease(scope_kind="test", scope_id="one")), encoding="utf-8")
            heartbeats: list[dict[str, object]] = []
            result = execution_runtime.run_streaming_command(
                [sys.executable, "-c", "import time; time.sleep(0.4)"],
                cwd=root,
                heartbeat_seconds=0.02,
                stall_warning_seconds=0.03,
                stall_timeout_seconds=0.08,
                timeout_seconds=0.18,
                on_heartbeat=heartbeats.append,
                external_wait_probe=execution_runtime.external_wait_file_probe(
                    lease_path,
                    expected_scope={"kind": "test", "id": "one"},
                ),
            )
            self.assertTrue(result["timed_out"])
            self.assertFalse(result["stall_timed_out"])
            self.assertEqual(result["termination_reason"], "command_timeout")
            self.assertTrue(any(row.get("liveness_status") == "RUNNING_WAITING_EXTERNAL" for row in heartbeats))

    def test_expired_external_wait_does_not_suppress_stall_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lease_path = root / "wait.json"
            lease_path.write_text(json.dumps(_lease(scope_kind="test", scope_id="two", expired=True)), encoding="utf-8")
            result = execution_runtime.run_streaming_command(
                [sys.executable, "-c", "import time; time.sleep(0.4)"],
                cwd=root,
                heartbeat_seconds=0.02,
                stall_warning_seconds=0.03,
                stall_timeout_seconds=0.08,
                timeout_seconds=0.3,
                external_wait_probe=execution_runtime.external_wait_file_probe(
                    lease_path,
                    expected_scope={"kind": "test", "id": "two"},
                ),
            )
            self.assertTrue(result["timed_out"])
            self.assertTrue(result["stall_timed_out"])
            self.assertEqual(result["termination_reason"], "no_progress_stall")
            self.assertEqual(result["liveness_status"], "STALL_TIMEOUT")

    def test_slow_observer_cannot_skip_suspected_stall_before_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            heartbeats: list[dict[str, object]] = []

            def slow_first_observer(payload: dict[str, object]) -> None:
                heartbeats.append(dict(payload))
                if len(heartbeats) == 1:
                    # Force the runtime to resume after both the warning and stall
                    # thresholds. The warning lifecycle must still be observable.
                    time.sleep(0.12)

            result = execution_runtime.run_streaming_command(
                [sys.executable, "-c", "import time; time.sleep(0.5)"],
                cwd=root,
                heartbeat_seconds=0.02,
                stall_warning_seconds=0.03,
                stall_timeout_seconds=0.08,
                timeout_seconds=0.4,
                on_heartbeat=slow_first_observer,
            )
            statuses = [str(row.get("liveness_status") or "") for row in heartbeats]
            self.assertTrue(result["stall_timed_out"])
            self.assertIn(execution_runtime.LIVENESS_SUSPECTED_STALL, statuses)
            self.assertIn(execution_runtime.LIVENESS_STALL_TIMEOUT, statuses)
            self.assertLess(
                statuses.index(execution_runtime.LIVENESS_SUSPECTED_STALL),
                statuses.index(execution_runtime.LIVENESS_STALL_TIMEOUT),
            )

    def test_quality_shell_preserves_output_timeout_and_liveness_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / "evidence"
            result = quality_environment._run_shell(
                root,
                evidence,
                "quick",
                {
                    "id": "compat-gate",
                    "argv": [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                    "timeout_seconds": 2,
                },
            )
            self.assertEqual(result["exit_code"], 3)
            self.assertEqual(result["stdout"], "out\n")
            self.assertEqual(result["stderr"], "err\n")
            self.assertEqual(result["metadata"]["argv"][0], sys.executable)
            liveness = json.loads((evidence / "liveness" / "compat-gate.json").read_text(encoding="utf-8"))
            self.assertEqual(liveness["contract"], "quality-gate-liveness@1")
            self.assertEqual(liveness["liveness_status"], "FAILED")

            timeout_result = quality_environment._run_shell(
                root,
                evidence,
                "quick",
                {
                    "id": "timeout-gate",
                    "argv": [sys.executable, "-c", "import time; time.sleep(2)"],
                    "timeout_seconds": 1,
                },
            )
            self.assertEqual(timeout_result["exit_code"], 124)
            self.assertIn("quality_loop_step_timeout_after_1s", timeout_result["stderr"])

    def test_wp08_without_external_wait_retains_stall_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_file = root / "state.json"
            state_file.write_text("{}\n", encoding="utf-8")
            env = {
                "WP08_CERTIFICATION_STATE_FILE": str(state_file),
                "WP08_CURRENT_BATCH_ID": "batch-a",
                "WP08_CURRENT_BATCH_TITLE": "Batch A",
                "WP08_CURRENT_BATCH_TIMEOUT": "1",
                "WP08_BATCH_INDEX": "1",
                "WP08_BATCH_TOTAL": "1",
                "WP08_HEARTBEAT_SECONDS": "0.02",
                "WP08_STALL_WARNING_SECONDS": "0.03",
                "WP08_STALL_TIMEOUT_SECONDS": "0.08",
            }
            code, stdout, stderr, timed_out = wp08._run_process(
                [sys.executable, "-c", "import time; time.sleep(0.4)"],
                cwd=root,
                env=env,
                timeout_seconds=0.3,
            )
            self.assertEqual(code, 124)
            self.assertTrue(timed_out)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")
            liveness = json.loads((root / "wp08-liveness.json").read_text(encoding="utf-8"))
            self.assertEqual(liveness["liveness_status"], "STALL_TIMEOUT")
            self.assertEqual(liveness["termination_reason"], "no_progress_stall")

    def test_wp08_valid_external_wait_reaches_batch_timeout_not_stall(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_file = root / "state.json"
            state_file.write_text("{}\n", encoding="utf-8")
            wait_file = root / "external-wait.json"
            wait_file.write_text(json.dumps(_lease(scope_kind="wp08_batch", scope_id="batch-b")), encoding="utf-8")
            env = {
                "WP08_CERTIFICATION_STATE_FILE": str(state_file),
                "WP08_CURRENT_BATCH_ID": "batch-b",
                "WP08_CURRENT_BATCH_TITLE": "Batch B",
                "WP08_CURRENT_BATCH_TIMEOUT": "1",
                "WP08_BATCH_INDEX": "1",
                "WP08_BATCH_TOTAL": "1",
                "WP08_HEARTBEAT_SECONDS": "0.02",
                "WP08_STALL_WARNING_SECONDS": "0.03",
                "WP08_STALL_TIMEOUT_SECONDS": "0.08",
                "WP08_EXTERNAL_WAIT_FILE": str(wait_file),
            }
            code, _, _, timed_out = wp08._run_process(
                [sys.executable, "-c", "import time; time.sleep(0.4)"],
                cwd=root,
                env=env,
                timeout_seconds=0.18,
            )
            self.assertEqual(code, 124)
            self.assertTrue(timed_out)
            liveness = json.loads((root / "wp08-liveness.json").read_text(encoding="utf-8"))
            self.assertEqual(liveness["liveness_status"], "TIMEOUT")
            self.assertEqual(liveness["termination_reason"], "batch_timeout")
            self.assertEqual(liveness["external_wait_evidence"]["external_ref"]["id"], "req-123")


if __name__ == "__main__":
    unittest.main()
