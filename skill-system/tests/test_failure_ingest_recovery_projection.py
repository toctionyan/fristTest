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

    def test_baseline_human_gate_prepares_exact_read_only_decision_evidence(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for token in (
            "steps.ingest.outputs.classification == 'protected_baseline_drift'",
            "control/skill-system/controller/baseline_acceptance_proposal.py",
            "--workspace candidate",
            '--candidate-sha "${SOURCE_SHA}"',
            "--output incoming/baseline-acceptance-proposal.json",
            "--summary-output incoming/baseline-acceptance-proposal.txt",
            'product-source-baseline-acceptance-proposal@1',
            '.baseline_write_allowed == false',
            '.source_write_allowed == false',
            '.oracle_write_allowed == false',
            "incoming/baseline-acceptance-proposal.json",
            "incoming/baseline-acceptance-proposal.txt",
            "Read-only baseline acceptance proposal",
            "Exact drift counts",
            "Exact added/modified/deleted path lists",
            "Baseline write authority: \\`false\\`",
        ):
            self.assertIn(token, text)

    def test_baseline_proposal_executes_only_trusted_control_script_against_candidate_data(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        section = text.split("- name: Prepare read-only baseline acceptance proposal", 1)[1].split(
            "- name: Upload governed failure evidence", 1
        )[0]
        self.assertIn("control/skill-system/controller/baseline_acceptance_proposal.py", section)
        self.assertIn("--workspace candidate", section)
        self.assertNotIn("candidate/skill-system/controller/baseline_acceptance_proposal.py", section)
        self.assertNotIn("python candidate/", section)
        self.assertNotIn("uv run", section)
        self.assertNotIn("pip install", section)
        self.assertNotIn("git commit", section)
        self.assertNotIn("git push", section)
        self.assertNotIn("gh api --method", section)

    def test_baseline_decision_preparation_never_becomes_automatic_acceptance(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        baseline_section = text.split("Read-only baseline acceptance proposal", 1)[1]
        for forbidden in (
            "baseline_write_allowed == true",
            "oracle_write_allowed == true",
            "source_write_allowed == true",
            "AUTO_ACCEPT_BASELINE",
            "PROMOTE_BASELINE_AUTOMATICALLY",
        ):
            self.assertNotIn(forbidden, baseline_section)
        self.assertIn(
            "The proposal is decision preparation only. It does not accept the candidate snapshot or change the baseline.",
            text,
        )


if __name__ == "__main__":
    unittest.main()
