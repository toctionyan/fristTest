from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/governed-ci-failure-ingest.yml"


class FailureIngestRecoveryProjectionTests(unittest.TestCase):
    def test_workflow_surfaces_recovery_disposition_and_human_need_separately(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for token in (
            "steps.ingest.outputs.recovery_disposition",
            "steps.ingest.outputs.human_required",
            "steps.ingest.outputs.diagnostic_allowed",
            "steps.ingest.outputs.retry_allowed",
            'AUTO_REPAIR)',
            'AUTO_RETRY)',
            'AUTO_DIAGNOSE)',
            'WAIT_EXTERNAL)',
            'HUMAN_REQUIRED)',
            'NEXT_ACTION="READ_ONLY_DIAGNOSIS_REQUIRED"',
            'NEXT_ACTION="HUMAN_DECISION_REQUIRED"',
            "A failed attempt is recorded but does not automatically become a user interruption.",
        ):
            self.assertIn(token, text)

        self.assertNotIn('NEXT_ACTION="BLOCKED_FOR_DIAGNOSIS"', text)

    def test_failure_issue_never_claims_source_write_from_diagnosis_or_retry(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        auto_diagnose = text.split('AUTO_DIAGNOSE)', 1)[1].split(';;', 1)[0]
        auto_retry = text.split('AUTO_RETRY)', 1)[1].split(';;', 1)[0]
        wait_external = text.split('WAIT_EXTERNAL)', 1)[1].split(';;', 1)[0]
        for block in (auto_diagnose, auto_retry, wait_external):
            self.assertNotIn('REMOTE_FALLBACK_ELIGIBLE="true"', block)
            self.assertNotIn('REPAIR_ALLOWED="true"', block)

    def test_summary_makes_unresolved_human_gate_visible(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Human decision required now", text)
        self.assertIn("Recovery disposition", text)
        self.assertIn("Product/source automatic write eligibility", text)
        self.assertIn("Read-only diagnosis eligibility", text)
        self.assertIn("Same-candidate retry eligibility", text)


if __name__ == "__main__":
    unittest.main()
