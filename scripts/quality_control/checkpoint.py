from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA_VERSION = 2


def _checkpoint_key(target_id: str) -> str:
    return hashlib.sha256(str(target_id).encode("utf-8")).hexdigest()


def active_checkpoint_path(state_dir: Path, *, target_id: str) -> Path:
    return Path(state_dir) / "active-runs" / f"{_checkpoint_key(target_id)}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Durably replace one checkpoint without exposing a partial JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("quality-loop checkpoint must be a JSON object")
    return raw


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_fingerprint(result: dict[str, Any]) -> str:
    payload = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def new_run_token() -> str:
    return secrets.token_hex(16)


def has_active_checkpoint_for_evidence(
    state_dir: Path,
    *,
    target_id: str,
    evidence_dir: Path,
) -> bool:
    """Allow a non-empty evidence directory to enter strict resume validation only."""
    path = active_checkpoint_path(state_dir, target_id=target_id)
    if not path.is_file():
        return False
    try:
        record = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(
        int(record.get("schema_version") or 0) == CHECKPOINT_SCHEMA_VERSION
        and str(record.get("status") or "") == "running"
        and str(record.get("run_token") or "")
        and str(record.get("evidence_dir") or "") == str(Path(evidence_dir).absolute())
    )


def load_compatible_active_checkpoint(
    state_dir: Path,
    *,
    target_id: str,
    target_identity: dict[str, Any],
    policy_fingerprint: str,
    workspace_start_fingerprint: str,
    mode: str,
    selected_gate_ids: list[str],
    evidence_dir: Path,
) -> dict[str, Any] | None:
    """Return only a checkpoint belonging to the exact interrupted run.

    A checkpoint is resume authority only when the target, policy, governed
    source snapshot, mode, gate closure and evidence directory are identical.
    Any mismatch is treated as a fresh run; historical PASS rows are never
    promoted into the new run.
    """
    path = active_checkpoint_path(state_dir, target_id=target_id)
    if not path.is_file():
        return None
    record = _load_json(path)
    if int(record.get("schema_version") or 0) != CHECKPOINT_SCHEMA_VERSION:
        return None
    if str(record.get("status") or "") != "running":
        return None
    expected = {
        "target_identity": target_identity,
        "policy_fingerprint": str(policy_fingerprint),
        "workspace_start_fingerprint": str(workspace_start_fingerprint),
        "mode": str(mode),
        "selected_gate_ids": [str(value) for value in selected_gate_ids],
        "evidence_dir": str(Path(evidence_dir).absolute()),
    }
    for key, value in expected.items():
        if record.get(key) != value:
            return None
    if not str(record.get("run_token") or ""):
        return None
    return record


