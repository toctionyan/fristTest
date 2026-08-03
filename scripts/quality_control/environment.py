from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .common import _interpolate, _npm_executable
from .constants import BLOCKED

def _python_ast_parse(workspace: Path) -> dict[str, Any]:
    roots = [workspace / "services", workspace / "architecture-skill", workspace / "scripts"]
    parsed = 0
    errors: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts or ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                parsed += 1
            except SyntaxError as exc:
                errors.append(f"{path.relative_to(workspace)}:{exc.lineno}:{exc.msg}")
    return {
        "exit_code": 0 if not errors else 1,
        "stdout": f"parsed {parsed} Python files\n",
        "stderr": "\n".join(errors),
        "metadata": {"parsed_files": parsed, "syntax_errors": errors},
    }

def _probe_http(url: str, *, path: str, timeout_seconds: float) -> bool:
    base = url.rstrip("/")
    request = urllib.request.Request(f"{base}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 400
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return False

def _probe_tcp_url(url: str, *, timeout_seconds: float) -> bool:
    normalized = re.sub(r"^postgresql\+[A-Za-z0-9_+-]+://", "postgresql://", url)
    parsed = urllib.parse.urlparse(normalized)
    host = parsed.hostname
    if not host:
        return False
    port = int(parsed.port or (5432 if parsed.scheme.startswith("postgres") else 80))
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False

def _environment_problem(workspace: Path, step: dict[str, Any]) -> list[str]:
    requirements = step.get("environment") or {}
    missing: list[str] = []
    for command in requirements.get("commands", []):
        command_name = str(command)
        available = bool(_npm_executable(workspace)) if command_name == "npm" else bool(shutil.which(command_name))
        if not available:
            missing.append(f"command:{command}")
    for key in requirements.get("variables", []):
        if not os.getenv(str(key)):
            missing.append(f"environment:{key}")
    for key, allowed in (requirements.get("expected_values") or {}).items():
        values = {str(value) for value in (allowed if isinstance(allowed, list) else [allowed])}
        if os.getenv(str(key)) not in values:
            missing.append(f"environment:{key}:expected={'|'.join(sorted(values))}")
    for probe in requirements.get("probes", []):
        if not isinstance(probe, dict):
            missing.append("probe:invalid")
            continue
        kind = str(probe.get("kind") or "")
        variable = str(probe.get("url_env") or "")
        value = os.getenv(variable) if variable else None
        timeout = float(probe.get("timeout_seconds", 3))
        if not value:
            missing.append(f"environment:{variable or 'probe_url'}")
            continue
        if kind == "http" and not _probe_http(value, path=str(probe.get("path") or "/health"), timeout_seconds=timeout):
            missing.append(f"probe:http:{variable}")
        elif kind == "tcp" and not _probe_tcp_url(value, timeout_seconds=timeout):
            missing.append(f"probe:tcp:{variable}")
        elif kind not in {"http", "tcp"}:
            missing.append(f"probe:unknown:{kind}")
    return missing

def _terminate_process_group(proc: subprocess.Popen[Any], *, grace_seconds: float) -> None:
    """Stop the gate process and every descendant in its dedicated session."""
    if os.name != "posix":  # pragma: no cover - Windows fallback
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=grace_seconds)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(proc.pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

def _run_shell(workspace: Path, evidence_dir: Path, mode: str, step: dict[str, Any]) -> dict[str, Any]:
    argv_raw = step.get("argv")
    if not isinstance(argv_raw, list) or not argv_raw:
        return {"exit_code": 2, "stdout": "", "stderr": "step argv must be a non-empty array", "metadata": {}}
    argv = [_interpolate(str(item), workspace=workspace, evidence_dir=evidence_dir, mode=mode) for item in argv_raw]
    cwd = workspace / _interpolate(str(step.get("cwd", ".")), workspace=workspace, evidence_dir=evidence_dir, mode=mode)
    if not cwd.is_dir():
        return {"exit_code": 2, "stdout": "", "stderr": f"step cwd does not exist: {cwd}", "metadata": {}}
    env = os.environ.copy()
    # A quality-loop run may itself be launched by pytest-cov. Propagating
    # that bootstrap into nested pytest/npm gates causes recursive collection,
    # unstable timings and duplicate coverage files. Each declared gate owns
    # its own coverage evidence, so strip only the outer bootstrap variables.
    for key in tuple(env):
        if key.startswith("COV_CORE_") or key == "COVERAGE_PROCESS_START":
            env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Every Gate gets an explicit, writable evidence boundary.  Build tools and
    # browser runners must put generated artifacts here instead of rewriting
    # governed workspace inputs (for example frontend/dist).  These values are
    # controller-owned and therefore deliberately override inherited shell
    # values before the Gate-specific environment is applied below.
    env["QUALITY_EVIDENCE_DIR"] = str(evidence_dir)
    env["QUALITY_LOOP_MODE"] = mode
    env["QUALITY_GATE_ID"] = str(step.get("id") or "")
    npm = _npm_executable(workspace)
    if npm is not None:
        env["PATH"] = str(npm.parent) + os.pathsep + env.get("PATH", "")
    for key, value in (step.get("env") or {}).items():
        env[str(key)] = _interpolate(str(value), workspace=workspace, evidence_dir=evidence_dir, mode=mode)
    timeout = int(step.get("timeout_seconds", 300))
    started = time.monotonic()
    timed_out = False
    with tempfile.TemporaryDirectory(prefix=f"quality-step-{step.get('id', 'gate')}-") as temp:
        stdout_path = Path(temp) / "stdout.log"
        stderr_path = Path(temp) / "stderr.log"
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_group(proc, grace_seconds=10)
                if proc.poll() is None:
                    proc.wait(timeout=2)
            else:
                # A successful gate is not allowed to leave background workers
                # alive. They can retain controller descriptors and corrupt the
                # next Gate even though the declared command already returned.
                _terminate_process_group(proc, grace_seconds=0.5)
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    duration_ms = int((time.monotonic() - started) * 1000)
    if timed_out:
        stderr = (stderr or "") + f"\nquality_loop_step_timeout_after_{timeout}s"
    return {
        "exit_code": 124 if timed_out else int(proc.returncode or 0),
        "stdout": stdout or "",
        "stderr": stderr or "",
        "duration_ms": duration_ms,
        "metadata": {"argv": argv},
    }

def _runtime_environment_block_evidence(raw: dict[str, Any]) -> dict[str, str] | None:
    """Accept a dynamic environment block only through an explicit protocol.

    Exit 78 by itself is not trusted: undeclared test failures must remain red.
    A Gate that discovers provider/network unavailability at runtime must emit a
    final JSON object with the exact blocked status and a non-empty reason.
    """
    if int(raw.get("exit_code") or 0) != 78:
        return None
    for line in reversed(str(raw.get("stdout") or "").splitlines()):
        try:
            payload = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        reason = str(payload.get("reason") or "").strip()
        if payload.get("status") == BLOCKED and reason:
            return {"status": BLOCKED, "reason": reason}
    return None

def _structured_stdout_payload(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Return a structured Gate payload only when stdout is one JSON object.

    Gate stdout remains human-readable evidence.  This parser is deliberately
    conservative so arbitrary log fragments can never silently influence the
    controller decision.
    """

    text = str(raw.get("stdout") or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None

