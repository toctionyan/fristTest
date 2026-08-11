#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def remove_function(path: Path, name: str) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next((row for row in tree.body if isinstance(row, ast.FunctionDef) and row.name == name), None)
    if node is None or node.end_lineno is None:
        return
    lines = text.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = []
    path.write_text("".join(lines), encoding="utf-8")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"anchor not found in {path}: {old[:100]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"anchor count != 1 in {path}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


runtime = ROOT / "skill-system" / "controller" / "execution_runtime.py"
text = runtime.read_text(encoding="utf-8")
if "MAX_EXTERNAL_WAIT_LEASE_SECONDS" not in text:
    replace_once(
        runtime,
        'EXTERNAL_WAIT_CONTRACT = "execution-external-wait@1"\n',
        'EXTERNAL_WAIT_CONTRACT = "execution-external-wait@1"\nMAX_EXTERNAL_WAIT_LEASE_SECONDS = 300.0\n',
    )
replace_once(
    runtime,
    '''    now = datetime.now(timezone.utc)\n    if expires_at <= now or heartbeat_at > now:\n        return None\n    return dict(payload)\n''',
    '''    now = datetime.now(timezone.utc)\n    lease_seconds = (expires_at - heartbeat_at).total_seconds()\n    age_seconds = (now - heartbeat_at).total_seconds()\n    if (\n        expires_at <= now\n        or heartbeat_at > now\n        or lease_seconds <= 0\n        or lease_seconds > MAX_EXTERNAL_WAIT_LEASE_SECONDS\n        or age_seconds > MAX_EXTERNAL_WAIT_LEASE_SECONDS\n    ):\n        return None\n    return dict(payload)\n''',
)
replace_once(
    runtime,
    '''            stderr=subprocess.PIPE,\n            start_new_session=(os.name == "posix"),\n            bufsize=1,\n''',
    '''            stderr=subprocess.PIPE,\n            start_new_session=(os.name == "posix"),\n            bufsize=1,\n            encoding="utf-8",\n            errors="replace",\n''',
)
text = runtime.read_text(encoding="utf-8")
text = text.replace('    latest_external_wait: dict[str, Any] | None = None\n', '')
text = text.replace('        if external_wait is not None:\n            latest_external_wait = external_wait\n', '')
text = text.replace(
    '''    final_payload["child_process_alive"] = False\n    final_payload["returncode"] = returncode\n    if latest_external_wait is not None:\n        final_payload["external_wait_evidence"] = latest_external_wait\n''',
    '''    final_payload["child_process_alive"] = False\n    final_payload["returncode"] = returncode\n    final_external_wait = current_external_wait()\n    if final_external_wait is not None:\n        final_payload["external_wait_evidence"] = final_external_wait\n''',
)
text = text.replace('        "external_wait_evidence": latest_external_wait,\n', '        "external_wait_evidence": final_external_wait,\n')
runtime.write_text(text, encoding="utf-8")
if '"MAX_EXTERNAL_WAIT_LEASE_SECONDS"' not in runtime.read_text(encoding="utf-8"):
    replace_once(runtime, '__all__ = [\n', '__all__ = [\n    "MAX_EXTERNAL_WAIT_LEASE_SECONDS",\n')


environment = ROOT / "scripts" / "quality_control" / "environment.py"
remove_function(environment, "_terminate_process_group")
text = environment.read_text(encoding="utf-8")
for unused in ("import signal\n", "import subprocess\n", "import tempfile\n", "import time\n"):
    text = text.replace(unused, "")
environment.write_text(text, encoding="utf-8")


wp08 = ROOT / "scripts" / "run_wp08_certification.py"
text = wp08.read_text(encoding="utf-8")
for unused in ("import signal\n", "import subprocess\n", "import threading\n"):
    text = text.replace(unused, "")
text = text.replace("from typing import Any, Iterable, Mapping, TextIO\n", "from typing import Any, Iterable, Mapping\n")
text = text.replace(
    '''    except ExecutionRuntimeError as exc:\n        payload = {\n            "status": BLOCKED,\n            "reason": "batch_executable_unavailable",\n            "error_type": exc.__class__.__name__,\n            "error": str(exc),\n        }\n        return 78, json.dumps(payload, ensure_ascii=False) + "\\n", str(exc), False\n''',
    '''    except ExecutionRuntimeError as exc:\n        cause = exc.__cause__ if isinstance(exc.__cause__, OSError) else exc\n        payload = {\n            "status": BLOCKED,\n            "reason": "batch_executable_unavailable",\n            "error_type": cause.__class__.__name__,\n            "error": str(cause),\n        }\n        return 78, json.dumps(payload, ensure_ascii=False) + "\\n", str(cause), False\n''',
)
wp08.write_text(text, encoding="utf-8")


test_path = ROOT / "skill-system" / "tests" / "test_execution_runtime_m2_integration.py"
test_path.write_text(textwrap.dedent(r'''
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
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
''').lstrip(), encoding="utf-8")

for path in (runtime, environment, wp08, test_path):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

print("M2 finalization patch prepared")
