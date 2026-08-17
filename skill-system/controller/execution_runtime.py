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
LIVENESS_WAITING_EXTERNAL = "RUNNING_WAITING_EXTERNAL"
LIVENESS_STALL_TIMEOUT = "STALL_TIMEOUT"
EXTERNAL_WAIT_CONTRACT = "execution-external-wait@1"
MAX_EXTERNAL_WAIT_LEASE_SECONDS = 300.0


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


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def validate_external_wait_evidence(payload: Mapping[str, Any] | None, *, expected_scope: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Accept only a fresh, scoped machine lease as external-wait authority."""
    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("contract") or "") != EXTERNAL_WAIT_CONTRACT:
        return None
    if str(payload.get("status") or "") != "WAITING_EXTERNAL":
        return None
    external_ref = payload.get("external_ref")
    scope = payload.get("scope")
    if not isinstance(external_ref, Mapping) or not isinstance(scope, Mapping):
        return None
    if not str(external_ref.get("kind") or "").strip() or not str(external_ref.get("id") or "").strip():
        return None
    if not str(scope.get("kind") or "").strip() or not str(scope.get("id") or "").strip():
        return None
    if expected_scope is not None:
        for key, value in expected_scope.items():
            if str(scope.get(key) or "") != str(value):
                return None
    heartbeat_at = _parse_utc(payload.get("heartbeat_at"))
    expires_at = _parse_utc(payload.get("expires_at"))
    if heartbeat_at is None or expires_at is None:
        return None
    now = datetime.now(timezone.utc)
    lease_seconds = (expires_at - heartbeat_at).total_seconds()
    age_seconds = (now - heartbeat_at).total_seconds()
    if expires_at <= now or heartbeat_at > now or lease_seconds <= 0 or lease_seconds > MAX_EXTERNAL_WAIT_LEASE_SECONDS or age_seconds > MAX_EXTERNAL_WAIT_LEASE_SECONDS:
        return None
    return dict(payload)


def external_wait_file_probe(path: Path, *, expected_scope: Mapping[str, str] | None = None) -> Callable[[], dict[str, Any] | None]:
    resolved = path.expanduser().resolve()
    def probe() -> dict[str, Any] | None:
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return validate_external_wait_evidence(payload, expected_scope=expected_scope)
    return probe


def _positive_seconds(value: float, *, name: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if seconds <= 0:
        raise ValueError(f"{name} must be > 0")
    return seconds


def _process_group_exists(process: subprocess.Popen[str]) -> bool:
    if os.name != "posix":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _cleanup_process_group(process: subprocess.Popen[str], *, grace_seconds: float) -> None:
    """Terminate the dedicated process group even when the parent already exited."""
    if os.name != "posix":  # pragma: no cover - Windows fallback
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=max(0.1, grace_seconds))
        return
    if not _process_group_exists(process):
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        if not _process_group_exists(process):
            return
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate the command group without waiting for every descendant to vanish."""
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
    else:  # pragma: no cover - Windows fallback
        process.terminate()
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return
    else:  # pragma: no cover - Windows fallback
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _reader(stream: TextIO, *, chunks: list[str], activity: dict[str, Any], lock: threading.Lock, stream_name: str, mirror: TextIO | None) -> None:
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


def _payload(*, process: subprocess.Popen[str], started_at: str, started_monotonic: float, activity: Mapping[str, Any], liveness_status: str, termination_reason: str | None = None) -> dict[str, Any]:
    now = time.monotonic()
    last_progress = float(activity.get("last_progress_monotonic") or started_monotonic)
    return {"heartbeat_at": utc_now(), "started_at": started_at, "last_progress_at": str(activity.get("last_progress_at") or started_at), "last_activity": str(activity.get("last_activity") or "process_started"), "progress_event_count": int(activity.get("progress_event_count") or 0), "liveness_status": liveness_status, "child_process_alive": process.poll() is None, "child_pid": process.pid, "elapsed_seconds": round(max(0.0, now - started_monotonic), 3), "idle_seconds": round(max(0.0, now - last_progress), 3), "termination_reason": termination_reason}


def run_streaming_command(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None, heartbeat_seconds: float = 30.0, stall_warning_seconds: float = 240.0, stall_timeout_seconds: float | None = None, timeout_seconds: float | None = None, on_heartbeat: Callable[[dict[str, Any]], None] | None = None, external_wait_probe: Callable[[], Mapping[str, Any] | None] | None = None, stream_output: bool = False, stdout_mirror: TextIO | None = None, stderr_mirror: TextIO | None = None, cleanup_process_group_after_exit: bool = False, cleanup_grace_seconds: float = 0.5) -> dict[str, Any]:
    """Run one command with durable liveness while preserving captured output.

    Silence alone never proves an external wait. A no-progress stall timeout is
    suppressed only while ``external_wait_probe`` returns a fresh, scoped lease
    satisfying ``execution-external-wait@1``. The explicit overall timeout is
    never suppressed. A skipped scheduling window cannot collapse a warning
    directly into terminal stall evidence.
    """
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv must contain non-empty strings")
    heartbeat_seconds = _positive_seconds(heartbeat_seconds, name="heartbeat_seconds")
    stall_warning_seconds = max(_positive_seconds(stall_warning_seconds, name="stall_warning_seconds"), heartbeat_seconds)
    if stall_timeout_seconds is not None:
        stall_timeout_seconds = max(_positive_seconds(stall_timeout_seconds, name="stall_timeout_seconds"), stall_warning_seconds + heartbeat_seconds)
    if timeout_seconds is not None:
        timeout_seconds = _positive_seconds(timeout_seconds, name="timeout_seconds")
    cleanup_grace_seconds = max(0.0, float(cleanup_grace_seconds))
    if stream_output:
        stdout_mirror = stdout_mirror or sys.stderr
        stderr_mirror = stderr_mirror or sys.stderr
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=dict(env) if env is not None else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=(os.name == "posix"), bufsize=1, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExecutionRuntimeError(f"unable to start command {argv!r}: {exc}") from exc
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    started_at = utc_now()
    started_monotonic = time.monotonic()
    activity: dict[str, Any] = {"last_progress_monotonic": started_monotonic, "last_progress_at": started_at, "last_activity": "process_started", "progress_event_count": 0}
    lock = threading.Lock()
    stdout_thread = threading.Thread(target=_reader, kwargs={"stream": process.stdout, "chunks": stdout_chunks, "activity": activity, "lock": lock, "stream_name": "stdout", "mirror": stdout_mirror}, daemon=True)
    stderr_thread = threading.Thread(target=_reader, kwargs={"stream": process.stderr, "chunks": stderr_chunks, "activity": activity, "lock": lock, "stream_name": "stderr", "mirror": stderr_mirror}, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    def current_external_wait() -> dict[str, Any] | None:
        if external_wait_probe is None:
            return None
        try:
            candidate = external_wait_probe()
        except Exception:
            return None
        return validate_external_wait_evidence(candidate)
    next_heartbeat = started_monotonic
    termination_reason: str | None = None
    timed_out = False
    stall_timed_out = False
    warned_progress_monotonic: float | None = None
    while process.poll() is None:
        now = time.monotonic()
        with lock:
            snapshot = dict(activity)
        elapsed = now - started_monotonic
        last_progress = float(snapshot.get("last_progress_monotonic") or started_monotonic)
        idle = now - last_progress
        external_wait = current_external_wait()
        if stall_timeout_seconds is not None and idle >= stall_timeout_seconds and external_wait is None:
            if warned_progress_monotonic != last_progress:
                warning_payload = _payload(process=process, started_at=started_at, started_monotonic=started_monotonic, activity=snapshot, liveness_status=LIVENESS_SUSPECTED_STALL)
                if on_heartbeat is not None:
                    on_heartbeat(warning_payload)
                warned_progress_monotonic = last_progress
            termination_reason = "no_progress_stall"
            timed_out = True
            stall_timed_out = True
            stall_payload = _payload(process=process, started_at=started_at, started_monotonic=started_monotonic, activity=snapshot, liveness_status=LIVENESS_STALL_TIMEOUT, termination_reason=termination_reason)
            if on_heartbeat is not None:
                on_heartbeat(stall_payload)
            _terminate_process(process)
            break
        if timeout_seconds is not None and elapsed >= timeout_seconds:
            termination_reason = "command_timeout"
            timed_out = True
            timeout_payload = _payload(process=process, started_at=started_at, started_monotonic=started_monotonic, activity=snapshot, liveness_status=LIVENESS_TIMEOUT, termination_reason=termination_reason)
            if external_wait is not None:
                timeout_payload["external_wait_evidence"] = external_wait
            if on_heartbeat is not None:
                on_heartbeat(timeout_payload)
            _terminate_process(process)
            break
        if now >= next_heartbeat:
            status = LIVENESS_WAITING_EXTERNAL if external_wait is not None else LIVENESS_SUSPECTED_STALL if idle >= stall_warning_seconds else LIVENESS_RUNNING
            heartbeat = _payload(process=process, started_at=started_at, started_monotonic=started_monotonic, activity=snapshot, liveness_status=status)
            if external_wait is not None:
                heartbeat["external_wait_evidence"] = external_wait
            if on_heartbeat is not None:
                on_heartbeat(heartbeat)
            if status == LIVENESS_SUSPECTED_STALL:
                warned_progress_monotonic = last_progress
            next_heartbeat = now + heartbeat_seconds
        time.sleep(min(0.2, max(0.02, heartbeat_seconds / 5.0)))
    if cleanup_process_group_after_exit and not timed_out:
        _cleanup_process_group(process, grace_seconds=cleanup_grace_seconds)
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    returncode = 124 if timed_out else int(process.returncode or 0)
    ended_at = utc_now()
    duration_seconds = round(max(0.0, time.monotonic() - started_monotonic), 3)
    with lock:
        final_activity = dict(activity)
    final_liveness = LIVENESS_STALL_TIMEOUT if stall_timed_out else LIVENESS_TIMEOUT if timed_out else LIVENESS_COMPLETED if returncode == 0 else LIVENESS_FAILED
    final_payload = _payload(process=process, started_at=started_at, started_monotonic=started_monotonic, activity=final_activity, liveness_status=final_liveness, termination_reason=termination_reason)
    final_payload["child_process_alive"] = False
    final_payload["returncode"] = returncode
    final_external_wait = current_external_wait()
    if final_external_wait is not None:
        final_payload["external_wait_evidence"] = final_external_wait
    if on_heartbeat is not None:
        on_heartbeat(final_payload)
    return {"exit_code": returncode, "stdout": "".join(stdout_chunks), "stderr": "".join(stderr_chunks), "timed_out": timed_out, "stall_timed_out": stall_timed_out, "started_at": started_at, "ended_at": ended_at, "duration_seconds": duration_seconds, "liveness_status": final_liveness, "last_progress_at": str(final_activity.get("last_progress_at") or started_at), "progress_event_count": int(final_activity.get("progress_event_count") or 0), "termination_reason": termination_reason, "external_wait_evidence": final_external_wait}


__all__ = ["MAX_EXTERNAL_WAIT_LEASE_SECONDS", "validate_external_wait_evidence", "external_wait_file_probe", "LIVENESS_WAITING_EXTERNAL", "LIVENESS_STALL_TIMEOUT", "EXTERNAL_WAIT_CONTRACT", "ExecutionRuntimeError", "LIVENESS_COMPLETED", "LIVENESS_FAILED", "LIVENESS_RUNNING", "LIVENESS_SUSPECTED_STALL", "LIVENESS_TIMEOUT", "atomic_json", "run_streaming_command", "utc_now"]
