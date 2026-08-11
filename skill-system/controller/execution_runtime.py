#!/usr/bin/env python3
"""Small, reusable process runtime for observable long-running validation commands.

This module deliberately owns *execution mechanics only*: streaming pipes, liveness,
timeout termination and atomic state helpers.  It does not decide whether a gate,
profile, release or product claim is correct.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, TextIO


LIVENESS_RUNNING = "RUNNING_ACTIVE"
LIVENESS_SUSPECTED_STALL = "SUSPECTED_STALL"
LIVENESS_COMPLETED = "COMPLETED"
LIVENESS_FAILED = "FAILED"
LIVENESS_TIMEOUT = "TIMEOUT"


class ExecutionRuntimeError(RuntimeError):
    """Raised when a command cannot be started safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace a JSON state file so interrupted writers do not corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _positive_seconds(value: float, *, name: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if seconds <= 0:
        raise ValueError(f"{name} must be > 0")
    return seconds


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _reader(
    stream: TextIO,
    *,
    chunks: list[str],
    activity: dict[str, Any],
    lock: threading.Lock,
    stream_name: str,
    mirror: TextIO | None,
) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            chunks.append(line)
            if mirror is not None:
                mirror.write(line)
                mirror.flush()
            with lock:
                activity["last_progress_monotonic"] = time.monotonic()
                activity["last_progress_at"] = utc_now()
                activity["last_activity"] = f"{stream_name}_output"
                activity["progress_event_count"] = int(activity.get("progress_event_count") or 0) + 1
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _payload(
    *,
    process: subprocess.Popen[str],
    started_at: str,
    started_monotonic: float,
    activity: Mapping[str, Any],
    liveness_status: str,
    termination_reason: str | None = None,
) -> dict[str, Any]:
    now = time.monotonic()
    last_progress = float(activity.get("last_progress_monotonic") or started_monotonic)
    return {
        "heartbeat_at": utc_now(),
        "started_at": started_at,
        "last_progress_at": str(activity.get("last_progress_at") or started_at),
        "last_activity": str(activity.get("last_activity") or "process_started"),
        "progress_event_count": int(activity.get("progress_event_count") or 0),
        "liveness_status": liveness_status,
        "child_process_alive": process.poll() is None,
        "child_pid": process.pid,
        "elapsed_seconds": round(max(0.0, now - started_monotonic), 3),
        "idle_seconds": round(max(0.0, now - last_progress), 3),
        "termination_reason": termination_reason,
    }


def run_streaming_command(
    argv: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    heartbeat_seconds: float = 30.0,
    stall_warning_seconds: float = 240.0,
    timeout_seconds: float | None = None,
    on_heartbeat: Callable[[dict[str, Any]], None] | None = None,
    stream_output: bool = False,
) -> dict[str, Any]:
    """Run one command while preserving the old captured-output result contract.

    Quiet commands are never killed merely for being quiet.  ``stall_warning_seconds``
    only changes liveness classification.  A process is terminated only when an
    explicit ``timeout_seconds`` is supplied and reached.
    """
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv must contain non-empty strings")
    heartbeat_seconds = _positive_seconds(heartbeat_seconds, name="heartbeat_seconds")
    stall_warning_seconds = max(
        _positive_seconds(stall_warning_seconds, name="stall_warning_seconds"),
        heartbeat_seconds,
    )
    if timeout_seconds is not None:
        timeout_seconds = _positive_seconds(timeout_seconds, name="timeout_seconds")

    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
            bufsize=1,
        )
    except OSError as exc:
        raise ExecutionRuntimeError(f"unable to start command {argv!r}: {exc}") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    started_at = utc_now()
    started_monotonic = time.monotonic()
    activity: dict[str, Any] = {
        "last_progress_monotonic": started_monotonic,
        "last_progress_at": started_at,
        "last_activity": "process_started",
        "progress_event_count": 0,
    }
    lock = threading.Lock()
    stdout_thread = threading.Thread(
        target=_reader,
        kwargs={
            "stream": process.stdout,
            "chunks": stdout_chunks,
            "activity": activity,
            "lock": lock,
            "stream_name": "stdout",
            "mirror": sys.stderr if stream_output else None,
        },
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        kwargs={
            "stream": process.stderr,
            "chunks": stderr_chunks,
            "activity": activity,
            "lock": lock,
            "stream_name": "stderr",
            "mirror": sys.stderr if stream_output else None,
        },
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    next_heartbeat = started_monotonic
    termination_reason: str | None = None
    timed_out = False
    while process.poll() is None:
        now = time.monotonic()
        with lock:
            snapshot = dict(activity)
        elapsed = now - started_monotonic
        last_progress = float(snapshot.get("last_progress_monotonic") or started_monotonic)
        idle = now - last_progress
        if timeout_seconds is not None and elapsed >= timeout_seconds:
            termination_reason = "command_timeout"
            timed_out = True
            timeout_payload = _payload(
                process=process,
                started_at=started_at,
                started_monotonic=started_monotonic,
                activity=snapshot,
                liveness_status=LIVENESS_TIMEOUT,
                termination_reason=termination_reason,
            )
            if on_heartbeat is not None:
                on_heartbeat(timeout_payload)
            _terminate_process(process)
            break
        if now >= next_heartbeat:
            status = LIVENESS_SUSPECTED_STALL if idle >= stall_warning_seconds else LIVENESS_RUNNING
            heartbeat = _payload(
                process=process,
                started_at=started_at,
                started_monotonic=started_monotonic,
                activity=snapshot,
                liveness_status=status,
            )
            if on_heartbeat is not None:
                on_heartbeat(heartbeat)
            next_heartbeat = now + heartbeat_seconds
        time.sleep(min(0.2, max(0.02, heartbeat_seconds / 5.0)))

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    returncode = 124 if timed_out else int(process.returncode or 0)
    ended_at = utc_now()
    duration_seconds = round(max(0.0, time.monotonic() - started_monotonic), 3)
    with lock:
        final_activity = dict(activity)
    final_liveness = (
        LIVENESS_TIMEOUT
        if timed_out
        else LIVENESS_COMPLETED
        if returncode == 0
        else LIVENESS_FAILED
    )
    final_payload = _payload(
        process=process,
        started_at=started_at,
        started_monotonic=started_monotonic,
        activity=final_activity,
        liveness_status=final_liveness,
        termination_reason=termination_reason,
    )
    final_payload["child_process_alive"] = False
    final_payload["returncode"] = returncode
    if on_heartbeat is not None:
        on_heartbeat(final_payload)
    return {
        "exit_code": returncode,
        "stdout": "".join(stdout_chunks),
        "stderr": "".join(stderr_chunks),
        "timed_out": timed_out,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "liveness_status": final_liveness,
        "last_progress_at": str(final_activity.get("last_progress_at") or started_at),
        "progress_event_count": int(final_activity.get("progress_event_count") or 0),
        "termination_reason": termination_reason,
    }


__all__ = [
    "ExecutionRuntimeError",
    "LIVENESS_COMPLETED",
    "LIVENESS_FAILED",
    "LIVENESS_RUNNING",
    "LIVENESS_SUSPECTED_STALL",
    "LIVENESS_TIMEOUT",
    "atomic_json",
    "run_streaming_command",
    "utc_now",
]
