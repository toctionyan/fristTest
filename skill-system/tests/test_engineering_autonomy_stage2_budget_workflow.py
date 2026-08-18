from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


class EngineeringAutonomyStage2BudgetWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_verified_autonomy_budget_is_exposed_to_repair_job(self) -> None:
        for marker in (
            "max_repair_rounds: ${{ steps.inspect.outputs.max_repair_rounds }}",
            "max_validation_retries: ${{ steps.inspect.outputs.max_validation_retries }}",
            "autonomy_continuation_sha256: ${{ steps.inspect.outputs.autonomy_continuation_sha256 }}",
            "autonomy_grant_id: ${{ steps.inspect.outputs.grant_id }}",
            "autonomy_authorization_id: ${{ steps.inspect.outputs.authorization_id }}",
        ):
            self.assertIn(marker, self.workflow)

    def test_repair_controller_uses_authorized_budget_not_global_eight(self) -> None:
        self.assertIn('MAX_REPAIR_ROUNDS: ${{ needs.inspect.outputs.max_repair_rounds }}', self.workflow)
        self.assertIn('MAX_VALIDATION_RETRIES: ${{ needs.inspect.outputs.max_validation_retries }}', self.workflow)
        self.assertIn('--max-repair-rounds "${MAX_REPAIR_ROUNDS}"', self.workflow)
        self.assertNotIn("--max-repair-rounds 8", self.workflow)

    def test_stage2_handoff_persists_exact_autonomy_continuation(self) -> None:
        for marker in (
            '--autonomy-continuation-sha256 "${AUTONOMY_CONTINUATION_SHA256}"',
            '--autonomy-grant-id "${AUTONOMY_GRANT_ID}"',
            '--autonomy-grant-sha256 "${AUTONOMY_GRANT_SHA256}"',
            '--autonomy-authorization-id "${AUTONOMY_AUTHORIZATION_ID}"',
            '--autonomy-authorization-sha256 "${AUTONOMY_AUTHORIZATION_SHA256}"',
            '--max-validation-retries "${MAX_VALIDATION_RETRIES}"',
        ):
            self.assertIn(marker, self.workflow)

    def test_loop_feedback_revalidates_and_cannot_expand_or_drop_envelope(self) -> None:
        self.assertIn("validate_autonomy_continuation", self.workflow)
        self.assertIn("outer-loop autonomy continuation digest mismatch", self.workflow)
        self.assertIn("outer-loop repair budget drifted from autonomy continuation", self.workflow)
        self.assertIn("outer-loop validation retry budget drifted from autonomy continuation", self.workflow)
        self.assertIn("legacy outer-loop feedback unexpectedly carries autonomy continuation identity", self.workflow)

    def test_manual_fallback_remains_bounded_and_protected(self) -> None:
        self.assertIn('handle.write("max_repair_rounds=8\\n")', self.workflow)
        self.assertIn('handle.write("max_validation_retries=3\\n")', self.workflow)
        self.assertIn("environment: production-certification", self.workflow)
        self.assertNotIn("production_closed: true", self.workflow)
        self.assertNotIn("gh pr merge", self.workflow)


if __name__ == "__main__":
    unittest.main()
