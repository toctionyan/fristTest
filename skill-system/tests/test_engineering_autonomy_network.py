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
)
from engineering_autonomy_network import (  # noqa: E402
    build_dispatch_receipt,
    compile_network_request,
    validate_dispatch_plan,
    validate_dispatch_receipt,
    validate_network_request,
)
from local_first_governance import create_local_first_task  # noqa: E402


REPOSITORY = "toctionyan/fristTest"
BRANCH = "agent/m32b-network-test"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
CONTROL_SHA = "c" * 40
TRUSTED_WORKFLOW_REF = (
    ".github/workflows/engineering-autonomy-authorize.yml@" + CONTROL_SHA
)
SOURCE_RUN_ID = 9201
SOURCE_RUN_ATTEMPT = 2
FAILURE_SIGNATURE = "quality-quick-execution:assertion:network-contract"


class EngineeringAutonomyNetworkTests(unittest.TestCase):
    def _store(self, root: Path):
        return create_local_first_task(
            root / "task-run.json",
            task_id="m32b-network-test",
            change_id="change-m32b-network-test",
            base_sha=BASE_SHA,
            branch=BRANCH,
            patch_owner="product-implementer",
            allowed_paths=["services/agent-service/app/runtime.py"],
            target_fingerprint="target-m32b",
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
            owner_authorization_ref="github-owner-workflow-dispatch:9200",
        )
        return grant

    def _authorization(self, store, grant):
        return build_owner_authorization_evidence(
            task=store.payload,
            grant=grant,
            repository=REPOSITORY,
            source_run_id=SOURCE_RUN_ID,
            source_run_attempt=SOURCE_RUN_ATTEMPT,
            source_head_sha=HEAD_SHA,
            failure_signature=FAILURE_SIGNATURE,
            actor="toctionyan",
            event_name="workflow_dispatch",
            trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
            authorization_id="owner-autonomy:m32b:9201:2",
        )

    def _repair_outcome(self):
        return {
            "schema": "engineering-reconcile-decision@1",
            "decision_id": "d" * 64,
            "task_id": "m32b-network-test",
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
        result = self._repair_outcome()
        result.update(
            {
                "decision_id": "e" * 64,
                "decision": "RETRY_CI",
                "action": "retry_transient_ci",
                "failure_class": "TRANSIENT_INFRA_FAILURE",
                "product_write_allowed": False,
            }
        )
        return result

    def _plan(self, store, grant, authorization, outcome):
        return compile_dispatch_plan(
            store,
            grant,
            authorization,
            reconcile_outcome=outcome,
            repository=REPOSITORY,
            trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
            current_head_sha=HEAD_SHA,
        )

    def test_repair_plan_is_digest_bound_before_network_use(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
            outcome = self._repair_outcome()
            plan = self._plan(store, grant, authorization, outcome)
            validated = validate_dispatch_plan(
                plan,
                task=store.payload,
                grant=grant,
                authorization_evidence=authorization,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
            )
            self.assertEqual(validated["kind"], "REQUEST_STAGE2_REPAIR")
            tampered = copy.deepcopy(plan)
            tampered["required_environment"] = "unprotected"
            with self.assertRaises(AutonomyDispatchError):
                validate_dispatch_plan(
                    tampered,
                    task=store.payload,
                    grant=grant,
                    authorization_evidence=authorization,
                    reconcile_outcome=outcome,
                    repository=REPOSITORY,
                    trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                )

    def test_autonomy_repair_request_targets_only_protected_stage2(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
            outcome = self._repair_outcome()
            plan = self._plan(store, grant, authorization, outcome)
            request = compile_network_request(
                plan,
                task=store.payload,
                grant=grant,
                authorization_evidence=authorization,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                handoff_run_id=9300,
                handoff_run_attempt=1,
            )
            self.assertEqual(request["kind"], "DISPATCH_STAGE2")
            self.assertEqual(
                request["workflow"], ".github/workflows/governed-ci-repair-stage2.yml"
            )
            self.assertEqual(request["ref"], "main")
            self.assertEqual(request["required_environment"], "production-certification")
            self.assertEqual(request["inputs"]["repair_round"], "1")
            self.assertNotIn("remote_repair_approval", request["inputs"])
            self.assertTrue(request["product_write_allowed"])
            self.assertFalse(request["merge_allowed"])
            self.assertFalse(request["deploy_allowed"])
            self.assertFalse(request["production_closed"])
            validate_network_request(request, plan=plan)

    def test_autonomy_request_rejects_legacy_manual_approval_injection(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
            outcome = self._repair_outcome()
            plan = self._plan(store, grant, authorization, outcome)
            request = compile_network_request(
                plan,
                task=store.payload,
                grant=grant,
                authorization_evidence=authorization,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                handoff_run_id=9300,
                handoff_run_attempt=1,
            )
            tampered = copy.deepcopy(request)
            tampered["inputs"]["remote_repair_approval"] = "explicitly-approved"
            with self.assertRaises(AutonomyDispatchError):
                validate_network_request(tampered, plan=plan)

    def test_transient_retry_can_only_rerun_exact_source_attempt(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
            outcome = self._retry_outcome()
            plan = self._plan(store, grant, authorization, outcome)
            request = compile_network_request(
                plan,
                task=store.payload,
                grant=grant,
                authorization_evidence=authorization,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                handoff_run_id=9301,
                handoff_run_attempt=1,
            )
            self.assertEqual(request["kind"], "RERUN_SOURCE_RUN")
            self.assertEqual(request["source_run_id"], SOURCE_RUN_ID)
            self.assertEqual(request["source_run_attempt"], SOURCE_RUN_ATTEMPT)
            self.assertEqual(request["source_head_sha"], HEAD_SHA)
            self.assertIsNone(request["workflow"])
            self.assertEqual(request["inputs"], {})
            self.assertFalse(request["product_write_allowed"])

            tampered = copy.deepcopy(request)
            tampered["source_run_id"] = SOURCE_RUN_ID + 1
            with self.assertRaises(AutonomyDispatchError):
                validate_network_request(tampered, plan=plan)

    def test_wrong_authorization_or_plan_lineage_cannot_dispatch(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
            outcome = self._repair_outcome()
            plan = self._plan(store, grant, authorization, outcome)
            wrong_outcome = copy.deepcopy(outcome)
            wrong_outcome["delivery_key"] = (
                f"{SOURCE_RUN_ID + 1}:{SOURCE_RUN_ATTEMPT}:{HEAD_SHA}"
            )
            with self.assertRaises(AutonomyDispatchError):
                validate_dispatch_plan(
                    plan,
                    task=store.payload,
                    grant=grant,
                    authorization_evidence=authorization,
                    reconcile_outcome=wrong_outcome,
                    repository=REPOSITORY,
                    trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                )

    def test_dispatch_receipt_is_exact_plan_and_request_bound(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
            outcome = self._repair_outcome()
            plan = self._plan(store, grant, authorization, outcome)
            request = compile_network_request(
                plan,
                task=store.payload,
                grant=grant,
                authorization_evidence=authorization,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                handoff_run_id=9300,
                handoff_run_attempt=1,
            )
            pending = build_dispatch_receipt(
                plan=plan,
                network_request=request,
                status="PENDING",
            )
            validate_dispatch_receipt(pending, plan=plan, network_request=request)
            dispatched = build_dispatch_receipt(
                plan=plan,
                network_request=request,
                status="DISPATCHED",
                network_ref="github-actions:stage2:9400",
            )
            validated = validate_dispatch_receipt(
                dispatched,
                plan=plan,
                network_request=request,
            )
            self.assertEqual(validated["status"], "DISPATCHED")
            self.assertEqual(validated["decision_id"], plan["decision_id"])
            self.assertEqual(
                validated["authorization_sha256"], plan["authorization_sha256"]
            )
            self.assertFalse(validated["merge_allowed"])
            self.assertFalse(validated["deploy_allowed"])
            self.assertFalse(validated["production_closed"])

    def test_receipt_cannot_claim_dispatch_without_network_reference(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
            outcome = self._repair_outcome()
            plan = self._plan(store, grant, authorization, outcome)
            request = compile_network_request(
                plan,
                task=store.payload,
                grant=grant,
                authorization_evidence=authorization,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                handoff_run_id=9300,
                handoff_run_attempt=1,
            )
            with self.assertRaises(AutonomyDispatchError):
                build_dispatch_receipt(
                    plan=plan,
                    network_request=request,
                    status="DISPATCHED",
                )

    def test_noop_plan_cannot_gain_network_target(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            grant = self._grant(store)
            authorization = self._authorization(store, grant)
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
            plan = self._plan(store, grant, authorization, outcome)
            request = compile_network_request(
                plan,
                task=store.payload,
                grant=grant,
                authorization_evidence=authorization,
                reconcile_outcome=outcome,
                repository=REPOSITORY,
                trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
                handoff_run_id=9302,
                handoff_run_attempt=1,
            )
            self.assertEqual(request["kind"], "NOOP")
            self.assertIsNone(request["workflow"])
            self.assertEqual(request["inputs"], {})
            self.assertFalse(request["product_write_allowed"])


if __name__ == "__main__":
    unittest.main()
