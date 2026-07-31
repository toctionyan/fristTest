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


if __name__ == "__main__":
    unittest.main()
