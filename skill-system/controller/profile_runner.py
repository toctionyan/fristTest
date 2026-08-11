#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "skill-system" / "profiles"
CONTROLLER_DIR = Path(__file__).resolve().parent
if str(CONTROLLER_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_DIR))

from execution_runtime import (  # noqa: E402
    ExecutionRuntimeError,
    atomic_json,
    run_streaming_command,
    utc_now,
)

PROFILE_RUN_CONTRACT = "skill-profile-run@2"
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


def load_profile(name: str) -> dict[str, Any]:
    path = PROFILES / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"unknown Skill profile: {name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("id") != name or payload.get("schema_version") != 1:
        raise ValueError(f"invalid Skill profile: {name}")
    return payload


def expand_profiles(name: str, seen: set[str] | None = None) -> list[dict[str, Any]]:
    seen = set(seen or ())
    if name in seen:
        raise ValueError(f"cyclic Skill profile include: {name}")
    seen.add(name)
    profile = load_profile(name)
    rows: list[dict[str, Any]] = []
    for child in profile.get("includes") or []:
        rows.extend(expand_profiles(str(child), seen.copy()))
    rows.append(profile)
    unique: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in rows:
        if row["id"] not in ids:
            unique.append(row)
            ids.add(row["id"])
    return unique


def command_argv(raw: list[str]) -> list[str]:
    return [sys.executable if value == "{python}" else value for value in raw]


def _command_plan(name: str) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for profile in expand_profiles(name):
        for index, raw in enumerate(profile.get("commands") or [], start=1):
            argv = command_argv([str(value) for value in raw])
            plan.append(
                {
                    "command_id": f"{profile['id']}:{index}",
                    "profile": str(profile["id"]),
                    "command_index": index,
                    "argv": argv,
                }
            )
    return plan


