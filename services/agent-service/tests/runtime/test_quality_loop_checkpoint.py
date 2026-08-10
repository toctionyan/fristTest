from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _load_checkpoint_module():
    path = ROOT / "scripts" / "quality_control" / "checkpoint.py"
    spec = importlib.util.spec_from_file_location("quality_loop_checkpoint_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _identity() -> dict[str, str]:
    return {"id": "target-1", "fingerprint": "target-fp"}


def _write(module, tmp_path: Path, *, token: str = "run-a") -> Path:
    return module.write_active_checkpoint(
        tmp_path,
        target_id="target-1",
        target_identity=_identity(),
        policy_fingerprint="policy-fp",
        workspace_start_fingerprint="workspace-fp",
        mode="quick",
        selected_gate_ids=["gate-a", "gate-b"],
        evidence_dir=tmp_path / "evidence",
        run_token=token,
        current_gate_id="gate-b",
        completed_steps=[{"id": "gate-a", "status": "PASS"}],
        updated_at="2026-08-10T16:00:00+08:00",
    )


def _load(module, tmp_path: Path, **overrides):
    kwargs = {
        "target_id": "target-1",
        "target_identity": _identity(),
        "policy_fingerprint": "policy-fp",
        "workspace_start_fingerprint": "workspace-fp",
        "mode": "quick",
        "selected_gate_ids": ["gate-a", "gate-b"],
        "evidence_dir": tmp_path / "evidence",
    }
    kwargs.update(overrides)
    return module.load_compatible_active_checkpoint(tmp_path, **kwargs)


def test_exact_same_run_frontier_is_resumable(tmp_path: Path) -> None:
    module = _load_checkpoint_module()
    path = _write(module, tmp_path)

    record = _load(module, tmp_path)

    assert path.is_file()
    assert record is not None
    assert record["run_token"] == "run-a"
    assert record["current_gate_id"] == "gate-b"
    assert record["completed_steps"] == [{"id": "gate-a", "status": "PASS"}]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_identity", {"id": "target-1", "fingerprint": "changed"}),
        ("policy_fingerprint", "changed-policy"),
        ("workspace_start_fingerprint", "changed-workspace"),
        ("mode", "integration"),
        ("selected_gate_ids", ["gate-a", "gate-c"]),
    ],
)
def test_changed_run_authority_never_reuses_checkpoint(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    module = _load_checkpoint_module()
    _write(module, tmp_path)

    assert _load(module, tmp_path, **{field: value}) is None


def test_changed_evidence_directory_never_reuses_checkpoint(tmp_path: Path) -> None:
    module = _load_checkpoint_module()
    _write(module, tmp_path)

    assert _load(module, tmp_path, evidence_dir=tmp_path / "other-evidence") is None


def test_only_checkpoint_owner_can_clear_active_frontier(tmp_path: Path) -> None:
    module = _load_checkpoint_module()
    path = _write(module, tmp_path, token="owner-token")

    assert module.clear_active_checkpoint(
        tmp_path,
        target_id="target-1",
        run_token="different-token",
    ) is False
    assert path.is_file()
    assert module.clear_active_checkpoint(
        tmp_path,
        target_id="target-1",
        run_token="owner-token",
    ) is True
    assert not path.exists()