def completed_step_record(evidence_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Bind one completed result to the exact step logs already written to disk."""
    step_id = str(result.get("id") or "")
    if not step_id:
        raise ValueError("completed checkpoint step requires a non-empty id")
    steps_dir = Path(evidence_dir) / "steps"
    stdout_path = steps_dir / f"{step_id}.stdout.txt"
    stderr_path = steps_dir / f"{step_id}.stderr.txt"
    if not stdout_path.is_file() or not stderr_path.is_file():
        raise ValueError(f"completed checkpoint step evidence missing: {step_id}")
    expected_stdout = str(result.get("stdout") or "")
    expected_stderr = str(result.get("stderr") or "")
    if stdout_path.read_text(encoding="utf-8") != expected_stdout:
        raise ValueError(f"completed checkpoint stdout does not match result: {step_id}")
    if stderr_path.read_text(encoding="utf-8") != expected_stderr:
        raise ValueError(f"completed checkpoint stderr does not match result: {step_id}")
    return {
        "id": step_id,
        "result": dict(result),
        "result_fingerprint": _result_fingerprint(result),
        "stdout_sha256": _sha256_file(stdout_path),
        "stderr_sha256": _sha256_file(stderr_path),
    }


def validate_completed_steps(
    evidence_dir: Path,
    *,
    checkpoint: dict[str, Any],
    selected_gate_ids: list[str],
) -> list[dict[str, Any]]:
    """Validate the durable resume frontier and return its exact result prefix."""
    raw_records = checkpoint.get("completed_steps")
    if not isinstance(raw_records, list):
        raise ValueError("quality-loop checkpoint completed_steps must be a list")
    if len(raw_records) > len(selected_gate_ids):
        raise ValueError("quality-loop checkpoint contains more steps than the selected closure")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise ValueError("quality-loop checkpoint step record must be an object")
        step_id = str(raw.get("id") or "")
        expected_id = str(selected_gate_ids[index])
        if step_id != expected_id:
            raise ValueError(
                "quality-loop checkpoint completed steps must be an exact selected-gate prefix"
            )
        if step_id in seen:
            raise ValueError(f"quality-loop checkpoint contains duplicate step: {step_id}")
        seen.add(step_id)
        result = raw.get("result")
        if not isinstance(result, dict) or str(result.get("id") or "") != step_id:
            raise ValueError(f"quality-loop checkpoint result identity mismatch: {step_id}")
        if str(raw.get("result_fingerprint") or "") != _result_fingerprint(result):
            raise ValueError(f"quality-loop checkpoint result fingerprint mismatch: {step_id}")
        steps_dir = Path(evidence_dir) / "steps"
        stdout_path = steps_dir / f"{step_id}.stdout.txt"
        stderr_path = steps_dir / f"{step_id}.stderr.txt"
        if not stdout_path.is_file() or not stderr_path.is_file():
            raise ValueError(f"quality-loop checkpoint evidence missing: {step_id}")
        if str(raw.get("stdout_sha256") or "") != _sha256_file(stdout_path):
            raise ValueError(f"quality-loop checkpoint stdout hash mismatch: {step_id}")
        if str(raw.get("stderr_sha256") or "") != _sha256_file(stderr_path):
            raise ValueError(f"quality-loop checkpoint stderr hash mismatch: {step_id}")
        if stdout_path.read_text(encoding="utf-8") != str(result.get("stdout") or ""):
            raise ValueError(f"quality-loop checkpoint stdout/result mismatch: {step_id}")
        if stderr_path.read_text(encoding="utf-8") != str(result.get("stderr") or ""):
            raise ValueError(f"quality-loop checkpoint stderr/result mismatch: {step_id}")
        results.append(dict(result))
    return results


def write_active_checkpoint(
    state_dir: Path,
    *,
    target_id: str,
    target_identity: dict[str, Any],
    policy_fingerprint: str,
    workspace_start_fingerprint: str,
    mode: str,
    selected_gate_ids: list[str],
    evidence_dir: Path,
    run_token: str,
    current_gate_id: str | None,
    completed_steps: list[dict[str, Any]],
    updated_at: str,
) -> Path:
    """Persist the exact same-run resume frontier after an atomic gate step."""
    path = active_checkpoint_path(state_dir, target_id=target_id)
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "status": "running",
        "run_token": str(run_token),
        "target_identity": target_identity,
        "policy_fingerprint": str(policy_fingerprint),
        "workspace_start_fingerprint": str(workspace_start_fingerprint),
        "mode": str(mode),
        "selected_gate_ids": [str(value) for value in selected_gate_ids],
        "evidence_dir": str(Path(evidence_dir).absolute()),
        "current_gate_id": str(current_gate_id) if current_gate_id else None,
        "completed_steps": [dict(item) for item in completed_steps],
        "updated_at": str(updated_at),
    }
    _atomic_write_json(path, payload)
    return path


def clear_active_checkpoint(
    state_dir: Path,
    *,
    target_id: str,
    run_token: str,
) -> bool:
    """Delete only the checkpoint owned by the completing run."""
    path = active_checkpoint_path(state_dir, target_id=target_id)
    if not path.is_file():
        return False
    record = _load_json(path)
    if str(record.get("run_token") or "") != str(run_token):
        return False
    path.unlink()
    return True
