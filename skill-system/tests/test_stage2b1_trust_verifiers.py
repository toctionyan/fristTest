from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


CONTROLLER = Path(__file__).resolve().parents[1] / "controller"
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from stage2b1_provenance import (  # noqa: E402
    PROVENANCE_OBSERVATION_SCHEMA,
    Stage2B1ProvenanceError,
    verify_artifact_provenance,
)


class Stage2B1TrustVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provenance = {
            "schema": PROVENANCE_OBSERVATION_SCHEMA,
            "repository": "toctionyan/fristTest",
            "workflow_path": ".github/workflows/source.yml",
            "workflow_id": 101,
            "event": "pull_request",
            "ref": "refs/pull/1/merge",
            "head_sha": "e" * 40,
            "run_id": 901,
            "run_attempt": 2,
            "artifact": {
                "id": "1001",
                "name": "stage2b1-acceptance-decision",
                "digest": "sha256:" + "f" * 64,
                "archive_digest": "sha256:" + "f" * 64,
                "content_digest": "sha256:" + "1" * 64,
                "source_run_id": 901,
                "source_run_attempt": 2,
            },
        }

    def _expected(self) -> dict[str, object]:
        artifact = self.provenance["artifact"]
        return {
            "receipt_id": "1001",
            "artifact_id": "1001",
            "artifact_name": "stage2b1-acceptance-decision",
            "artifact_digest": artifact["digest"],
            "content_digest": artifact["content_digest"],
            "repository": "toctionyan/fristTest",
            "workflow_path": ".github/workflows/source.yml",
            "workflow_id": 101,
            "event": "pull_request",
            "ref": "refs/pull/1/merge",
            "head_sha": "e" * 40,
            "run_id": 901,
            "run_attempt": 2,
        }

    def test_provenance_requires_exact_expected_identity(self) -> None:
        verified = verify_artifact_provenance(self.provenance, expected=self._expected())
        self.assertEqual(verified.artifact_id, "1001")
        self.assertTrue(verified.proof_ref.startswith("provenance:sha256:"))

        changed = copy.deepcopy(self.provenance)
        changed["artifact"]["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(Stage2B1ProvenanceError):
            verify_artifact_provenance(changed, expected=self._expected())

    def test_provenance_rejects_unknown_fields_and_run_mismatch(self) -> None:
        unknown = copy.deepcopy(self.provenance)
        unknown["latest"] = True
        with self.assertRaises(Stage2B1ProvenanceError):
            verify_artifact_provenance(unknown, expected=self._expected())
        mismatch = copy.deepcopy(self.provenance)
        mismatch["artifact"]["source_run_attempt"] = 1
        with self.assertRaises(Stage2B1ProvenanceError):
            verify_artifact_provenance(mismatch, expected=self._expected())


if __name__ == "__main__":
    unittest.main()
