from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import product_quality_bridge as bridge  # type: ignore
import product_contract_gate as contract_gate  # type: ignore
from models import ChangeContract  # type: ignore


class ProductQualityBridgeTest(unittest.TestCase):
    def contract(self, workspace: Path, fingerprint: str = "abc") -> ChangeContract:
        evidence = workspace / ".quality" / "product-code" / "change-1" / "verification-static"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "run-summary.json").write_text(json.dumps({
            "schema_version": 6,
            "run_kind": "verification",
            "decision": "PASS",
            "loop_status": "CONVERGED",
            "completion_eligible": True,
            "mode": "static",
            "target_identity": {"id": "change-1"},
        }), encoding="utf-8")
        return ChangeContract(workspace / "governance" / "active-change.json", {
            "change_id": "change-1",
            "target_kind": "repair",
            "profile": "product-repair",
            "allowed_paths": ["services/agent-service/src/example.py"],
            "minimum_quality_mode": "static",
            "product_validation": {
                "verification": ".quality/product-code/change-1/verification-static",
                "verification_mode": "static",
                "verification_source_fingerprint": fingerprint,
                "verification_source_file_count": 1,
            },
        })

    def test_current_reuses_bound_completion_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            contract = self.contract(workspace)
            with patch.object(bridge, "ROOT", workspace), \
                 patch.object(bridge, "load_contract", return_value=contract), \
                 patch.object(bridge, "source_fingerprint", return_value=("abc", 1)), \
                 patch.object(bridge, "_verify_evidence_attestation", return_value=None):
                result = bridge.current("static")
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["reused_current_evidence"])

    def test_current_rejects_source_change_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            contract = self.contract(workspace)
            with patch.object(bridge, "ROOT", workspace), \
                 patch.object(bridge, "load_contract", return_value=contract), \
                 patch.object(bridge, "source_fingerprint", return_value=("changed", 1)), \
                 patch.object(bridge, "_verify_evidence_attestation", return_value=None):
                with self.assertRaisesRegex(ValueError, "source changed"):
                    bridge.current("static")

    def test_current_uses_explicit_product_workspace_without_rebinding_controller_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw).resolve()
            contract = self.contract(workspace)
            with patch.object(bridge, "load_contract", return_value=contract) as load_contract, \
                 patch.object(bridge, "source_fingerprint", return_value=("abc", 1)) as source_fingerprint, \
                 patch.object(bridge, "_verify_evidence_attestation", return_value=None):
                result = bridge.current("static", workspace=workspace)
            self.assertEqual(result["workspace_root"], str(workspace))
            load_contract.assert_called_once_with(workspace, require_approved=False)
            source_fingerprint.assert_called_once_with(workspace, contract.allowed_paths)

    def test_product_contract_gate_uses_explicit_product_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw).resolve()
            target = workspace / "governance" / "target.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "目标 ID：change-1\n目标类型：repair\n最低质量模式：static\n允许变更路径：services/agent-service/src/example.py\n",
                encoding="utf-8",
            )
            contract = self.contract(workspace)
            contract.payload["quality_target"] = "governance/target.md"
            contract.payload["status"] = "approved"
            with patch.object(contract_gate, "load_contract", return_value=contract) as load_contract:
                errors = contract_gate.verify(workspace)
            self.assertEqual(errors, [])
            load_contract.assert_called_once_with(workspace, require_approved=False)

    def test_product_workspace_rejects_missing_external_root(self) -> None:
        missing = Path(tempfile.gettempdir()) / "c3h-a2-missing-workspace"
        if missing.exists():
            self.fail(f"test path unexpectedly exists: {missing}")
        with self.assertRaisesRegex(ValueError, "product workspace root does not exist"):
            bridge._product_workspace(missing)
        with self.assertRaisesRegex(ValueError, "product workspace root does not exist"):
            contract_gate._product_workspace(missing)


if __name__ == "__main__":
    unittest.main()
