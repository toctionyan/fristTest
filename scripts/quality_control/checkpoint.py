from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA_VERSION = 1


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


def new_run_token() -> str:
    return secrets.token_hex(16)


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
