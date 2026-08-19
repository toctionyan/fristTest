from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import bind_autonomy_grant, create_autonomy_grant, revoke_autonomy_grant  # noqa: E402
from engineering_task_controller import (  # noqa: E402
    CIObservation,
    EngineeringReconcileError,
    reconcile_ci_terminal,
)
from local_first_governance import (  # noqa: E402
    LOCAL_GATE_ORDER,
    begin_local_repair_round,
    bind_ci_run,
    create_local_first_task,
    local_metadata,
    record_local_gate,
    upload_admission,
)


REPOSITORY = "toctionyan/fristTest"
BASE_SHA = "a" * 40
CANDIDATE_SHA = "b" * 40
BRANCH = "agent/m3-controller-test"


class EngineeringTaskControllerTests(unittest.TestCase):
    def _ready_store(self, root: Path):
        store = create_local_first_task(
            root / "task-run.json",
            task_id="m3-controller-test",
            change_id="change-m3-controller-test",
            base_sha=BASE_SHA,
            branch=BRANCH,
            patch_owner="product-implementer",
            allowed_paths=["services/agent-service/app/runtime.py"],
            target_fingerprint="target-m3",
        )
        begin_local_repair_round(store, workspace_fingerprint="workspace-green")
        for gate in LOCAL_GATE_ORDER:
            record_local_gate(
                store,
                gate=gate,
                passed=True,
                evidence_refs=[f"local:{gate}"],
                workspace_fingerprint="workspace-green",
            )
        admission = upload_admission(
            store,
            changed_paths=["services/agent-service/app/runtime.py"],
            candidate_head_sha=CANDIDATE_SHA,
            workspace_fingerprint="workspace-green",
            evidence_refs=["upload:admitted"],
        )
        self.assertTrue(admission.allowed)
        bind_ci_run(
            store,
            run_id=9001,
            run_attempt=1,
            head_sha=CANDIDATE_SHA,
            evidence_refs=["github:run:9001"],
        )
        return store

    def _grant(self, store):
        grant = create_autonomy_grant(
            task=store.payload,
            repository=REPOSITORY,
            branch=BRANCH,
            base_sha=BASE_SHA,
            issued_by="repository-owner",
            allowed_actions=[
                "analyze_failure",
                "edit_authorized_source",
                "add_authorized_counterexample_tests",
                "commit_current_branch",
                "push_current_branch",
                "dispatch_ci",
                "retry_transient_ci",
                "repair_meaningful_product_red",
                "advance_verified_milestone",
            ],
        )
        bind_autonomy_grant(
            store,
            grant,
            repository=REPOSITORY,
            owner_authorization_ref="github-owner-ack:m3",
        )
        return grant

    def _observation(
        self,
        *,
        conclusion: str = "failure",
        job_name: str = "quality-quick-execution",
        log_text: str = "AssertionError: expected READY, got BLOCKED",
        head_sha: str = CANDIDATE_SHA,
        run_id: int = 9001,
        run_attempt: int = 1,
    ) -> CIObservation:
        return CIObservation(
            run_id=run_id,
            run_attempt=run_attempt,
            head_sha=head_sha,
            conclusion=conclusion,
            job_name=job_name,
            log_text=log_text,
            evidence_refs=(f"github:run:{run_id}:attempt:{run_attempt}",),
        )

    def test_meaningful_product_red_can_return_bounded_repair_action(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            result = reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(),
                product_verdict="FAIL",
                transport_verdict="FAIL",
                authority_context={
                    "underlying_write_authority": True,
                    "exact_write_scope": True,
                    "current_head_sha": CANDIDATE_SHA,
                },
            )
            self.assertEqual(result["decision"], "REPAIR_PRODUCT")
            self.assertEqual(result["action"], "repair_meaningful_product_red")
            self.assertTrue(result["allowed"])
            self.assertFalse(result["human_required"])
            self.assertEqual(result["failure_class"], "PRODUCT_SOURCE_FAILURE")
            self.assertFalse(result["merge_allowed"])
            self.assertFalse(result["deploy_allowed"])
            self.assertFalse(result["production_closed"])

    def test_product_red_cannot_gain_write_authority_from_autonomy_grant(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            result = reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(),
                product_verdict="FAIL",
                transport_verdict="FAIL",
                authority_context={
                    "underlying_write_authority": False,
                    "exact_write_scope": True,
                    "current_head_sha": CANDIDATE_SHA,
                },
            )
            self.assertEqual(result["decision"], "STOP_REPAIR_AUTHORITY")
            self.assertFalse(result["allowed"])
            self.assertTrue(result["human_required"])

    def test_transport_only_failure_never_becomes_product_red(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            result = reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(
                    job_name="quality-quick-required-status",
                    log_text="stable status publication provenance mismatch",
                ),
                product_verdict="PASS",
                transport_verdict="FAIL",
                authority_context={"current_head_sha": CANDIDATE_SHA},
            )
            self.assertEqual(result["decision"], "STOP_TRANSPORT_FAILURE")
            self.assertIsNone(result["action"])
            self.assertFalse(result["product_write_allowed"])
            self.assertEqual(local_metadata(store)["counters"]["ci_feedback_rounds"], 0)

    def test_transient_failure_retries_same_candidate_without_product_write(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            result = reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(
                    job_name="quality-quick-execution",
                    log_text="runner lost communication while uploading evidence",
                ),
                product_verdict="FAIL",
                transport_verdict="FAIL",
                authority_context={"current_head_sha": CANDIDATE_SHA},
            )
            self.assertEqual(result["decision"], "RETRY_CI")
            self.assertEqual(result["action"], "retry_transient_ci")
            self.assertTrue(result["allowed"])
            self.assertFalse(result["product_write_allowed"])

    def test_stale_current_head_blocks_retry_or_repair(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            with self.assertRaises(EngineeringReconcileError):
                reconcile_ci_terminal(
                    store,
                    grant,
                    repository=REPOSITORY,
                    observation=self._observation(),
                    product_verdict="FAIL",
                    transport_verdict="FAIL",
                    authority_context={
                        "underlying_write_authority": True,
                        "exact_write_scope": True,
                        "current_head_sha": "c" * 40,
                    },
                )

    def test_wrong_run_or_head_is_rejected_before_mutating_feedback(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            with self.assertRaises(EngineeringReconcileError):
                reconcile_ci_terminal(
                    store,
                    grant,
                    repository=REPOSITORY,
                    observation=self._observation(run_id=9002),
                    product_verdict="FAIL",
                    transport_verdict="FAIL",
                    authority_context={"current_head_sha": CANDIDATE_SHA},
                )
            self.assertEqual(local_metadata(store)["counters"]["ci_feedback_rounds"], 0)

    def test_duplicate_terminal_delivery_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            kwargs = {
                "repository": REPOSITORY,
                "observation": self._observation(),
                "product_verdict": "FAIL",
                "transport_verdict": "FAIL",
                "authority_context": {
                    "underlying_write_authority": True,
                    "exact_write_scope": True,
                    "current_head_sha": CANDIDATE_SHA,
                },
            }
            first = reconcile_ci_terminal(store, grant, **kwargs)
            first_revision = store.payload["revision"]
            second = reconcile_ci_terminal(store, grant, **kwargs)
            self.assertFalse(first["duplicate"])
            self.assertTrue(second["duplicate"])
            self.assertEqual(first["decision_id"], second["decision_id"])
            self.assertEqual(store.payload["revision"], first_revision)
            self.assertEqual(local_metadata(store)["counters"]["ci_feedback_rounds"], 1)

    def test_conflicting_duplicate_for_same_run_attempt_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(),
                product_verdict="FAIL",
                transport_verdict="FAIL",
                authority_context={
                    "underlying_write_authority": True,
                    "exact_write_scope": True,
                    "current_head_sha": CANDIDATE_SHA,
                },
            )
            with self.assertRaises(EngineeringReconcileError):
                reconcile_ci_terminal(
                    store,
                    grant,
                    repository=REPOSITORY,
                    observation=self._observation(conclusion="cancelled"),
                    product_verdict="UNKNOWN",
                    transport_verdict="FAIL",
                    authority_context={"current_head_sha": CANDIDATE_SHA},
                )

    def test_test_defect_is_not_repaired_as_product_code(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            result = reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(log_text="collection error: invalid fixture definition"),
                product_verdict="FAIL",
                transport_verdict="FAIL",
                authority_context={"current_head_sha": CANDIDATE_SHA},
            )
            self.assertEqual(result["decision"], "STOP_TEST_AUTHORITY")
            self.assertFalse(result["product_write_allowed"])
            self.assertTrue(result["human_required"])

    def test_revoked_grant_stops_new_terminal_reconciliation(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            revoke_autonomy_grant(
                store,
                reason="owner stopped autonomous continuation",
                evidence_ref="github-owner-revoke:m3",
            )
            result = reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(),
                product_verdict="FAIL",
                transport_verdict="FAIL",
                authority_context={
                    "underlying_write_authority": True,
                    "exact_write_scope": True,
                    "current_head_sha": CANDIDATE_SHA,
                },
            )
            self.assertEqual(result["decision"], "STOP_AUTONOMY")
            self.assertFalse(result["allowed"])
            self.assertTrue(result["human_required"])
            self.assertEqual(local_metadata(store)["counters"]["ci_feedback_rounds"], 0)

    def test_green_terminal_ci_completes_existing_governed_task_without_new_authority(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._ready_store(Path(directory))
            grant = self._grant(store)
            result = reconcile_ci_terminal(
                store,
                grant,
                repository=REPOSITORY,
                observation=self._observation(
                    conclusion="success",
                    job_name="quality",
                    log_text="all required gates passed",
                ),
                product_verdict="PASS",
                transport_verdict="PASS",
                authority_context={"current_head_sha": CANDIDATE_SHA},
            )
            self.assertEqual(result["decision"], "COMPLETE")
            self.assertIsNone(result["action"])
            self.assertEqual(store.payload["status"], "COMPLETED")
            self.assertFalse(result["product_write_allowed"])
            self.assertFalse(result["merge_allowed"])


if __name__ == "__main__":
    unittest.main()
