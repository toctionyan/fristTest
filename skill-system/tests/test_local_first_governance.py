from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from local_first_governance import (  # noqa: E402
    CIFeedbackBindingError,
    UploadAdmissionError,
    begin_local_repair_round,
    bind_ci_run,
    classify_ci_failure,
    create_local_first_task,
    export_status,
    record_ci_result,
    record_local_gate,
    scope_violations,
    upload_admission,
)


class LocalFirstGovernanceTests(unittest.TestCase):
    def _store(self, root: Path):
        return create_local_first_task(
            root / "task-run.json",
            task_id="local-first-test",
            change_id="change-local-first-test",
            base_sha="a" * 40,
            branch="agent/local-first-test",
            patch_owner="product-implementer",
            allowed_paths=["services/agent-service/app/runtime.py", "tests/runtime/**"],
            target_fingerprint="target-1",
        )

    def _pass_local(self, store) -> None:
        begin_local_repair_round(store, workspace_fingerprint="workspace-green")
        for gate in ("targeted", "module", "static", "quick", "review", "scope"):
            record_local_gate(
                store,
                gate=gate,
                passed=True,
                evidence_refs=[f"evidence/{gate}.json"],
                workspace_fingerprint="workspace-green",
            )

    def test_scope_never_expands_from_ci_logs(self) -> None:
        violations = scope_violations(
            ["services/agent-service/app/runtime.py", "scripts/verify_task_ledger.py"],
            ["services/agent-service/app/runtime.py"],
        )
        self.assertEqual(violations, ("scripts/verify_task_ledger.py",))

    def test_upload_requires_all_local_gates_and_exact_scope(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            decision = upload_admission(
                store,
                changed_paths=["services/agent-service/app/runtime.py"],
                candidate_head_sha="b" * 40,
                workspace_fingerprint="workspace-green",
                evidence_refs=["upload.json"],
            )
            self.assertFalse(decision.allowed)
            self.assertIn("local_targeted_green", decision.missing_conditions)

            self._pass_local(store)
            denied = upload_admission(
                store,
                changed_paths=["scripts/verify_task_ledger.py"],
                candidate_head_sha="b" * 40,
                workspace_fingerprint="workspace-green",
                evidence_refs=["upload.json"],
            )
            self.assertFalse(denied.allowed)
            self.assertEqual(denied.scope_violations, ("scripts/verify_task_ledger.py",))

            admitted = upload_admission(
                store,
                changed_paths=["services/agent-service/app/runtime.py"],
                candidate_head_sha="b" * 40,
                workspace_fingerprint="workspace-green",
                evidence_refs=["upload.json"],
            )
            self.assertTrue(admitted.allowed)
            self.assertEqual(export_status(store)["phase"], "READY_FOR_CI")

    def test_ci_failure_is_returned_to_original_patch_owner(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            self._pass_local(store)
            upload_admission(
                store,
                changed_paths=["services/agent-service/app/runtime.py"],
                candidate_head_sha="b" * 40,
                workspace_fingerprint="workspace-green",
                evidence_refs=["upload.json"],
            )
            bind_ci_run(
                store,
                run_id=123,
                run_attempt=1,
                head_sha="b" * 40,
                evidence_refs=["ci-start.json"],
            )
            decision = record_ci_result(
                store,
                run_id=123,
                run_attempt=1,
                head_sha="b" * 40,
                conclusion="failure",
                job_name="quality-quick",
                log_text="AssertionError: expected READY, got BLOCKED",
                evidence_refs=["ci-failure.json"],
            )
            self.assertIsNotNone(decision)
            self.assertEqual(decision.kind, "code_or_contract")
            status = export_status(store)
            self.assertEqual(status["phase"], "CI_FAILURE_RETURNED_TO_PATCH_OWNER")
            self.assertTrue(status["ci_feedback"][-1]["product_code_write_allowed"])
            self.assertFalse(status["ci_feedback"][-1]["remote_repair_allowed"])

    def test_environment_and_secret_failures_cannot_edit_product_code(self) -> None:
        environment = classify_ci_failure(job_name="integration", log_text="Could not resolve host: registry.example")
        secret = classify_ci_failure(job_name="model", log_text="401 Unauthorized: bad credentials")
        self.assertEqual(environment.owner, "ci-reliability-agent")
        self.assertFalse(environment.product_code_write_allowed)
        self.assertEqual(secret.owner, "platform-operator")
        self.assertFalse(secret.product_code_write_allowed)

    def test_ci_binding_rejects_another_sha(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            self._pass_local(store)
            upload_admission(
                store,
                changed_paths=["services/agent-service/app/runtime.py"],
                candidate_head_sha="b" * 40,
                workspace_fingerprint="workspace-green",
                evidence_refs=["upload.json"],
            )
            with self.assertRaises(CIFeedbackBindingError):
                bind_ci_run(
                    store,
                    run_id=123,
                    run_attempt=1,
                    head_sha="c" * 40,
                    evidence_refs=["ci-start.json"],
                )

    def test_ci_success_completes_only_after_local_and_ci_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            self._pass_local(store)
            upload_admission(
                store,
                changed_paths=["services/agent-service/app/runtime.py"],
                candidate_head_sha="b" * 40,
                workspace_fingerprint="workspace-green",
                evidence_refs=["upload.json"],
            )
            bind_ci_run(
                store,
                run_id=123,
                run_attempt=1,
                head_sha="b" * 40,
                evidence_refs=["ci-start.json"],
            )
            decision = record_ci_result(
                store,
                run_id=123,
                run_attempt=1,
                head_sha="b" * 40,
                conclusion="success",
                job_name="quality",
                log_text="all green",
                evidence_refs=["ci-success.json"],
            )
            self.assertIsNone(decision)
            status = export_status(store)
            self.assertEqual(status["status"], "COMPLETED")
            self.assertFalse(status["production_closed"])

    def test_new_local_round_invalidates_prior_green_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            self._pass_local(store)
            admitted = upload_admission(
                store,
                changed_paths=["services/agent-service/app/runtime.py"],
                candidate_head_sha="b" * 40,
                workspace_fingerprint="workspace-green",
                evidence_refs=["upload.json"],
            )
            self.assertTrue(admitted.allowed)
            # Simulate a CI code failure returning the task to the same Patch Owner.
            bind_ci_run(
                store,
                run_id=123,
                run_attempt=1,
                head_sha="b" * 40,
                evidence_refs=["ci-start.json"],
            )
            record_ci_result(
                store,
                run_id=123,
                run_attempt=1,
                head_sha="b" * 40,
                conclusion="failure",
                job_name="quality-quick",
                log_text="AssertionError: expected READY",
                evidence_refs=["ci-failure.json"],
            )
            begin_local_repair_round(store, workspace_fingerprint="workspace-round-2")
            status = export_status(store)
            self.assertEqual(status["counters"]["local_repair_rounds"], 2)
            self.assertFalse(status["conditions"]["local_targeted_green"])
            denied = upload_admission(
                store,
                changed_paths=["services/agent-service/app/runtime.py"],
                candidate_head_sha="c" * 40,
                workspace_fingerprint="workspace-round-2",
                evidence_refs=["upload-round-2.json"],
            )
            self.assertFalse(denied.allowed)

    def test_test_authority_failure_is_not_misclassified_as_product_code(self) -> None:
        decision = classify_ci_failure(
            job_name="pytest collection",
            log_text="collection error: invalid fixture definition",
        )
        self.assertEqual(decision.kind, "test_defect")
        self.assertEqual(decision.owner, "test-maintainer-agent")
        self.assertFalse(decision.product_code_write_allowed)


if __name__ == "__main__":
    unittest.main()