def _plan_fingerprint(plan: list[dict[str, Any]]) -> str:
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _git_bytes(*args: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def workspace_source_identity() -> str | None:
    """Fingerprint the checked-out source, including local tracked/untracked changes.

    Resume is intentionally refused when this identity cannot be established.  This
    prevents a PASS from another source snapshot being reused merely because the
    profile command list happens to be unchanged.
    """
    head_raw = _git_bytes("rev-parse", "HEAD")
    if head_raw is None:
        return None
    head = head_raw.decode("ascii", errors="ignore").strip().casefold()
    if not _SHA40_RE.fullmatch(head):
        return None
    diff = _git_bytes("diff", "--binary", "HEAD", "--", ".")
    untracked = _git_bytes("ls-files", "--others", "--exclude-standard", "-z")
    if diff is None or untracked is None:
        return None
    digest = hashlib.sha256()
    digest.update(head.encode("ascii"))
    digest.update(b"\0tracked-diff\0")
    digest.update(diff)
    digest.update(b"\0untracked\0")
    for raw_path in sorted(path for path in untracked.split(b"\0") if path):
        digest.update(raw_path)
        digest.update(b"\0")
        candidate = ROOT / os.fsdecode(raw_path)
        try:
            if candidate.is_file():
                digest.update(hashlib.sha256(candidate.read_bytes()).digest())
        except OSError:
            return None
    return f"git:{head}:{digest.hexdigest()}"


def _default_state_file(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "profile"
    return ROOT / ".quality" / "profile-runs" / f"{safe}.json"


def _load_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("profile run state must be a JSON object")
    return payload


def _new_state(
    *,
    name: str,
    plan_fingerprint: str,
    source_identity: str | None,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "contract": PROFILE_RUN_CONTRACT,
        "run_token": secrets.token_hex(16),
        "requested_profile": name,
        "plan_fingerprint_sha256": plan_fingerprint,
        "source_identity": source_identity,
        "status": "RUNNING",
        "liveness_status": "RUNNING_ACTIVE",
        "started_at": now,
        "updated_at": now,
        "heartbeat_at": now,
        "current_command": None,
        "completed_commands": {},
    }


def _validated_resume_state(
    path: Path,
    *,
    name: str,
    plan_fingerprint: str,
    source_identity: str | None,
) -> dict[str, Any]:
    if source_identity is None:
        raise ValueError("--resume requires a verifiable workspace source identity")
    if not path.is_file():
        raise ValueError(f"--resume state file does not exist: {path}")
    state = _load_state(path)
    if state.get("contract") != PROFILE_RUN_CONTRACT:
        raise ValueError("resume state contract is incompatible")
    if state.get("requested_profile") != name:
        raise ValueError("resume state belongs to another Skill profile")
    if state.get("plan_fingerprint_sha256") != plan_fingerprint:
        raise ValueError("resume state belongs to another profile command plan")
    if state.get("source_identity") != source_identity:
        raise ValueError("resume state belongs to another workspace source identity")
    completed = state.get("completed_commands")
    if not isinstance(completed, dict):
        raise ValueError("resume state completed_commands is invalid")
    return state


def run(
    name: str,
    *,
    state_file: Path | str | None = None,
    resume: bool = False,
    heartbeat_seconds: float = 30.0,
    stall_warning_seconds: float = 240.0,
    command_timeout_seconds: float | None = None,
    stream_output: bool = False,
) -> dict[str, Any]:
    """Run one expanded Skill profile without changing its historical result schema."""
    plan = _command_plan(name)
    plan_fingerprint = _plan_fingerprint(plan)
    source_identity = workspace_source_identity()
    state_path = Path(state_file).expanduser().resolve() if state_file else _default_state_file(name)
    if resume:
        state = _validated_resume_state(
            state_path,
            name=name,
            plan_fingerprint=plan_fingerprint,
            source_identity=source_identity,
        )
        state["status"] = "RUNNING"
        state["liveness_status"] = "RUNNING_ACTIVE"
        state["updated_at"] = utc_now()
        state["current_command"] = None
        print(
            f"[skill-profile] resuming {name} run {str(state.get('run_token') or '')[:8]}",
            file=sys.stderr,
            flush=True,
        )
    else:
        state = _new_state(
            name=name,
            plan_fingerprint=plan_fingerprint,
            source_identity=source_identity,
        )
    atomic_json(state_path, state)

    completed_commands = state["completed_commands"]
    results: list[dict[str, Any]] = []
    for item in plan:
        command_id = str(item["command_id"])
        prior = completed_commands.get(command_id)
        if resume and isinstance(prior, dict) and prior.get("status") == "PASS":
            prior_result = prior.get("result")
            if not isinstance(prior_result, dict):
                raise ValueError(f"resume state result is invalid for {command_id}")
            results.append(dict(prior_result))
            print(
                f"[skill-profile] resume verified {command_id}; skipping same-source PASS",
                file=sys.stderr,
                flush=True,
            )
            continue

        state["current_command"] = {
            "command_id": command_id,
            "profile": item["profile"],
            "command_index": item["command_index"],
            "argv": item["argv"],
        }
        state["updated_at"] = utc_now()
        atomic_json(state_path, state)

        def on_heartbeat(payload: dict[str, Any]) -> None:
            state["heartbeat_at"] = payload.get("heartbeat_at")
            state["updated_at"] = payload.get("heartbeat_at") or utc_now()
            state["liveness_status"] = payload.get("liveness_status")
            state["current_command"] = {
                "command_id": command_id,
                "profile": item["profile"],
                "command_index": item["command_index"],
                "argv": item["argv"],
                "child_pid": payload.get("child_pid"),
                "child_process_alive": payload.get("child_process_alive"),
                "elapsed_seconds": payload.get("elapsed_seconds"),
                "idle_seconds": payload.get("idle_seconds"),
                "last_progress_at": payload.get("last_progress_at"),
                "last_activity": payload.get("last_activity"),
                "progress_event_count": payload.get("progress_event_count"),
                "termination_reason": payload.get("termination_reason"),
            }
            atomic_json(state_path, state)
            print(
                "[skill-profile heartbeat] "
                + json.dumps(
                    {
                        "profile": name,
                        "command_id": command_id,
                        "liveness_status": payload.get("liveness_status"),
                        "elapsed_seconds": payload.get("elapsed_seconds"),
                        "idle_seconds": payload.get("idle_seconds"),
                        "last_activity": payload.get("last_activity"),
                        "progress_event_count": payload.get("progress_event_count"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )

        runtime_result = run_streaming_command(
            list(item["argv"]),
            cwd=ROOT,
            heartbeat_seconds=heartbeat_seconds,
            stall_warning_seconds=stall_warning_seconds,
            timeout_seconds=command_timeout_seconds,
            on_heartbeat=on_heartbeat,
            stream_output=stream_output,
        )
        row = {
            "profile": item["profile"],
            "command_index": item["command_index"],
            "argv": item["argv"],
            "exit_code": runtime_result["exit_code"],
            "stdout": runtime_result["stdout"],
            "stderr": runtime_result["stderr"],
            "status": "PASS" if runtime_result["exit_code"] == 0 else "FAIL",
        }
        results.append(row)
        completed_commands[command_id] = {
            "status": row["status"],
            "completed_at": runtime_result["ended_at"],
            "runtime": {
                key: runtime_result.get(key)
                for key in (
                    "timed_out",
                    "started_at",
                    "ended_at",
                    "duration_seconds",
                    "liveness_status",
                    "last_progress_at",
                    "progress_event_count",
                    "termination_reason",
                )
            },
            "result": row,
        }
        state["current_command"] = None
        state["updated_at"] = utc_now()
        state["heartbeat_at"] = state["updated_at"]
        state["liveness_status"] = "COMPLETED" if row["status"] == "PASS" else "FAILED"
        atomic_json(state_path, state)
        if row["status"] == "FAIL":
            state["status"] = "FAIL"
            state["completed_at"] = utc_now()
            state["updated_at"] = state["completed_at"]
            state["liveness_status"] = "FAILED"
            atomic_json(state_path, state)
            return {"status": "FAIL", "requested_profile": name, "results": results}

    state["status"] = "PASS"
    state["completed_at"] = utc_now()
    state["updated_at"] = state["completed_at"]
    state["heartbeat_at"] = state["completed_at"]
    state["liveness_status"] = "COMPLETED"
    state["current_command"] = None
    atomic_json(state_path, state)
    return {"status": "PASS", "requested_profile": name, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("--state-file")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument("--stall-warning-seconds", type=float, default=240.0)
    parser.add_argument("--command-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--stream-output", action="store_true")
    args = parser.parse_args()
    try:
        result = run(
            args.profile,
            state_file=args.state_file,
            resume=bool(args.resume),
            heartbeat_seconds=args.heartbeat_seconds,
            stall_warning_seconds=args.stall_warning_seconds,
            command_timeout_seconds=(
                args.command_timeout_seconds if args.command_timeout_seconds > 0 else None
            ),
            stream_output=bool(args.stream_output),
        )
    except (OSError, ValueError, json.JSONDecodeError, ExecutionRuntimeError) as exc:
        result = {
            "status": "FAIL",
            "requested_profile": args.profile,
            "error": str(exc),
            "results": [],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
