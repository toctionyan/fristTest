from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTHORIZE = ROOT / ".github" / "workflows" / "engineering-autonomy-authorize.yml"
WAKEUP = ROOT / ".github" / "workflows" / "engineering-autonomy-wakeup.yml"
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


class EngineeringAutonomyWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.authorize = AUTHORIZE.read_text(encoding="utf-8")
        cls.wakeup = WAKEUP.read_text(encoding="utf-8")
        cls.stage2 = STAGE2.read_text(encoding="utf-8")

    def test_authorization_is_owner_workflow_dispatch_and_network_free(self) -> None:
        self.assertIn("name: engineering-autonomy-authorize", self.authorize)
        self.assertIn("workflow_dispatch:", self.authorize)
        self.assertIn("github.actor == github.repository_owner", self.authorize)
        self.assertIn("ref: main", self.authorize)
        self.assertIn("persist-credentials: false", self.authorize)
        self.assertIn("github_repair_engineering_autonomy_authorize.py", self.authorize)
        self.assertIn("Network execution in this workflow:", self.authorize)
        self.assertNotIn("actions: write", self.authorize)
        self.assertNotIn("gh workflow run", self.authorize)
        self.assertNotIn("gh run rerun", self.authorize)
        self.assertNotIn("remote_repair_approval", self.authorize)

    def test_wakeup_consumes_only_completed_trusted_authorization(self) -> None:
        self.assertIn("name: engineering-autonomy-wakeup", self.wakeup)
        self.assertIn("workflows:\n      - engineering-autonomy-authorize", self.wakeup)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.wakeup)
        self.assertIn("github.event.workflow_run.event == 'workflow_dispatch'", self.wakeup)
        self.assertIn(
            "github.event.workflow_run.actor.login == github.repository_owner",
            self.wakeup,
        )
        self.assertIn("ref: ${{ github.event.workflow_run.head_sha }}", self.wakeup)
        self.assertIn("actions: write", self.wakeup)
        self.assertIn("statuses: write", self.wakeup)
        self.assertIn("github_repair_engineering_autonomy_wakeup.py verify", self.wakeup)
        self.assertIn("gh workflow run governed-ci-repair-stage2.yml", self.wakeup)
        self.assertIn('gh run rerun "${SOURCE_RUN_ID}"', self.wakeup)
        self.assertNotIn("remote_repair_approval", self.wakeup)
        self.assertNotIn("gh pr merge", self.wakeup)
        self.assertNotIn("gh api -X PUT", self.wakeup)

    def test_wakeup_binds_exact_authorize_attempt_artifact(self) -> None:
        self.assertIn("Resolve and download exact authorize-attempt handoff artifact", self.wakeup)
        self.assertIn(".run_attempt | tostring", self.wakeup)
        self.assertIn(".created_at >= $started", self.wakeup)
        self.assertIn('select(.name | startswith("engineering-autonomy-handoff-"))', self.wakeup)
        self.assertIn('[[ "${count}" == "1" ]]', self.wakeup)

    def test_wakeup_has_durable_idempotency_and_failure_receipts(self) -> None:
        self.assertIn("engineering-autonomy-dispatch/${DECISION_ID}", self.wakeup)
        self.assertIn("already_dispatched=true", self.wakeup)
        self.assertIn("--status DISPATCHED", self.wakeup)
        self.assertIn("--status FAILED", self.wakeup)
        self.assertIn("NETWORK_ACTION_FAILED", self.wakeup)
        self.assertIn("Preserve unsafe or failed network state as workflow failure", self.wakeup)
        self.assertIn("if-no-files-found: warn", self.wakeup)

    def test_wakeup_reservation_is_at_most_once_across_crash_window(self) -> None:
        # A network request may be accepted immediately before the wakeup process dies.
        # The exact decision marker must therefore be a durable reservation, not merely
        # a success cache: any pre-existing PENDING marker is uncertain and must stop
        # fail-closed rather than issuing the same network request again.
        self.assertIn('CONTEXT="engineering-autonomy-dispatch/${DECISION_ID}"', self.wakeup)
        self.assertNotIn("engineering-autonomy-dispatch/${DECISION_ID:0:16}", self.wakeup)
        self.assertIn("reservation_state=UNCERTAIN", self.wakeup)
        self.assertIn("reservation_state=DISPATCHED", self.wakeup)
        self.assertIn("reservation_state=FAILED", self.wakeup)
        self.assertIn("reservation_state=NEW", self.wakeup)
        self.assertIn(
            "if: steps.idempotency.outputs.reservation_state == 'NEW'",
            self.wakeup,
        )
        self.assertIn("DISPATCH_OUTCOME_UNCERTAIN", self.wakeup)
        self.assertIn("PREVIOUS_NETWORK_FAILURE", self.wakeup)
        self.assertIn('if [[ "${RESERVATION_STATE}" != "NEW" ]]', self.wakeup)

    def test_wakeup_reservation_check_is_serialized_across_authorization_runs(self) -> None:
        # GitHub commit statuses are not compare-and-swap. Two successful owner
        # authorization runs for the same decision could otherwise both observe no
        # marker and both issue the network request. Serialize the short wakeup
        # adapter per repository so reservation inspection + request + receipt is one
        # fail-closed critical section across duplicate authorization deliveries.
        self.assertIn(
            "group: engineering-autonomy-wakeup-${{ github.repository }}",
            self.wakeup,
        )
        self.assertNotIn(
            "group: engineering-autonomy-wakeup-${{ github.event.workflow_run.id }}-${{ github.event.workflow_run.run_attempt }}",
            self.wakeup,
        )
        self.assertIn("cancel-in-progress: false", self.wakeup)

    def test_stage2_keeps_manual_and_autonomy_entry_paths_mutually_exclusive(self) -> None:
        for field in (
            "autonomy_handoff_run_id:",
            "autonomy_handoff_run_attempt:",
            "autonomy_authorization_id:",
            "autonomy_authorization_sha256:",
            "autonomy_grant_id:",
            "autonomy_grant_sha256:",
            "autonomy_plan_sha256:",
        ):
            self.assertIn(field, self.stage2)
        self.assertIn(
            "Autonomy Stage-2 handoff must not synthesize or combine legacy manual approval.",
            self.stage2,
        )
        self.assertIn(
            'if [[ "${REMOTE_REPAIR_APPROVAL}" != "explicitly-approved" ]]',
            self.stage2,
        )
        self.assertIn('echo "input_kind=autonomy_stage1"', self.stage2)
        self.assertIn("github_repair_autonomy_stage2.py", self.stage2)
        self.assertIn('.name == "engineering-autonomy-authorize"', self.stage2)

    def test_stage2_autonomy_path_is_bound_to_exact_authorized_control_sha(self) -> None:
        # Authorization and Stage-2 execution must use one exact trusted control-plane
        # revision. If main advances between them, the autonomous write path stops
        # instead of silently executing a newer Stage-2 controller under an older grant.
        self.assertIn("STAGE2_CONTROL_SHA: ${{ steps.control.outputs.control_sha }}", self.stage2)
        self.assertIn("--stage2-control-sha \"${STAGE2_CONTROL_SHA}\"", self.stage2)
        self.assertIn(
            "Stage-2 trusted control SHA differs from the owner-authorized control SHA",
            self.stage2,
        )

    def test_stage2_protected_environment_and_repair_owner_are_not_duplicated(self) -> None:
        self.assertEqual(self.stage2.count("\n  repair:\n"), 1)
        self.assertIn("environment: production-certification", self.stage2)
        self.assertIn("Run bounded restricted remote fallback repair controller", self.stage2)
        self.assertIn("--max-cycles 8", self.stage2)
        self.assertIn('MAX_REPAIR_ROUNDS: ${{ needs.inspect.outputs.max_repair_rounds }}', self.stage2)
        self.assertIn('--max-repair-rounds "${MAX_REPAIR_ROUNDS}"', self.stage2)
        self.assertNotIn("--max-repair-rounds 8", self.stage2)
        self.assertNotIn("environment: unprotected", self.stage2)
        self.assertNotIn("production_closed: true", self.stage2)


if __name__ == "__main__":
    unittest.main()
