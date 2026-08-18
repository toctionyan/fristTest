from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPT = ROOT / "scripts" / "github_repair_autonomy_stage2.py"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import bind_autonomy_grant, create_autonomy_grant  # noqa: E402
from engineering_autonomy_dispatch import AutonomyDispatchError  # noqa: E402
from engineering_autonomy_handoff import (  # noqa: E402
    HANDOFF_BUNDLE_SCHEMA,
    compile_trusted_handoff,
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


def _load_stage2():
    spec = importlib.util.spec_from_file_location("github_repair_autonomy_stage2", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STAGE2 = _load_stage2()
REPOSITORY = "toctionyan/fristTest"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
AUTHORIZE_HEAD_SHA = "c" * 40
BRANCH = "agent/m32b-stage2-test"
SOURCE_RUN_ID = 9701
SOURCE_RUN_ATTEMPT = 1
SOURCE_PR_NUMBER = 1808
HANDOFF_RUN_ID = 9801
HANDOFF_RUN_ATTEMPT = 1
FAILURE_SIGNATURE = "quality:meaningful-product-red:stage2"
TRUSTED_WORKFLOW_REF = (
    ".github/workflows/engineering-autonomy-authorize.yml@" + AUTHORIZE_HEAD_SHA
)


class EngineeringAutonomyStage2Tests(unittest.TestCase):
    def _handoff_result(self, root: Path):
        store = create_local_first_task(
            root / "local-task.json",
            task_id="m32b-stage2-local-owner",
            change_id="change-m32b-stage2",
            base_sha=BASE_SHA,
            branch=BRANCH,
            patch_owner="product-implementer",
            allowed_paths=["services/agent-service/app/runtime.py"],
            target_fingerprint="target-m32b-stage2",
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
            owner_authorization_ref="github-owner-ack:stage2",
        )
        observation = CIObservation(
            run_id=SOURCE_RUN_ID,
            run_attempt=SOURCE_RUN_ATTEMPT,
            head_sha=HEAD_SHA,
            conclusion="failure",
            job_name="quality-quick-execution",
            log_text="AssertionError: expected READY, got BLOCKED",
            evidence_refs=(f"github:run:{SOURCE_RUN_ID}:attempt:1",),
        )
        outcome = reconcile_ci_terminal(
            store,
            grant,
            repository=REPOSITORY,
            observation=observation,
            product_verdict="FAIL",
            transport_verdict="FAIL",
            authority_context={
                "underlying_write_authority": True,
                "exact_write_scope": True,
                "current_head_sha": HEAD_SHA,
            },
        )
        self.assertEqual(outcome["decision"], "REPAIR_PRODUCT")
        bundle = {
            "schema": HANDOFF_BUNDLE_SCHEMA,
            "task": copy.deepcopy(store.payload),
            "grant": copy.deepcopy(grant),
            "reconcile_outcome": copy.deepcopy(outcome),
            "failure_signature": FAILURE_SIGNATURE,
            "source_pr_number": SOURCE_PR_NUMBER,
        }
        return compile_trusted_handoff(
            bundle,
            repository=REPOSITORY,
            actor="toctionyan",
            event_name="workflow_dispatch",
            trusted_workflow_ref=TRUSTED_WORKFLOW_REF,
            authorization_id="owner-autonomy:stage2:9701:1",
            handoff_run_id=HANDOFF_RUN_ID,
            handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
            observed_pr_number=SOURCE_PR_NUMBER,
            observed_pr_head_sha=HEAD_SHA,
            observed_pr_draft=True,
            observed_pr_state="open",
        )

    def _stage1(self):
        failure = {
            "schema": "github-failure-ingest@1",
            "status": "INGESTED",
            "repository": REPOSITORY,
            "workflow_name": "quality",
            "workflow_run_id": str(SOURCE_RUN_ID),
            "workflow_run_attempt": str(SOURCE_RUN_ATTEMPT),
            "head_sha": HEAD_SHA,
            "head_branch": BRANCH,
            "failure_signature": FAILURE_SIGNATURE,
            "classification": "code_or_contract",
            "repair_allowed": True,
            "same_repository": True,
            "candidate_paths": ["services/agent-service/app/runtime.py"],
        }
        task = {
            "binding": {
                "repository": REPOSITORY,
                "workflow_run_id": str(SOURCE_RUN_ID),
                "workflow_run_attempt": str(SOURCE_RUN_ATTEMPT),
                "head_sha": HEAD_SHA,
                "failure_signature": FAILURE_SIGNATURE,
            }
        }
        return failure, task

    def test_exact_autonomy_handoff_is_admitted_to_protected_stage2(self) -> None:
        with TemporaryDirectory() as directory:
            result = self._handoff_result(Path(directory))
            failure, stage1_task = self._stage1()
            verified = STAGE2.verify_stage2_autonomy_handoff(
                failure=failure,
                stage1_task=stage1_task,
                handoff_result=result,
                repository=REPOSITORY,
                source_run_id=SOURCE_RUN_ID,
                source_run_attempt=SOURCE_RUN_ATTEMPT,
                handoff_run_id=HANDOFF_RUN_ID,
                handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
                handoff_head_sha=AUTHORIZE_HEAD_SHA,
                stage2_control_sha=AUTHORIZE_HEAD_SHA,
            )
            self.assertTrue(verified["repair_allowed"])
            self.assertEqual(verified["input_kind"], "autonomy_stage1")
            self.assertEqual(verified["head_sha"], HEAD_SHA)
            self.assertEqual(verified["control_sha"], AUTHORIZE_HEAD_SHA)
            self.assertEqual(verified["repair_round"], 1)
            self.assertFalse(verified["production_closed"])

    def test_stage1_and_local_first_source_identity_must_match(self) -> None:
        with TemporaryDirectory() as directory:
            result = self._handoff_result(Path(directory))
            failure, stage1_task = self._stage1()
            failure["head_sha"] = "d" * 40
            stage1_task["binding"]["head_sha"] = "d" * 40
            with self.assertRaises(AutonomyDispatchError):
                STAGE2.verify_stage2_autonomy_handoff(
                    failure=failure,
                    stage1_task=stage1_task,
                    handoff_result=result,
                    repository=REPOSITORY,
                    source_run_id=SOURCE_RUN_ID,
                    source_run_attempt=SOURCE_RUN_ATTEMPT,
                    handoff_run_id=HANDOFF_RUN_ID,
                    handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
                    handoff_head_sha=AUTHORIZE_HEAD_SHA,
                    stage2_control_sha=AUTHORIZE_HEAD_SHA,
                )

    def test_wrong_authorize_workflow_sha_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            result = self._handoff_result(Path(directory))
            failure, stage1_task = self._stage1()
            with self.assertRaises(AutonomyDispatchError):
                STAGE2.verify_stage2_autonomy_handoff(
                    failure=failure,
                    stage1_task=stage1_task,
                    handoff_result=result,
                    repository=REPOSITORY,
                    source_run_id=SOURCE_RUN_ID,
                    source_run_attempt=SOURCE_RUN_ATTEMPT,
                    handoff_run_id=HANDOFF_RUN_ID,
                    handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
                    handoff_head_sha="f" * 40,
                    stage2_control_sha="f" * 40,
                )

    def test_stage2_control_sha_must_equal_authorized_control_sha(self) -> None:
        with TemporaryDirectory() as directory:
            result = self._handoff_result(Path(directory))
            failure, stage1_task = self._stage1()
            with self.assertRaisesRegex(
                AutonomyDispatchError,
                "Stage-2 trusted control SHA differs from the owner-authorized control SHA",
            ):
                STAGE2.verify_stage2_autonomy_handoff(
                    failure=failure,
                    stage1_task=stage1_task,
                    handoff_result=result,
                    repository=REPOSITORY,
                    source_run_id=SOURCE_RUN_ID,
                    source_run_attempt=SOURCE_RUN_ATTEMPT,
                    handoff_run_id=HANDOFF_RUN_ID,
                    handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
                    handoff_head_sha=AUTHORIZE_HEAD_SHA,
                    stage2_control_sha="d" * 40,
                )

    def test_tampered_plan_or_handoff_run_binding_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            result = self._handoff_result(Path(directory))
            failure, stage1_task = self._stage1()
            tampered = copy.deepcopy(result)
            tampered["plan"]["required_environment"] = "unprotected"
            with self.assertRaises(AutonomyDispatchError):
                STAGE2.verify_stage2_autonomy_handoff(
                    failure=failure,
                    stage1_task=stage1_task,
                    handoff_result=tampered,
                    repository=REPOSITORY,
                    source_run_id=SOURCE_RUN_ID,
                    source_run_attempt=SOURCE_RUN_ATTEMPT,
                    handoff_run_id=HANDOFF_RUN_ID,
                    handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
                    handoff_head_sha=AUTHORIZE_HEAD_SHA,
                    stage2_control_sha=AUTHORIZE_HEAD_SHA,
                )

            with self.assertRaises(AutonomyDispatchError):
                STAGE2.verify_stage2_autonomy_handoff(
                    failure=failure,
                    stage1_task=stage1_task,
                    handoff_result=result,
                    repository=REPOSITORY,
                    source_run_id=SOURCE_RUN_ID,
                    source_run_attempt=SOURCE_RUN_ATTEMPT,
                    handoff_run_id=HANDOFF_RUN_ID + 1,
                    handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
                    handoff_head_sha=AUTHORIZE_HEAD_SHA,
                    stage2_control_sha=AUTHORIZE_HEAD_SHA,
                )

    def test_non_repairable_stage1_never_enters_autonomy_stage2(self) -> None:
        with TemporaryDirectory() as directory:
            result = self._handoff_result(Path(directory))
            failure, stage1_task = self._stage1()
            failure["repair_allowed"] = False
            with self.assertRaises(AutonomyDispatchError):
                STAGE2.verify_stage2_autonomy_handoff(
                    failure=failure,
                    stage1_task=stage1_task,
                    handoff_result=result,
                    repository=REPOSITORY,
                    source_run_id=SOURCE_RUN_ID,
                    source_run_attempt=SOURCE_RUN_ATTEMPT,
                    handoff_run_id=HANDOFF_RUN_ID,
                    handoff_run_attempt=HANDOFF_RUN_ATTEMPT,
                    handoff_head_sha=AUTHORIZE_HEAD_SHA,
                    stage2_control_sha=AUTHORIZE_HEAD_SHA,
                )


if __name__ == "__main__":
    unittest.main()
