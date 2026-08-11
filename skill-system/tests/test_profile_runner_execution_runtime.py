from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "skill-system" / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import execution_runtime  # noqa: E402
import profile_runner  # noqa: E402


def _write_profile(root: Path, name: str, commands: list[list[str]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": name,
                "description": "test profile",
                "includes": [],
                "commands": commands,
            }
        ),
        encoding="utf-8",
    )


class ExecutionRuntimeCompatibilityTests(unittest.TestCase):
    def test_execution_runtime_preserves_captured_output_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = execution_runtime.run_streaming_command(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('runtime-out'); print('runtime-err', file=sys.stderr)",
                ],
                cwd=Path(raw),
                heartbeat_seconds=0.02,
                stall_warning_seconds=0.1,
            )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "runtime-out\n")
        self.assertEqual(result["stderr"], "runtime-err\n")
        self.assertIs(result["timed_out"], False)
        self.assertEqual(result["liveness_status"], "COMPLETED")

    def test_execution_runtime_only_terminates_on_explicit_timeout(self) -> None:
        heartbeats: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as raw:
            result = execution_runtime.run_streaming_command(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                cwd=Path(raw),
                heartbeat_seconds=0.02,
                stall_warning_seconds=0.04,
                timeout_seconds=0.12,
                on_heartbeat=heartbeats.append,
            )

        self.assertEqual(result["exit_code"], 124)
        self.assertIs(result["timed_out"], True)
        self.assertEqual(result["termination_reason"], "command_timeout")
        self.assertEqual(result["liveness_status"], "TIMEOUT")
        self.assertTrue(
            any(row.get("liveness_status") == "SUSPECTED_STALL" for row in heartbeats)
        )
        self.assertTrue(any(row.get("liveness_status") == "TIMEOUT" for row in heartbeats))

    def test_profile_runner_preserves_result_schema_and_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            profiles = tmp_path / "profiles"
            marker = tmp_path / "marker.txt"
            _write_profile(
                profiles,
                "compat",
                [
                    [
                        "{python}",
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).open('a').write('A')",
                    ],
                    [
                        "{python}",
                        "-c",
                        f"from pathlib import Path; import sys; Path({str(marker)!r}).open('a').write('B'); sys.exit(3)",
                    ],
                    [
                        "{python}",
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).open('a').write('C')",
                    ],
                ],
            )
            with (
                mock.patch.object(profile_runner, "PROFILES", profiles),
                mock.patch.object(
                    profile_runner,
                    "workspace_source_identity",
                    return_value="git:" + "1" * 40 + ":" + "2" * 64,
                ),
            ):
                result = profile_runner.run(
                    "compat",
                    state_file=tmp_path / "state.json",
                    heartbeat_seconds=0.02,
                    stall_warning_seconds=0.2,
                )

            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(set(result), {"status", "requested_profile", "results"})
            self.assertEqual(marker.read_text(encoding="utf-8"), "AB")
            self.assertEqual(len(result["results"]), 2)
            self.assertEqual(result["results"][1]["exit_code"], 3)
            self.assertEqual(result["results"][1]["status"], "FAIL")
            for row in result["results"]:
                self.assertEqual(
                    set(row),
                    {
                        "profile",
                        "command_index",
                        "argv",
                        "exit_code",
                        "stdout",
                        "stderr",
                        "status",
                    },
                )

    def test_profile_runner_resume_skips_only_same_source_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            profiles = tmp_path / "profiles"
            marker = tmp_path / "resume-marker.txt"
            _write_profile(
                profiles,
                "resume",
                [
                    [
                        "{python}",
                        "-c",
                        f"from pathlib import Path; Path({str(marker)!r}).open('a').write('A')",
                    ],
                    [
                        "{python}",
                        "-c",
                        f"from pathlib import Path; import sys; Path({str(marker)!r}).open('a').write('B'); sys.exit(5)",
                    ],
                ],
            )
            identity = "git:" + "3" * 40 + ":" + "4" * 64
            state_file = tmp_path / "resume-state.json"
            with (
                mock.patch.object(profile_runner, "PROFILES", profiles),
                mock.patch.object(profile_runner, "workspace_source_identity", return_value=identity),
            ):
                first = profile_runner.run(
                    "resume",
                    state_file=state_file,
                    heartbeat_seconds=0.02,
                    stall_warning_seconds=0.2,
                )
                self.assertEqual(first["status"], "FAIL")
                self.assertEqual(marker.read_text(encoding="utf-8"), "AB")

                second = profile_runner.run(
                    "resume",
                    state_file=state_file,
                    resume=True,
                    heartbeat_seconds=0.02,
                    stall_warning_seconds=0.2,
                )

            self.assertEqual(second["status"], "FAIL")
            self.assertEqual(marker.read_text(encoding="utf-8"), "ABB")
            self.assertEqual(len(second["results"]), 2)
            self.assertEqual(second["results"][0]["status"], "PASS")
            self.assertEqual(second["results"][1]["status"], "FAIL")

    def test_profile_runner_resume_rejects_source_identity_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            profiles = tmp_path / "profiles"
            _write_profile(profiles, "source-bound", [["{python}", "-c", "print('ok')"]])
            state_file = tmp_path / "source-state.json"
            first_identity = "git:" + "5" * 40 + ":" + "6" * 64
            second_identity = "git:" + "7" * 40 + ":" + "8" * 64
            with (
                mock.patch.object(profile_runner, "PROFILES", profiles),
                mock.patch.object(
                    profile_runner, "workspace_source_identity", return_value=first_identity
                ),
            ):
                self.assertEqual(
                    profile_runner.run(
                        "source-bound",
                        state_file=state_file,
                        heartbeat_seconds=0.02,
                        stall_warning_seconds=0.2,
                    )["status"],
                    "PASS",
                )

            with (
                mock.patch.object(profile_runner, "PROFILES", profiles),
                mock.patch.object(
                    profile_runner, "workspace_source_identity", return_value=second_identity
                ),
            ):
                with self.assertRaisesRegex(ValueError, "another workspace source identity"):
                    profile_runner.run(
                        "source-bound",
                        state_file=state_file,
                        resume=True,
                        heartbeat_seconds=0.02,
                        stall_warning_seconds=0.2,
                    )

    def test_profile_runner_resume_rejects_profile_plan_change(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_path = Path(raw)
            profiles = tmp_path / "profiles"
            state_file = tmp_path / "plan-state.json"
            identity = "git:" + "9" * 40 + ":" + "a" * 64
            _write_profile(profiles, "plan-bound", [["{python}", "-c", "print('one')"]])
            with (
                mock.patch.object(profile_runner, "PROFILES", profiles),
                mock.patch.object(profile_runner, "workspace_source_identity", return_value=identity),
            ):
                self.assertEqual(
                    profile_runner.run(
                        "plan-bound",
                        state_file=state_file,
                        heartbeat_seconds=0.02,
                        stall_warning_seconds=0.2,
                    )["status"],
                    "PASS",
                )

                _write_profile(
                    profiles, "plan-bound", [["{python}", "-c", "print('two')"]]
                )
                with self.assertRaisesRegex(ValueError, "another profile command plan"):
                    profile_runner.run(
                        "plan-bound",
                        state_file=state_file,
                        resume=True,
                        heartbeat_seconds=0.02,
                        stall_warning_seconds=0.2,
                    )


if __name__ == "__main__":
    unittest.main()
