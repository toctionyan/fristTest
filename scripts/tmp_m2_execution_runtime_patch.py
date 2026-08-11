#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def _replace_function(path: Path, name: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next((row for row in tree.body if isinstance(row, (ast.FunctionDef, ast.AsyncFunctionDef)) and row.name == name), None)
    if node is None or node.end_lineno is None:
        raise RuntimeError(f"function {name} not found in {path}")
    lines = text.splitlines(keepends=True)
    block = textwrap.dedent(replacement).strip("\n") + "\n\n"
    lines[node.lineno - 1:node.end_lineno] = [block]
    path.write_text("".join(lines), encoding="utf-8")


def _remove_function(path: Path, name: str) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    node = next((row for row in tree.body if isinstance(row, (ast.FunctionDef, ast.AsyncFunctionDef)) and row.name == name), None)
    if node is None or node.end_lineno is None:
        return
    lines = text.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = []
    path.write_text("".join(lines), encoding="utf-8")


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"anchor not found in {path}: {old[:80]!r}")
    if text.count(old) != 1:
        raise RuntimeError(f"anchor count != 1 in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


runtime = ROOT / "skill-system" / "controller" / "execution_runtime.py"
text = runtime.read_text(encoding="utf-8")
if "EXTERNAL_WAIT_CONTRACT" not in text:
    _replace_once(
        runtime,
        'LIVENESS_TIMEOUT = "TIMEOUT"\n',
        'LIVENESS_TIMEOUT = "TIMEOUT"\nLIVENESS_WAITING_EXTERNAL = "RUNNING_WAITING_EXTERNAL"\nLIVENESS_STALL_TIMEOUT = "STALL_TIMEOUT"\nEXTERNAL_WAIT_CONTRACT = "execution-external-wait@1"\n',
    )
if "def validate_external_wait_evidence" not in runtime.read_text(encoding="utf-8"):
    _replace_once(
        runtime,
        'def _positive_seconds(value: float, *, name: str) -> float:\n',
        '''def _parse_utc(value: object) -> datetime | None:\n    text = str(value or "").strip()\n    if not text:\n        return None\n    try:\n        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))\n    except ValueError:\n        return None\n    if parsed.tzinfo is None:\n        return None\n    return parsed.astimezone(timezone.utc)\n\n\ndef validate_external_wait_evidence(\n    payload: Mapping[str, Any] | None,\n    *,\n    expected_scope: Mapping[str, str] | None = None,\n) -> dict[str, Any] | None:\n    """Accept only a fresh, scoped machine lease as external-wait authority."""\n    if not isinstance(payload, Mapping):\n        return None\n    if str(payload.get("contract") or "") != EXTERNAL_WAIT_CONTRACT:\n        return None\n    if str(payload.get("status") or "") != "WAITING_EXTERNAL":\n        return None\n    external_ref = payload.get("external_ref")\n    scope = payload.get("scope")\n    if not isinstance(external_ref, Mapping) or not isinstance(scope, Mapping):\n        return None\n    if not str(external_ref.get("kind") or "").strip() or not str(external_ref.get("id") or "").strip():\n        return None\n    if not str(scope.get("kind") or "").strip() or not str(scope.get("id") or "").strip():\n        return None\n    if expected_scope is not None:\n        for key, value in expected_scope.items():\n            if str(scope.get(key) or "") != str(value):\n                return None\n    heartbeat_at = _parse_utc(payload.get("heartbeat_at"))\n    expires_at = _parse_utc(payload.get("expires_at"))\n    if heartbeat_at is None or expires_at is None:\n        return None\n    now = datetime.now(timezone.utc)\n    if expires_at <= now or heartbeat_at > now:\n        return None\n    return dict(payload)\n\n\ndef external_wait_file_probe(\n    path: Path,\n    *,\n    expected_scope: Mapping[str, str] | None = None,\n) -> Callable[[], dict[str, Any] | None]:\n    resolved = path.expanduser().resolve()\n\n    def probe() -> dict[str, Any] | None:\n        try:\n            payload = json.loads(resolved.read_text(encoding="utf-8"))\n        except (OSError, json.JSONDecodeError):\n            return None\n        return validate_external_wait_evidence(payload, expected_scope=expected_scope)\n\n    return probe\n\n\ndef _positive_seconds(value: float, *, name: str) -> float:\n''',
    )

_replace_function(
    runtime,
    "_terminate_process",
    r'''
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
    _cleanup_process_group(process, grace_seconds=5.0)
    if process.poll() is None:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
''',
)

_replace_function(
    runtime,
    "run_streaming_command",
    r'''
def run_streaming_command(
    argv: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    heartbeat_seconds: float = 30.0,
    stall_warning_seconds: float = 240.0,
    stall_timeout_seconds: float | None = None,
    timeout_seconds: float | None = None,
    on_heartbeat: Callable[[dict[str, Any]], None] | None = None,
    external_wait_probe: Callable[[], Mapping[str, Any] | None] | None = None,
    stream_output: bool = False,
    stdout_mirror: TextIO | None = None,
    stderr_mirror: TextIO | None = None,
    cleanup_process_group_after_exit: bool = False,
    cleanup_grace_seconds: float = 0.5,
) -> dict[str, Any]:
    """Run one command with durable liveness while preserving captured output.

    Silence alone never proves an external wait. A no-progress stall timeout is
    suppressed only while ``external_wait_probe`` returns a fresh, scoped lease
    satisfying ``execution-external-wait@1``. The explicit overall timeout is
    never suppressed.
    """
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv must contain non-empty strings")
    heartbeat_seconds = _positive_seconds(heartbeat_seconds, name="heartbeat_seconds")
    stall_warning_seconds = max(
        _positive_seconds(stall_warning_seconds, name="stall_warning_seconds"),
        heartbeat_seconds,
    )
    if stall_timeout_seconds is not None:
        stall_timeout_seconds = max(
            _positive_seconds(stall_timeout_seconds, name="stall_timeout_seconds"),
            stall_warning_seconds + heartbeat_seconds,
        )
    if timeout_seconds is not None:
        timeout_seconds = _positive_seconds(timeout_seconds, name="timeout_seconds")
    cleanup_grace_seconds = max(0.0, float(cleanup_grace_seconds))
    if stream_output:
        stdout_mirror = stdout_mirror or sys.stderr
        stderr_mirror = stderr_mirror or sys.stderr

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
            "mirror": stdout_mirror,
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
            "mirror": stderr_mirror,
        },
        daemon=True,
    )
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
    latest_external_wait: dict[str, Any] | None = None
    while process.poll() is None:
        now = time.monotonic()
        with lock:
            snapshot = dict(activity)
        elapsed = now - started_monotonic
        last_progress = float(snapshot.get("last_progress_monotonic") or started_monotonic)
        idle = now - last_progress
        external_wait = current_external_wait()
        if external_wait is not None:
            latest_external_wait = external_wait
        if (
            stall_timeout_seconds is not None
            and idle >= stall_timeout_seconds
            and external_wait is None
        ):
            termination_reason = "no_progress_stall"
            timed_out = True
            stall_timed_out = True
            stall_payload = _payload(
                process=process,
                started_at=started_at,
                started_monotonic=started_monotonic,
                activity=snapshot,
                liveness_status=LIVENESS_STALL_TIMEOUT,
                termination_reason=termination_reason,
            )
            if on_heartbeat is not None:
                on_heartbeat(stall_payload)
            _terminate_process(process)
            break
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
            if external_wait is not None:
                timeout_payload["external_wait_evidence"] = external_wait
            if on_heartbeat is not None:
                on_heartbeat(timeout_payload)
            _terminate_process(process)
            break
        if now >= next_heartbeat:
            status = (
                LIVENESS_WAITING_EXTERNAL
                if external_wait is not None
                else LIVENESS_SUSPECTED_STALL
                if idle >= stall_warning_seconds
                else LIVENESS_RUNNING
            )
            heartbeat = _payload(
                process=process,
                started_at=started_at,
                started_monotonic=started_monotonic,
                activity=snapshot,
                liveness_status=status,
            )
            if external_wait is not None:
                heartbeat["external_wait_evidence"] = external_wait
            if on_heartbeat is not None:
                on_heartbeat(heartbeat)
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
    final_liveness = (
        LIVENESS_STALL_TIMEOUT
        if stall_timed_out
        else LIVENESS_TIMEOUT
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
    if latest_external_wait is not None:
        final_payload["external_wait_evidence"] = latest_external_wait
    if on_heartbeat is not None:
        on_heartbeat(final_payload)
    return {
        "exit_code": returncode,
        "stdout": "".join(stdout_chunks),
        "stderr": "".join(stderr_chunks),
        "timed_out": timed_out,
        "stall_timed_out": stall_timed_out,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "liveness_status": final_liveness,
        "last_progress_at": str(final_activity.get("last_progress_at") or started_at),
        "progress_event_count": int(final_activity.get("progress_event_count") or 0),
        "termination_reason": termination_reason,
        "external_wait_evidence": latest_external_wait,
    }
''',
)

text = runtime.read_text(encoding="utf-8")
for name in [
    '"EXTERNAL_WAIT_CONTRACT"',
    '"LIVENESS_STALL_TIMEOUT"',
    '"LIVENESS_WAITING_EXTERNAL"',
    '"external_wait_file_probe"',
    '"validate_external_wait_evidence"',
]:
    if name not in text:
        _replace_once(runtime, '__all__ = [\n', '__all__ = [\n    ' + name + ',\n')
        text = runtime.read_text(encoding="utf-8")


environment = ROOT / "scripts" / "quality_control" / "environment.py"
if "from execution_runtime import" not in environment.read_text(encoding="utf-8"):
    _replace_once(
        environment,
        'from .constants import BLOCKED\n',
        '''from .constants import BLOCKED\n\nCONTROL_PLANE_DIR = Path(__file__).resolve().parents[2] / "skill-system" / "controller"\nif str(CONTROL_PLANE_DIR) not in sys.path:\n    sys.path.insert(0, str(CONTROL_PLANE_DIR))\nfrom execution_runtime import (  # type: ignore  # noqa: E402\n    atomic_json as _execution_atomic_json,\n    external_wait_file_probe,\n    run_streaming_command,\n)\n''',
    )
if "import sys\n" not in environment.read_text(encoding="utf-8"):
    _replace_once(environment, "import subprocess\n", "import subprocess\nimport sys\n")

_replace_function(
    environment,
    "_run_shell",
    r'''
def _run_shell(workspace: Path, evidence_dir: Path, mode: str, step: dict[str, Any]) -> dict[str, Any]:
    argv_raw = step.get("argv")
    if not isinstance(argv_raw, list) or not argv_raw:
        return {"exit_code": 2, "stdout": "", "stderr": "step argv must be a non-empty array", "metadata": {}}
    argv = [_interpolate(str(item), workspace=workspace, evidence_dir=evidence_dir, mode=mode) for item in argv_raw]
    cwd = workspace / _interpolate(str(step.get("cwd", ".")), workspace=workspace, evidence_dir=evidence_dir, mode=mode)
    if not cwd.is_dir():
        return {"exit_code": 2, "stdout": "", "stderr": f"step cwd does not exist: {cwd}", "metadata": {}}
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("COV_CORE_") or key == "COVERAGE_PROCESS_START":
            env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QUALITY_EVIDENCE_DIR"] = str(evidence_dir)
    env["QUALITY_LOOP_MODE"] = mode
    env["QUALITY_GATE_ID"] = str(step.get("id") or "")
    npm = _npm_executable(workspace)
    if npm is not None:
        env["PATH"] = str(npm.parent) + os.pathsep + env.get("PATH", "")
    for key, value in (step.get("env") or {}).items():
        env[str(key)] = _interpolate(str(value), workspace=workspace, evidence_dir=evidence_dir, mode=mode)

    timeout = int(step.get("timeout_seconds", 300))
    gate_id = str(step.get("id") or "gate")
    safe_gate_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", gate_id).strip("-") or "gate"
    liveness_dir = evidence_dir / "liveness"
    liveness_file = liveness_dir / f"{safe_gate_id}.json"
    external_wait_file = liveness_dir / f"{safe_gate_id}.external-wait.json"
    external_wait_file.parent.mkdir(parents=True, exist_ok=True)
    external_wait_file.unlink(missing_ok=True)
    env.update({
        "EXECUTION_EXTERNAL_WAIT_FILE": str(external_wait_file),
        "EXECUTION_EXTERNAL_WAIT_CONTRACT": "execution-external-wait@1",
        "EXECUTION_EXTERNAL_WAIT_SCOPE_KIND": "quality_gate",
        "EXECUTION_EXTERNAL_WAIT_SCOPE_ID": gate_id,
    })

    def publish(event: dict[str, Any]) -> None:
        payload = {
            **event,
            "contract": "quality-gate-liveness@1",
            "gate_id": gate_id,
            "mode": mode,
        }
        _execution_atomic_json(liveness_file, payload)
        print("[quality-gate heartbeat] " + json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)

    raw = run_streaming_command(
        argv,
        cwd=cwd,
        env=env,
        heartbeat_seconds=float(step.get("heartbeat_seconds") or 30.0),
        stall_warning_seconds=float(step.get("stall_warning_seconds") or 240.0),
        timeout_seconds=timeout,
        on_heartbeat=publish,
        external_wait_probe=external_wait_file_probe(
            external_wait_file,
            expected_scope={"kind": "quality_gate", "id": gate_id},
        ),
        cleanup_process_group_after_exit=True,
        cleanup_grace_seconds=0.5,
    )
    stderr = str(raw.get("stderr") or "")
    if bool(raw.get("timed_out")):
        stderr += f"\nquality_loop_step_timeout_after_{timeout}s"
    return {
        "exit_code": int(raw.get("exit_code") or 0),
        "stdout": str(raw.get("stdout") or ""),
        "stderr": stderr,
        "duration_ms": int(float(raw.get("duration_seconds") or 0.0) * 1000),
        "metadata": {"argv": argv},
    }
''',
)


wp08 = ROOT / "scripts" / "run_wp08_certification.py"
if "from execution_runtime import" not in wp08.read_text(encoding="utf-8"):
    _replace_once(
        wp08,
        'from typing import Any, Iterable, Mapping, TextIO\n',
        '''from typing import Any, Iterable, Mapping, TextIO\n\nCONTROL_PLANE_DIR = Path(__file__).resolve().parents[1] / "skill-system" / "controller"\nif str(CONTROL_PLANE_DIR) not in sys.path:\n    sys.path.insert(0, str(CONTROL_PLANE_DIR))\nfrom execution_runtime import (  # type: ignore  # noqa: E402\n    ExecutionRuntimeError,\n    atomic_json as _execution_atomic_json,\n    external_wait_file_probe,\n    run_streaming_command,\n)\n''',
    )

_replace_function(
    wp08,
    "_atomic_json",
    r'''
def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _execution_atomic_json(path, payload)
''',
)
_remove_function(wp08, "_terminate_process")
_remove_function(wp08, "_stream_reader")

_replace_function(
    wp08,
    "_heartbeat_payload",
    r'''
def _heartbeat_payload(
    *,
    env: Mapping[str, str],
    runtime_payload: Mapping[str, Any],
) -> dict[str, Any]:
    generic = str(runtime_payload.get("liveness_status") or "")
    status_map = {
        "RUNNING_ACTIVE": "RUNNING",
        "RUNNING_WAITING_EXTERNAL": "RUNNING_WAITING_EXTERNAL",
        "SUSPECTED_STALL": "SUSPECTED_STALL",
        "STALL_TIMEOUT": "STALL_TIMEOUT",
        "TIMEOUT": "TIMEOUT",
        "COMPLETED": "BATCH_COMPLETED",
        "FAILED": "BATCH_COMPLETED",
    }
    termination_reason = runtime_payload.get("termination_reason")
    if termination_reason == "command_timeout":
        termination_reason = "batch_timeout"
    batch_index = int(str(env.get("WP08_BATCH_INDEX") or "0") or "0")
    batch_total = int(str(env.get("WP08_BATCH_TOTAL") or "0") or "0")
    payload = {
        "contract": LIVENESS_CONTRACT,
        "run_id": str(env.get("GITHUB_RUN_ID") or ""),
        "run_attempt": str(env.get("GITHUB_RUN_ATTEMPT") or ""),
        "commit_sha": str(env.get("GITHUB_SHA") or ""),
        "heartbeat_at": runtime_payload.get("heartbeat_at") or utc_now(),
        "batch_started_at": runtime_payload.get("started_at"),
        "last_progress_at": runtime_payload.get("last_progress_at"),
        "last_activity": runtime_payload.get("last_activity"),
        "progress_event_count": int(runtime_payload.get("progress_event_count") or 0),
        "liveness_status": status_map.get(generic, generic or "RUNNING"),
        "child_process_alive": bool(runtime_payload.get("child_process_alive")),
        "child_pid": runtime_payload.get("child_pid"),
        "elapsed_seconds": runtime_payload.get("elapsed_seconds"),
        "idle_seconds": runtime_payload.get("idle_seconds"),
        "current_batch": {
            "id": str(env.get("WP08_CURRENT_BATCH_ID") or ""),
            "title": str(env.get("WP08_CURRENT_BATCH_TITLE") or ""),
            "index": batch_index,
            "total": batch_total,
            "timeout_seconds": int(str(env.get("WP08_CURRENT_BATCH_TIMEOUT") or "0") or "0"),
        },
        "termination_reason": termination_reason,
        "production_closed": False,
    }
    if runtime_payload.get("external_wait_evidence") is not None:
        payload["external_wait_evidence"] = runtime_payload.get("external_wait_evidence")
    if runtime_payload.get("returncode") is not None:
        payload["returncode"] = runtime_payload.get("returncode")
    return payload
''',
)

_replace_function(
    wp08,
    "_run_process",
    r'''
def _run_process(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[int, str, str, bool]:
    heartbeat_seconds = _positive_float(
        env, "WP08_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS
    )
    stall_warning_seconds = _positive_float(
        env, "WP08_STALL_WARNING_SECONDS", DEFAULT_STALL_WARNING_SECONDS
    )
    stall_timeout_seconds = _positive_float(
        env, "WP08_STALL_TIMEOUT_SECONDS", DEFAULT_STALL_TIMEOUT_SECONDS
    )
    stall_warning_seconds = max(stall_warning_seconds, heartbeat_seconds)
    stall_timeout_seconds = max(stall_timeout_seconds, stall_warning_seconds + heartbeat_seconds)

    external_wait_raw = str(env.get("WP08_EXTERNAL_WAIT_FILE") or "").strip()
    external_wait_probe = None
    if external_wait_raw:
        external_wait_probe = external_wait_file_probe(
            Path(external_wait_raw),
            expected_scope={
                "kind": "wp08_batch",
                "id": str(env.get("WP08_CURRENT_BATCH_ID") or ""),
            },
        )

    def publish(runtime_event: dict[str, Any]) -> None:
        payload = _heartbeat_payload(env=env, runtime_payload=runtime_event)
        _publish_liveness(env, payload)
        status = str(payload.get("liveness_status") or "")
        alive = bool(payload.get("child_process_alive"))
        if alive and status == "STALL_TIMEOUT":
            prefix = "[WP08 STALL] "
        elif alive and status == "TIMEOUT":
            prefix = "[WP08 TIMEOUT] "
        elif status == "BATCH_COMPLETED" or not alive:
            prefix = "[WP08 LIVENESS] "
        else:
            prefix = "[WP08 HEARTBEAT] "
        print(prefix + json.dumps(payload, ensure_ascii=False), flush=True)

    try:
        raw = run_streaming_command(
            command,
            cwd=cwd,
            env=env,
            heartbeat_seconds=heartbeat_seconds,
            stall_warning_seconds=stall_warning_seconds,
            stall_timeout_seconds=stall_timeout_seconds,
            timeout_seconds=timeout_seconds,
            on_heartbeat=publish,
            external_wait_probe=external_wait_probe,
            stdout_mirror=sys.stdout,
            stderr_mirror=sys.stderr,
        )
    except ExecutionRuntimeError as exc:
        payload = {
            "status": BLOCKED,
            "reason": "batch_executable_unavailable",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
        return 78, json.dumps(payload, ensure_ascii=False) + "\n", str(exc), False
    return (
        int(raw.get("exit_code") or 0),
        str(raw.get("stdout") or ""),
        str(raw.get("stderr") or ""),
        bool(raw.get("timed_out")),
    )
''',
)

anchor = '''        batch_env.update({\n            "WP08_CURRENT_BATCH_ID": batch_id,\n            "WP08_CURRENT_BATCH_TITLE": str(batch["title"]),\n            "WP08_CURRENT_BATCH_TIMEOUT": str(batch["timeout_seconds"]),\n            "WP08_BATCH_INDEX": str(batch_index),\n            "WP08_BATCH_TOTAL": str(batch_total),\n        })\n'''
replacement = '''        external_wait_file = batch_dir / "external-wait.json"\n        external_wait_file.unlink(missing_ok=True)\n        batch_env.update({\n            "WP08_CURRENT_BATCH_ID": batch_id,\n            "WP08_CURRENT_BATCH_TITLE": str(batch["title"]),\n            "WP08_CURRENT_BATCH_TIMEOUT": str(batch["timeout_seconds"]),\n            "WP08_BATCH_INDEX": str(batch_index),\n            "WP08_BATCH_TOTAL": str(batch_total),\n            "WP08_EXTERNAL_WAIT_FILE": str(external_wait_file),\n            "EXECUTION_EXTERNAL_WAIT_FILE": str(external_wait_file),\n            "EXECUTION_EXTERNAL_WAIT_CONTRACT": "execution-external-wait@1",\n            "EXECUTION_EXTERNAL_WAIT_SCOPE_KIND": "wp08_batch",\n            "EXECUTION_EXTERNAL_WAIT_SCOPE_ID": batch_id,\n        })\n'''
if '"WP08_EXTERNAL_WAIT_FILE": str(external_wait_file)' not in wp08.read_text(encoding="utf-8"):
    _replace_once(wp08, anchor, replacement)

# Fail fast on syntax before the workflow commits anything.
for path in (runtime, environment, wp08):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

print("M2 execution-runtime patch applied")
