#!/usr/bin/env python3
"""Run WP-08 certification batches with bounded time, durable checkpoints, and resume.

This is a pre-production diagnostic orchestrator.  It deliberately does not
produce ``production_closed`` evidence.  Its job is to execute every independent
WP-08 batch, preserve each result, and prevent one blocked or hung component
from hiding the state of the remaining components.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

CONTRACT = "wp08-resumable-certification@1"
PASS = "PASS"
BLOCKED = "BLOCKED_BY_ENVIRONMENT"
FAIL = "FAIL"
TIMEOUT = "TIMEOUT"
VALID_STATUSES = {PASS, BLOCKED, FAIL, TIMEOUT}
DEFAULT_CONFIG = "deployment/ci/wp08-certification-batches.json"


class CertificationInputError(RuntimeError):
    """Raised when resume or batch configuration input is unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CertificationInputError(f"invalid JSON input: {path}") from exc
    if not isinstance(payload, dict):
        raise CertificationInputError(f"JSON object required: {path}")
    return payload


def _last_json(text: str) -> dict[str, Any] | None:
    for line in reversed(str(text or "").splitlines()):
        try:
            payload = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _production_workspace_fingerprint(workspace: Path) -> str:
    """Match production_certification_contract.workspace_fingerprint without runtime imports."""
    excluded_parts = {
        ".git", ".quality", ".pytest_cache", "__pycache__", "node_modules",
        ".venv", "venv", "dist", "build", "coverage", "runtime",
    }
    allowed_suffixes = {
        ".py", ".json", ".md", ".toml", ".yaml", ".yml", ".js", ".jsx",
        ".ts", ".tsx", ".css", ".html", ".sh", ".txt", ".lock",
    }
    digest = hashlib.sha256()
    count = 0
    for path in sorted(workspace.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or any(part in excluded_parts for part in path.relative_to(workspace).parts):
            continue
        if path.name not in {"VERSION", "Dockerfile", "Makefile"} and path.suffix.casefold() not in allowed_suffixes:
            continue
        relative = path.relative_to(workspace).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        count += 1
    if count == 0:
        raise CertificationInputError("workspace has no certifiable source files")
    return digest.hexdigest()


def _source_fingerprint(workspace: Path, config_path: Path) -> str:
    digest = hashlib.sha256()
    candidates = [
        workspace / "release" / "MANIFEST.json",
        workspace / "VERSION",
        workspace / "PHASE_CANDIDATE_MANIFEST.json",
        config_path,
        workspace / "scripts" / "run_wp08_certification.py",
        workspace / "scripts" / "prepare_wp08_resume.py",
        workspace / ".github" / "workflows" / "wp08-certification.yml",
        workspace / "deployment" / "ci" / "release-toolchain-lock.json",
    ]
    included = 0
    for path in candidates:
        if not path.is_file():
            continue
        digest.update(path.relative_to(workspace).as_posix().encode("utf-8") if path.is_relative_to(workspace) else str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        included += 1
    if included == 0:
        raise CertificationInputError("no source identity inputs are available")
    return digest.hexdigest()


def _expand_command(
    command: Iterable[Any],
    *,
    workspace: Path,
    evidence_dir: Path,
    state_dir: Path,
) -> list[str]:
    values = {
        "workspace": str(workspace),
        "evidence_dir": str(evidence_dir),
        "state_dir": str(state_dir),
        "python": sys.executable,
        "agent_python": str(workspace / "services" / "agent-service" / ".venv" / "bin" / "python"),
        "business_python": str(workspace / "services" / "business-service" / ".venv" / "bin" / "python"),
    }
    result: list[str] = []
    for item in command:
        if not isinstance(item, str) or not item.strip():
            raise CertificationInputError("batch command entries must be non-empty strings")
        expanded = item
        for key, value in values.items():
            expanded = expanded.replace("{" + key + "}", value)
        result.append(expanded)
    return result


def load_batches(
    config_path: Path,
    *,
    workspace: Path,
    evidence_dir: Path,
    state_dir: Path,
) -> list[dict[str, Any]]:
    payload = _load_json(config_path)
    if payload.get("contract") != "wp08-certification-batches@1":
        raise CertificationInputError("unsupported WP-08 batch config contract")
    rows = payload.get("batches")
    if not isinstance(rows, list) or not rows:
        raise CertificationInputError("WP-08 batch config must contain batches")
    seen: set[str] = set()
    batches: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CertificationInputError(f"batch {index} must be an object")
        batch_id = str(row.get("id") or "").strip()
        if not batch_id or batch_id in seen:
            raise CertificationInputError(f"batch id is missing or duplicated: {batch_id!r}")
        seen.add(batch_id)
        timeout_seconds = int(row.get("timeout_seconds") or 0)
        if timeout_seconds < 1 or timeout_seconds > 21600:
            raise CertificationInputError(f"invalid timeout for batch {batch_id}")
        command = row.get("command")
        if not isinstance(command, list) or not command:
            raise CertificationInputError(f"batch {batch_id} requires command")
        component = str(row.get("production_component") or "").strip()
        if component and component not in {"real_model", "postgres", "browser"}:
            raise CertificationInputError(f"invalid production component for batch {batch_id}")
        batches.append({
            "id": batch_id,
            "title": str(row.get("title") or batch_id),
            "timeout_seconds": timeout_seconds,
            "command": _expand_command(command, workspace=workspace, evidence_dir=evidence_dir, state_dir=state_dir),
            "required": bool(row.get("required", True)),
            "production_component": component or None,
        })
    return batches


def _classify(returncode: int, payload: Mapping[str, Any] | None) -> str:
    payload_status = str((payload or {}).get("status") or "").strip().upper()
    if payload_status in VALID_STATUSES:
        if returncode == 0 and payload_status != PASS:
            return FAIL
        if returncode not in (0, 78) and payload_status == PASS:
            return FAIL
        return payload_status
    if returncode == 0:
        return PASS
    if returncode == 78:
        return BLOCKED
    return FAIL


def _run_process(command: list[str], *, cwd: Path, env: Mapping[str, str], timeout_seconds: int) -> tuple[int, str, str, bool]:
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        payload = {
            "status": BLOCKED,
            "reason": "batch_executable_unavailable",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
        return 78, json.dumps(payload, ensure_ascii=False) + "\n", str(exc), False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return int(process.returncode), stdout, stderr, False
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
        return 124, stdout, stderr, True


def run_certification(
    *,
    workspace: Path,
    config_path: Path,
    evidence_dir: Path,
    state_file: Path,
    resume: bool,
    only: set[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], int]:
    workspace = workspace.resolve()
    config_path = config_path.resolve()
    evidence_dir = evidence_dir.resolve()
    state_file = state_file.resolve()
    state_dir = state_file.parent
    evidence_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    batches = load_batches(
        config_path,
        workspace=workspace,
        evidence_dir=evidence_dir,
        state_dir=state_dir,
    )
    known = {row["id"] for row in batches}
    if only:
        unknown = sorted(only - known)
        if unknown:
            raise CertificationInputError("unknown batch ids: " + ", ".join(unknown))
        batches = [row for row in batches if row["id"] in only]

    fingerprint = _source_fingerprint(workspace, config_path)
    previous: dict[str, Any] = {}
    if resume and state_file.is_file():
        previous = _load_json(state_file)
        if previous.get("contract") != CONTRACT:
            raise CertificationInputError("resume state contract is invalid")
        if previous.get("source_fingerprint_sha256") != fingerprint:
            raise CertificationInputError("resume state belongs to another source identity")
    previous_batches = previous.get("batches") if isinstance(previous.get("batches"), dict) else {}

    diagnostic_session_id = str(previous.get("diagnostic_session_id") or "").strip()
    if not diagnostic_session_id:
        diagnostic_session_id = "prodcert-" + secrets.token_hex(24)
    diagnostic_started_at = str(previous.get("diagnostic_started_at") or utc_now())
    production_fingerprint = _production_workspace_fingerprint(workspace)

    state: dict[str, Any] = {
        "contract": CONTRACT,
        "status": "RUNNING",
        "source_fingerprint_sha256": fingerprint,
        "production_workspace_fingerprint_sha256": production_fingerprint,
        "diagnostic_session_id": diagnostic_session_id,
        "diagnostic_started_at": diagnostic_started_at,
        "workspace": str(workspace),
        "config": str(config_path),
        "started_at": previous.get("started_at") or utc_now(),
        "updated_at": utc_now(),
        "resume": bool(resume),
        "batches": dict(previous_batches),
    }
    _atomic_json(state_file, state)

    env = dict(os.environ if environment is None else environment)
    env.update({
        "WP08_CERTIFICATION_EVIDENCE_DIR": str(evidence_dir),
        "WP08_CERTIFICATION_STATE_FILE": str(state_file),
        "PYTHONDONTWRITEBYTECODE": "1",
    })

    for batch in batches:
        batch_id = batch["id"]
        prior = state["batches"].get(batch_id)
        if resume and isinstance(prior, dict) and prior.get("status") == PASS:
            state["batches"][batch_id] = {**prior, "resume_action": "SKIPPED_ALREADY_PASS"}
            state["updated_at"] = utc_now()
            _atomic_json(state_file, state)
            continue

        batch_dir = evidence_dir / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        started_at = utc_now()
        batch_env = dict(env)
        component = batch.get("production_component")
        if component:
            batch_env.update({
                "PRODUCTION_CERTIFICATION_SESSION_ID": diagnostic_session_id,
                "PRODUCTION_CERTIFICATION_WORKSPACE_FINGERPRINT": production_fingerprint,
                "PRODUCTION_CERTIFICATION_SESSION_STARTED_AT": diagnostic_started_at,
                "PRODUCTION_CERTIFICATION_COMPONENT": str(component),
                "PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT": str(
                    env.get("PRODUCTION_CERTIFICATION_TOOLCHAIN_FINGERPRINT") or ""
                ).strip().casefold(),
            })
        returncode, stdout, stderr, timed_out = _run_process(
            batch["command"],
            cwd=workspace,
            env=batch_env,
            timeout_seconds=batch["timeout_seconds"],
        )
        duration = round(time.monotonic() - started, 3)
        (batch_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (batch_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        payload = _last_json(stdout)
        status = TIMEOUT if timed_out else _classify(returncode, payload)
        result = {
            "id": batch_id,
            "title": batch["title"],
            "status": status,
            "required": batch["required"],
            "returncode": returncode,
            "timeout_seconds": batch["timeout_seconds"],
            "timed_out": timed_out,
            "duration_seconds": duration,
            "started_at": started_at,
            "completed_at": utc_now(),
            "command": batch["command"],
            "command_display": shlex.join(batch["command"]),
            "stdout_log": str(batch_dir / "stdout.log"),
            "stderr_log": str(batch_dir / "stderr.log"),
            "payload": payload,
        }
        _atomic_json(batch_dir / "result.json", result)
        state["batches"][batch_id] = result
        state["updated_at"] = utc_now()
        _atomic_json(state_file, state)

    selected_ids = [row["id"] for row in batches]
    selected_results = [state["batches"].get(batch_id, {}) for batch_id in selected_ids]
    required_results = [row for row in selected_results if row.get("required", True)]
    statuses = [str(row.get("status") or FAIL) for row in required_results]
    if any(status in {FAIL, TIMEOUT} for status in statuses):
        overall, exit_code = FAIL, 1
    elif any(status == BLOCKED for status in statuses):
        overall, exit_code = BLOCKED, 78
    elif statuses and all(status == PASS for status in statuses):
        overall, exit_code = PASS, 0
    else:
        overall, exit_code = FAIL, 1

    state.update({
        "status": overall,
        "completed_at": utc_now(),
        "updated_at": utc_now(),
        "selected_batches": selected_ids,
        "summary": {
            status: sum(1 for row in selected_results if row.get("status") == status)
            for status in (PASS, BLOCKED, FAIL, TIMEOUT)
        },
        "production_closed": False,
        "exit_code": exit_code,
    })
    _atomic_json(state_file, state)
    _atomic_json(evidence_dir / "wp08-certification-summary.json", state)
    return state, exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run resumable WP-08 certification batches.")
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--list-batches", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    workspace = Path(args.workspace_root).expanduser().resolve()
    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = workspace / config_path
    evidence_dir = Path(args.evidence_dir).expanduser().resolve()
    state_file = Path(args.state_file).expanduser().resolve()
    if args.reset:
        if state_file.exists():
            state_file.unlink()
        if evidence_dir.exists():
            import shutil
            shutil.rmtree(evidence_dir)
    try:
        batches = load_batches(
            config_path,
            workspace=workspace,
            evidence_dir=evidence_dir,
            state_dir=state_file.parent,
        )
        if args.list_batches:
            print(json.dumps({"contract": CONTRACT, "batches": batches}, ensure_ascii=False))
            return 0
        state, exit_code = run_certification(
            workspace=workspace,
            config_path=config_path,
            evidence_dir=evidence_dir,
            state_file=state_file,
            resume=bool(args.resume),
            only=set(args.only) or None,
        )
        print(json.dumps(state, ensure_ascii=False))
        return exit_code
    except CertificationInputError as exc:
        print(json.dumps({
            "contract": CONTRACT,
            "status": "INVALID_INPUT",
            "reason": str(exc),
            "production_closed": False,
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
