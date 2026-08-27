from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage_evidence_receipt import (  # type: ignore
    STAGE_EVIDENCE_RECEIPT_SCHEMA,
    StageEvidenceReceiptError,
    build_stage_evidence_receipt,
    canonical_json_bytes,
    load_stage_evidence_receipt,
    receipt_digest,
    validate_stage_evidence_receipt,
    write_stage_evidence_receipt,
)


class StageEvidenceReceiptTests(unittest.TestCase):
    @staticmethod
    def receipt() -> dict[str, object]:
        return build_stage_evidence_receipt(
            stage_id="STAGE-2B1-P3",
            accepted_state_id="accepted-state-17",
            product_source_ref="git-commit-sha1:" + "a" * 40,
            protected_snapshot_digest="sha256:" + "b" * 64,
            control_plane_ref="git-commit-sha1:" + "c" * 40,
            execution_repo_ref="toctionyan/fristTest@main",
            workflow_run_attempt={"run_id": 2185, "attempt": 1},
            artifact={"id": "artifact-8842", "digest": "sha256:" + "d" * 64},
            result="PASS",
            producer="github-actions",
            policy="stage2b1-p3-evidence-receipt@1",
        )

    def test_build_is_canonical_and_digest_covers_every_other_field(self) -> None:
        payload = self.receipt()
        self.assertEqual(payload["schema"], STAGE_EVIDENCE_RECEIPT_SCHEMA)
        encoded = canonical_json_bytes(payload)
        self.assertFalse(encoded.endswith(b"\n"))
        self.assertEqual(
            payload["receipt_digest"],
            "sha256:" + hashlib.sha256(canonical_json_bytes({k: v for k, v in payload.items() if k != "receipt_digest"})).hexdigest(),
        )
        self.assertEqual(validate_stage_evidence_receipt(payload), payload)

    def test_write_and_load_preserve_exact_canonical_bytes(self) -> None:
        payload = self.receipt()
        with tempfile.TemporaryDirectory() as temporary:
            path = write_stage_evidence_receipt(Path(temporary) / "receipt.json", payload)
            self.assertEqual(path.read_bytes(), canonical_json_bytes(payload))
            self.assertEqual(load_stage_evidence_receipt(path), payload)

    def test_tampering_missing_unknown_and_nested_shape_fail_closed(self) -> None:
        payload = self.receipt()

        tampered = dict(payload)
        tampered["result"] = "PASS "
        with self.assertRaisesRegex(StageEvidenceReceiptError, "receipt_digest_mismatch"):
            validate_stage_evidence_receipt(tampered)

        missing = dict(payload)
        del missing["policy"]
        with self.assertRaisesRegex(StageEvidenceReceiptError, "receipt_missing:policy"):
            validate_stage_evidence_receipt(missing)

        unknown = dict(payload)
        unknown["unexpected"] = True
        with self.assertRaisesRegex(StageEvidenceReceiptError, "receipt_unknown:unexpected"):
            validate_stage_evidence_receipt(unknown)

        nested = dict(payload)
        nested["artifact"] = {"id": "artifact-8842", "digest": "sha256:" + "d" * 64, "url": "https://example.invalid"}
        nested["receipt_digest"] = receipt_digest(nested)
        with self.assertRaisesRegex(StageEvidenceReceiptError, "artifact_unknown:url"):
            validate_stage_evidence_receipt(nested)

    def test_result_run_attempt_and_artifact_are_strict(self) -> None:
        invalid_result = dict(self.receipt())
        invalid_result["result"] = "SKIPPED"
        invalid_result["receipt_digest"] = receipt_digest(invalid_result)
        with self.assertRaisesRegex(StageEvidenceReceiptError, "result_invalid"):
            validate_stage_evidence_receipt(invalid_result)

        invalid_run = dict(self.receipt())
        invalid_run["workflow_run_attempt"] = {"run_id": True, "attempt": 0}
        invalid_run["receipt_digest"] = receipt_digest(invalid_run)
        with self.assertRaisesRegex(StageEvidenceReceiptError, "workflow_run_attempt_run_id_invalid"):
            validate_stage_evidence_receipt(invalid_run)

        invalid_artifact = dict(self.receipt())
        invalid_artifact["artifact"] = {"id": "artifact-8842", "digest": "sha256:bad"}
        invalid_artifact["receipt_digest"] = receipt_digest(invalid_artifact)
        with self.assertRaisesRegex(StageEvidenceReceiptError, "artifact_digest_invalid"):
            validate_stage_evidence_receipt(invalid_artifact)

    def test_canonical_json_uses_utf8_and_rejects_non_finite_numbers(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"producer": "测试"}),
            '{"producer":"测试"}'.encode("utf-8"),
        )
        with self.assertRaises(StageEvidenceReceiptError):
            canonical_json_bytes({"value": float("nan")})


if __name__ == "__main__":
    unittest.main()
