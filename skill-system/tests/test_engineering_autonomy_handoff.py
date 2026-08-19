from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import bind_autonomy_grant, create_autonomy_grant  # noqa: E402
from engineering_autonomy_dispatch import AutonomyDispatchError  # noqa: E402
from engineering_autonomy_handoff import (  # noqa: E402
    HANDOFF_BUNDLE_SCHEMA,
    compile_trusted_handoff,
    validate_handoff_bundle,
)
from engineering_task_controller import CIObservation, reconcile_ci_terminal  # noqa: E402
from local_first_governance import (  # noqa: E402
    LOCAL_GATE_ORDER,
    begin_local_repair_round,
    bind_ci_run,
    create_local_first_task,
    record_local_gate,
    upload_admission,
)


REPOSITORY = "toctionyan/fristTest"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
BRANCH = "agent/m32b-handoff-test"
SOURCE_RUN_ID = 9501
SOURCE_RUN_ATTEMPT = 1
SOURCE_PR_NUMBER = 1808
CONTROL_SHA = "c" * 40
TRUSTED_WORKFLOW_REF = (
    ".github/workflows/engineering-autonomy-authorize.yml@" + CONTROL_SHA
)


class EngineeringAutonomyHandoffTests(unittest.TestCase):
    def _ready_store(self, root: Path):
        store = create_local_first_task(
            root / "task-run.json",
            task_id="m32b-handoff-test",
            change_id="change-m32b-handoff-test",
            base_sha=BASE_SHA,
            branch=BRANCH,
            patch_owner="product-implementer",
            allowed_paths=["services/agent-service/app/runtime.py"],
            target_fingerprint="target-m32b-handoff",
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
            candidate_head_sha=HEAD_SHA,
            workspace_fingerprint="workspace-green",
            evidence_refs=["upload:admitted"],
        )
        self.assertTrue(admission.allowed)
        bind_ci_run(
            store,
            run_id=SOURCE_RUN_ID,
            run_attempt=SOURCE_RUN_ATTEMPT,
            head_sha=HEAD_SHA,
            evidence_refs=[f"github:run:{SOURCE_RUN_ID}"],
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
            owner_authorization_ref="github-owner-ack:m32b",
        )
        return grant

    def _observation(self, *, log_text: str = "AssertionError: expected READY, got BLOCKED"):
        return CIObservation(
            run_id=SOURCE_RUN_ID,
            run_attempt=SOURCE_RUN_ATTEMPT,
            head_sha=HEAD_SHA,
            conclusion="failure",
            job_name="quality-quick-execution",
            log_text=log_text,
            evidence_refs=(f"github:run:{SOURCE_RUN_ID}:attempt:1",),
        )

    def _repair_bundle(self, root: Path):
        store = self._ready_store(root)
        grant = self._grant(store)
        outcome = reconcile_ci_terminal(
            store,
            grant,
            repository=REPOSITORY,
            observation=self._observation(),
            product_verdict="FAIL",
            transport_verdict="FAIL",
            authority_context={
                "underlying_write_authority": True,
                "exact_write_scope": True,
                "current_head_sha": HEAD_SHA,
            },
        )
        self.assertEqual(outcome["decision"], "REPAIR_PRODUCT")
        return {
            "schema": HANDOFF_BUNDLE_SCHEMA,
            "task": copy.deepcopy(store.payload),
            "grant": copy.deepcopy(grant),
            "reconcile_outcome": copy.deepcopy(outcome),
            "failure_signature": "quality:meaningful-product-red:m32b",
            "source_pr_number": SOURCE_PR_NUMBER,
        }

    def test_handoff_requires_persisted_local_first_reconciler_decision(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = self._repair_bundle(Path(directory))
            validated = validate_handoff_bundle(bundle)
            self.assertEqual(validated["source_run_id"], SOURCE_RUN_ID)
            self.assertEqual(validated["source_run_attempt"], SOURCE_RUN_ATTEMPT)
            self.assertEqual(validated["source_head_sha"], HEAD_SHA)

            tampered = copy.deepcopy(bundle)
            tampered["reconcile_outcome"]["decision_id"] = "f" * 64
            with self.assertRaises(AutonomyDispatchError):
                validate_handoff_bundle(tampered)

    def test_owner_dispatch_compiles_exact_protected_stage2_request(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = self._repair_bundle(Path(directory))
            result = compile_trusted_handoff(
                bundle,
                repository=REPOSITORY,
                actor="toctionyan",
                event_name="workflow_dispatch",
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                authorization_id="owner-autonomy:m32b:9501:1",
                handoff_run_id=9600,
                handoff_run_attempt=1,
                observed_pr_number=SOURCE_PR_NUMBER,
                observed_pr_head_sha=HEAD_SHA,
                observed_pr_draft=True,
                observed_pr_state="open",
            )
            self.assertEqual(result["plan"]["kind"], "REQUEST_STAGE2_REPAIR")
            self.assertEqual(result["network_request"]["kind"], "DISPATCH_STAGE2")
            self.assertEqual(
                result["network_request"]["required_environment"],
                "production-certification",
            )
            self.assertNotIn(
                "remote_repair_approval", result["network_request"]["inputs"]
            )
            self.assertFalse(result["write_authority_effect"])
            self.assertFalse(result["test_authority_effect"])
            self.assertFalse(result["merge_allowed"])
            self.assertFalse(result["deploy_allowed"])
            self.assertFalse(result["production_closed"])

    def test_stale_or_non_draft_pr_cannot_receive_network_wakeup(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = self._repair_bundle(Path(directory))
            base = {
                "repository": REPOSITORY,
                "actor": "toctionyan",
                "event_name": "workflow_dispatch",
                "trusted_workflow_ref": TRUSTED_WORKFLOW_REF,
                "authorization_id": "owner-autonomy:m32b:9501:1",
                "handoff_run_id": 9600,
                "handoff_run_attempt": 1,
                "observed_pr_number": SOURCE_PR_NUMBER,
                "observed_pr_head_sha": HEAD_SHA,
                "observed_pr_draft": True,
                "observed_pr_state": "open",
            }
            stale = dict(base)
            stale["observed_pr_head_sha"] = "d" * 40
            with self.assertRaises(AutonomyDispatchError):
                compile_trusted_handoff(bundle, **stale)
            not_draft = dict(base)
            not_draft["observed_pr_draft"] = False
            with self.assertRaises(AutonomyDispatchError):
                compile_trusted_handoff(bundle, **not_draft)

    def test_candidate_supplied_unpersisted_repair_decision_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = self._repair_bundle(Path(directory))
            forged = copy.deepcopy(bundle)
            decisions = forged["task"]["metadata"]["engineering_reconciler"]["decisions"]
            decisions.clear()
            with self.assertRaises(AutonomyDispatchError):
                validate_handoff_bundle(forged)

    def test_two_task_authority_substitution_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = self._repair_bundle(Path(directory))
            forged = copy.deepcopy(bundle)
            forged["task"]["task_id"] = "competing-task-owner"
            with self.assertRaises(AutonomyDispatchError):
                validate_handoff_bundle(forged)


if __name__ == "__main__":
    unittest.main()
