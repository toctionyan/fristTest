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
from engineering_autonomy_dispatch import (  # noqa: E402
    AutonomyDispatchError,
    build_owner_authorization_evidence,
    compile_dispatch_plan,
    validate_owner_authorization_evidence,
)
from local_first_governance import create_local_first_task  # noqa: E402


REPOSITORY = "toctionyan/fristTest"
BRANCH = "agent/m31-dispatch-test"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
TRUSTED_WORKFLOW_REF = ".github/workflows/engineering-autonomy-authorize.yml@" + ("c" * 40)
SOURCE_RUN_ID = 9101
SOURCE_RUN_ATTEMPT = 2
FAILURE_SIGNATURE = "quality-quick-execution:assertion:ready-blocked"


class EngineeringAutonomyDispatchTests(unittest.TestCase):
    def _store(self, root: Path):
        return create_local_first_task(
            root / "task-run.json",
            task_id="m31-dispatch-test",
            change_id="change-m31-dispatch-test",
            base_sha=BASE_SHA,
            branch=BRANCH,
            patch_owner="product-implementer",
            allowed_paths=["services/agent-service/app/runtime.py"],
            target_fingerprint="target-m31",
        )

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
            owner_authorization_ref="github-owner-workflow-dispatch:9100",
        )
        return grant

    def _authorization(self, store, grant, **overrides):
        values = {
            "task": store.payload,
            "grant": grant,
            "repository": REPOSITORY,
            "source_run_id": SOURCE_RUN_ID,
            "source_run_attempt": SOURCE_RUN_ATTEMPT,
            "source_head_sha": HEAD_SHA,
            "failure_signature": FAILURE_SIGNATURE,
            "actor": "toctionyan",
            "event_name": "workflow_dispatch",
            "trusted_workflow_ref": TRUSTED_WORKFLOW_REF,
            "authorization_id": "owner-autonomy:m31:9101:2",
        }
        values.update(overrides)
        return build_owner_authorization_evidence(**values)

    def _repair_outcome(self):
        return {
            "schema": "engineering-reconcile-decision@1",
            "decision_id": "d" * 64,
            "task_id": "m31-dispatch-test",
            "delivery_key": f"{SOURCE_RUN_ID}:{SOURCE_RUN_ATTEMPT}:{HEAD_SHA}",
            "decision": "REPAIR_PRODUCT",
            "action": "repair_meaningful_product_red",
            "allowed": True,
            "human_required": False,
            "failure_class": "PRODUCT_SOURCE_FAILURE",
            "product_write_allowed": True,
            "authority_effect": "automation_continuation_only",
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
            "duplicate": False,
        }

    def _retry_outcome(self):
        return {
            "schema": "engineering-reconcile-decision@1",
            "decision_id": "e" * 64,
            "task_id": "m31-dispatch-test",
            "delivery_key": f"{SOURCE_RUN_ID}:{SOURCE_RUN_ATTEMPT}:{HEAD_SHA}",
            "decision": "RETRY_CI",
            "action": "retry_transient_ci",
            "allowed": True,
            "human_required": False,
            "failure_class": "TRANSIENT_INFRA_FAILURE",
            "product_write_allowed": False,
            "authority_effect": "automation_continuation_only",
            "merge_allowed": False,
            "deploy_allowed": False,
            "production_closed": False,
            "duplicate": False,
        }

    def test_owner_authorization_requires_trusted_workflow_dispatch(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            with self.assertRaises(AutonomyDispatchError):
                self._authorization(store, grant, event_name="pull_request")
            with self.assertRaises(AutonomyDispatchError):
                self._authorization(store, grant, actor="")
            with self.assertRaises(AutonomyDispatchError):
                self._authorization(store, grant, trusted_workflow_ref="candidate/workflow.yml@" + ("c" * 40))

    def test_authorization_is_exact_task_run_head_failure_and_grant_bound(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            validated = validate_owner_authorization_evidence(
                evidence,
                task=store.payload,
                grant=grant,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
            )
            self.assertEqual(validated["task_id"], "m31-dispatch-test")
            self.assertEqual(validated["source_run_id"], SOURCE_RUN_ID)
            self.assertEqual(validated["source_run_attempt"], SOURCE_RUN_ATTEMPT)
            self.assertEqual(validated["source_head_sha"], HEAD_SHA)
            self.assertEqual(validated["grant_sha256"], grant["grant_sha256"])
            self.assertEqual(validated["authority_effect"], "autonomy_continuation_authorization_only")
            self.assertFalse(validated["write_authority_effect"])
            self.assertFalse(validated["test_authority_effect"])
            self.assertFalse(validated["merge_allowed"])
            self.assertFalse(validated["deploy_allowed"])
            self.assertFalse(validated["production_closed"])

            tampered = copy.deepcopy(evidence)
            tampered["source_head_sha"] = "f" * 40
            with self.assertRaises(AutonomyDispatchError):
                validate_owner_authorization_evidence(
                    tampered,
                    task=store.payload,
                    grant=grant,
                    repository=REPOSITORY,
                    trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                )

    def test_repair_product_compiles_stage2_request_without_synthesizing_manual_approval(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            plan = compile_dispatch_plan(
                store,
                grant,
                evidence,
                reconcile_outcome=self._repair_outcome(),
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                current_head_sha=HEAD_SHA,
            )
            self.assertEqual(plan["kind"], "REQUEST_STAGE2_REPAIR")
            self.assertEqual(plan["workflow"], ".github/workflows/governed-ci-repair-stage2.yml")
            self.assertEqual(plan["required_environment"], "production-certification")
            self.assertEqual(plan["inputs"]["source_quality_run_id"], str(SOURCE_RUN_ID))
            self.assertEqual(plan["inputs"]["source_quality_run_attempt"], str(SOURCE_RUN_ATTEMPT))
            self.assertEqual(plan["inputs"]["autonomy_authorization_id"], evidence["authorization_id"])
            self.assertEqual(plan["inputs"]["autonomy_authorization_sha256"], evidence["authorization_sha256"])
            self.assertNotIn("remote_repair_approval", plan["inputs"])
            self.assertFalse(plan["merge_allowed"])
            self.assertFalse(plan["deploy_allowed"])
            self.assertFalse(plan["production_closed"])

    def test_retry_ci_can_only_rerun_same_candidate_and_never_stage2(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            plan = compile_dispatch_plan(
                store,
                grant,
                evidence,
                reconcile_outcome=self._retry_outcome(),
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                current_head_sha=HEAD_SHA,
            )
            self.assertEqual(plan["kind"], "RERUN_SAME_CANDIDATE")
            self.assertIsNone(plan["workflow"])
            self.assertEqual(plan["source_head_sha"], HEAD_SHA)
            self.assertFalse(plan["product_write_allowed"])
            self.assertNotIn("remote_repair_approval", plan["inputs"])
            with self.assertRaises(AutonomyDispatchError):
                compile_dispatch_plan(
                    store,
                    grant,
                    evidence,
                    reconcile_outcome=self._retry_outcome(),
                    repository=REPOSITORY,
                    trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                    current_head_sha="f" * 40,
                )

    def test_stop_decision_is_noop_and_cannot_be_promoted_to_repair(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            outcome = self._repair_outcome()
            outcome.update(
                {
                    "decision": "STOP_TRANSPORT_FAILURE",
                    "action": None,
                    "allowed": False,
                    "human_required": True,
                    "failure_class": "TRANSPORT_FAILURE",
                    "product_write_allowed": False,
                }
            )
            plan = compile_dispatch_plan(
                store,
                grant,
                evidence,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                current_head_sha=HEAD_SHA,
            )
            self.assertEqual(plan["kind"], "NOOP_STOPPED")
            self.assertIsNone(plan["workflow"])
            self.assertFalse(plan["product_write_allowed"])

    def test_repair_outcome_must_preserve_controller_authority_invariants(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            for field in ("merge_allowed", "deploy_allowed", "production_closed"):
                outcome = self._repair_outcome()
                outcome[field] = True
                with self.assertRaises(AutonomyDispatchError):
                    compile_dispatch_plan(
                        store,
                        grant,
                        evidence,
                        reconcile_outcome=outcome,
                        repository=REPOSITORY,
                        trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                        current_head_sha=HEAD_SHA,
                    )

    def test_wrong_authorization_lineage_or_grant_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            outcome = self._repair_outcome()
            outcome["delivery_key"] = f"{SOURCE_RUN_ID + 1}:{SOURCE_RUN_ATTEMPT}:{HEAD_SHA}"
            with self.assertRaises(AutonomyDispatchError):
                compile_dispatch_plan(
                    store,
                    grant,
                    evidence,
                    reconcile_outcome=outcome,
                    repository=REPOSITORY,
                    trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                    current_head_sha=HEAD_SHA,
                )

            competing = dict(grant)
            competing["grant_id"] = "autonomy:competing"
            with self.assertRaises(AutonomyDispatchError):
                compile_dispatch_plan(
                    store,
                    competing,
                    evidence,
                    reconcile_outcome=self._repair_outcome(),
                    repository=REPOSITORY,
                    trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                    current_head_sha=HEAD_SHA,
                )

    def test_prior_dispatch_receipt_prevents_duplicate_network_action(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            outcome = self._repair_outcome()
            plan = compile_dispatch_plan(
                store,
                grant,
                evidence,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                current_head_sha=HEAD_SHA,
                prior_dispatch_receipts=[
                    {
                        "schema": "engineering-autonomy-dispatch-receipt@1",
                        "decision_id": outcome["decision_id"],
                        "status": "DISPATCHED",
                        "authorization_sha256": evidence["authorization_sha256"],
                    }
                ],
            )
            self.assertEqual(plan["kind"], "NOOP_ALREADY_DISPATCHED")
            self.assertIsNone(plan["workflow"])

    def test_candidate_cannot_change_trusted_workflow_identity(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            evidence = self._authorization(store, grant)
            with self.assertRaises(AutonomyDispatchError):
                compile_dispatch_plan(
                    store,
                    grant,
                    evidence,
                    reconcile_outcome=self._repair_outcome(),
                    repository=REPOSITORY,
                    trusted_workflow_ref=".github/workflows/engineering-autonomy-authorize.yml@" + ("f" * 40),
                    current_head_sha=HEAD_SHA,
                )


if __name__ == "__main__":
    unittest.main()
