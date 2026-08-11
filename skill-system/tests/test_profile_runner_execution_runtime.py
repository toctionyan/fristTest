from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

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


def test_execution_runtime_preserves_captured_output_and_exit_code(tmp_path: Path) -> None:
    result = execution_runtime.run_streaming_command(
        [
            sys.executable,
            "-c",
            "import sys; print('runtime-out'); print('runtime-err', file=sys.stderr)",
        ],
        cwd=tmp_path,
        heartbeat_seconds=0.02,
        stall_warning_seconds=0.1,
    )

    assert result["exit_code"] == 0
    assert result["stdout"] == "runtime-out\n"
    assert result["stderr"] == "runtime-err\n"
    assert result["timed_out"] is False
    assert result["liveness_status"] == "COMPLETED"


def test_execution_runtime_only_terminates_on_explicit_timeout(tmp_path: Path) -> None:
    heartbeats: list[dict[str, object]] = []
    result = execution_runtime.run_streaming_command(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        cwd=tmp_path,
        heartbeat_seconds=0.02,
        stall_warning_seconds=0.04,
        timeout_seconds=0.12,
        on_heartbeat=heartbeats.append,
    )

    assert result["exit_code"] == 124
    assert result["timed_out"] is True
    assert result["termination_reason"] == "command_timeout"
    assert result["liveness_status"] == "TIMEOUT"
    assert any(row.get("liveness_status") == "SUSPECTED_STALL" for row in heartbeats)
    assert any(row.get("liveness_status") == "TIMEOUT" for row in heartbeats)


def test_profile_runner_preserves_result_schema_and_fail_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(profile_runner, "PROFILES", profiles)
    monkeypatch.setattr(
        profile_runner,
        "workspace_source_identity",
        lambda: "git:" + "1" * 40 + ":" + "2" * 64,
    )

    result = profile_runner.run(
        "compat",
        state_file=tmp_path / "state.json",
        heartbeat_seconds=0.02,
        stall_warning_seconds=0.2,
    )

    assert result["status"] == "FAIL"
    assert set(result) == {"status", "requested_profile", "results"}
    assert marker.read_text(encoding="utf-8") == "AB"
    assert len(result["results"]) == 2
    assert result["results"][1]["exit_code"] == 3
    assert result["results"][1]["status"] == "FAIL"
    for row in result["results"]:
        assert set(row) == {
            "profile",
            "command_index",
            "argv",
            "exit_code",
            "stdout",
            "stderr",
            "status",
        }


def test_profile_runner_resume_skips_only_same_source_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(profile_runner, "PROFILES", profiles)
    identity = "git:" + "3" * 40 + ":" + "4" * 64
    monkeypatch.setattr(profile_runner, "workspace_source_identity", lambda: identity)
    state_file = tmp_path / "resume-state.json"

    first = profile_runner.run(
        "resume",
        state_file=state_file,
        heartbeat_seconds=0.02,
        stall_warning_seconds=0.2,
    )
    assert first["status"] == "FAIL"
    assert marker.read_text(encoding="utf-8") == "AB"

    second = profile_runner.run(
        "resume",
        state_file=state_file,
        resume=True,
        heartbeat_seconds=0.02,
        stall_warning_seconds=0.2,
    )
    assert second["status"] == "FAIL"
    assert marker.read_text(encoding="utf-8") == "ABB"
    assert len(second["results"]) == 2
    assert second["results"][0]["status"] == "PASS"
    assert second["results"][1]["status"] == "FAIL"


def test_profile_runner_resume_rejects_source_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "source-bound", [["{python}", "-c", "print('ok')"]])
    monkeypatch.setattr(profile_runner, "PROFILES", profiles)
    state_file = tmp_path / "source-state.json"
    first_identity = "git:" + "5" * 40 + ":" + "6" * 64
    second_identity = "git:" + "7" * 40 + ":" + "8" * 64
    monkeypatch.setattr(profile_runner, "workspace_source_identity", lambda: first_identity)

    assert profile_runner.run(
        "source-bound",
        state_file=state_file,
        heartbeat_seconds=0.02,
        stall_warning_seconds=0.2,
    )["status"] == "PASS"

    monkeypatch.setattr(profile_runner, "workspace_source_identity", lambda: second_identity)
    with pytest.raises(ValueError, match="another workspace source identity"):
        profile_runner.run(
            "source-bound",
            state_file=state_file,
            resume=True,
            heartbeat_seconds=0.02,
            stall_warning_seconds=0.2,
        )


def test_profile_runner_resume_rejects_profile_plan_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = tmp_path / "profiles"
    state_file = tmp_path / "plan-state.json"
    identity = "git:" + "9" * 40 + ":" + "a" * 64
    monkeypatch.setattr(profile_runner, "PROFILES", profiles)
    monkeypatch.setattr(profile_runner, "workspace_source_identity", lambda: identity)
    _write_profile(profiles, "plan-bound", [["{python}", "-c", "print('one')"]])

    assert profile_runner.run(
        "plan-bound",
        state_file=state_file,
        heartbeat_seconds=0.02,
        stall_warning_seconds=0.2,
    )["status"] == "PASS"

    _write_profile(profiles, "plan-bound", [["{python}", "-c", "print('two')"]])
    with pytest.raises(ValueError, match="another profile command plan"):
        profile_runner.run(
            "plan-bound",
            state_file=state_file,
            resume=True,
            heartbeat_seconds=0.02,
            stall_warning_seconds=0.2,
        )
