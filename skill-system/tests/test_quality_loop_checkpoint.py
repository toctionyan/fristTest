from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


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


def _write(module, root: Path, *, token: str = "run-a") -> Path:
    return module.write_active_checkpoint(
        root,
        target_id="target-1",
        target_identity=_identity(),
        policy_fingerprint="policy-fp",
        workspace_start_fingerprint="workspace-fp",
        mode="quick",
        selected_gate_ids=["gate-a", "gate-b"],
        evidence_dir=root / "evidence",
        run_token=token,
        current_gate_id="gate-b",
        completed_steps=[{"id": "gate-a", "status": "PASS"}],
        updated_at="2026-08-10T16:00:00+08:00",
    )


def _load(module, root: Path, **overrides):
    kwargs = {
        "target_id": "target-1",
        "target_identity": _identity(),
        "policy_fingerprint": "policy-fp",
        "workspace_start_fingerprint": "workspace-fp",
        "mode": "quick",
        "selected_gate_ids": ["gate-a", "gate-b"],
        "evidence_dir": root / "evidence",
    }
    kwargs.update(overrides)
    return module.load_compatible_active_checkpoint(root, **kwargs)


class QualityLoopCheckpointTests(unittest.TestCase):
    def test_exact_same_run_frontier_is_resumable(self) -> None:
        module = _load_checkpoint_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(module, root)
            record = _load(module, root)

            self.assertTrue(path.is_file())
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["run_token"], "run-a")
            self.assertEqual(record["current_gate_id"], "gate-b")
            self.assertEqual(record["completed_steps"], [{"id": "gate-a", "status": "PASS"}])

    def test_changed_run_authority_never_reuses_checkpoint(self) -> None:
        module = _load_checkpoint_module()
        cases = [
            ("target_identity", {"id": "target-1", "fingerprint": "changed"}),
            ("policy_fingerprint", "changed-policy"),
            ("workspace_start_fingerprint", "changed-workspace"),
            ("mode", "integration"),
            ("selected_gate_ids", ["gate-a", "gate-c"]),
        ]
        for field, value in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _write(module, root)
                self.assertIsNone(_load(module, root, **{field: value}))

    def test_changed_evidence_directory_never_reuses_checkpoint(self) -> None:
        module = _load_checkpoint_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(module, root)
            self.assertIsNone(_load(module, root, evidence_dir=root / "other-evidence"))

    def test_only_checkpoint_owner_can_clear_active_frontier(self) -> None:
        module = _load_checkpoint_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(module, root, token="owner-token")

            self.assertFalse(
                module.clear_active_checkpoint(
                    root,
                    target_id="target-1",
                    run_token="different-token",
                )
            )
            self.assertTrue(path.is_file())
            self.assertTrue(
                module.clear_active_checkpoint(
                    root,
                    target_id="target-1",
                    run_token="owner-token",
                )
            )
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
