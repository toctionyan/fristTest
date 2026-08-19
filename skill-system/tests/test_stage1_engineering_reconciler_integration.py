from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
INGEST_SCRIPT = ROOT / "scripts" / "github_failure_ingest.py"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import bind_autonomy_grant, create_autonomy_grant  # noqa: E402
import engineering_task_controller as engineering_controller  # noqa: E402
from task_run import TaskRunStore  # noqa: E402


def _load_ingest():
    spec = importlib.util.spec_from_file_location("stage1_engineering_ingest", INGEST_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INGEST = _load_ingest()
REPOSITORY = "toctionyan/fristTest"
HEAD_SHA = "a" * 40
SOURCE_RUN_ID = "32157584844"
SOURCE_RUN_ATTEMPT = "1"
REPAIR_BRANCH = f"governed-repair/quality-{SOURCE_RUN_ID}"


class Stage1EngineeringReconcilerIntegrationTests(unittest.TestCase):
    def _report(self, *, classification: str = "code_or_contract", repair_allowed: bool = True) -> dict:
        failed_gates = [{"gate_id": "unit", "status": "FAIL"}]
        candidate_paths = ["services/agent-service/app/runtime.py"] if repair_allowed else []
        return {
            "schema": "github-failure-ingest@1",
            "status": "INGESTED",
            "repository": REPOSITORY,
            "workflow_name": "quality",
            "workflow_run_id": SOURCE_RUN_ID,
            "workflow_run_attempt": SOURCE_RUN_ATTEMPT,
            "head_sha": HEAD_SHA,
            "head_branch": "feature/source-candidate",
            "repair_branch": REPAIR_BRANCH,
            "failure_signature": "b" * 64,
            "classification": classification,
            "repair_allowed": repair_allowed,
            "same_repository": True,
            "failed_gates": failed_gates,
            "candidate_paths": candidate_paths,
            "production_closed": False,
        }

    def _store_and_grant(self, root: Path, report: dict):
        task_path = root / "task-run.json"
        INGEST._create_task_run(report, task_path)
        store = TaskRunStore(task_path, json.loads(task_path.read_text(encoding="utf-8")))
        grant = create_autonomy_grant(
            task=store.payload,
            repository=REPOSITORY,
            branch=REPAIR_BRANCH,
            base_sha=HEAD_SHA,
            issued_by="repository-owner",
            allowed_actions=[
                "analyze_failure",
                "retry_transient_ci",
                "repair_meaningful_product_red",
                "dispatch_ci",
            ],
        )
        bind_autonomy_grant(
            store,
            grant,
            repository=REPOSITORY,
            owner_authorization_ref="github-owner-workflow-dispatch:stage1-integration",
        )
        return store, grant

    def test_stage1_task_enters_same_engineering_controller_without_second_task_owner(self) -> None:
        self.assertTrue(
            hasattr(engineering_controller, "reconcile_stage1_failure"),
            "M3 controller lacks a Stage-1 reconciliation entrypoint for the existing Stage-1 TaskRun",
        )
        with TemporaryDirectory() as directory:
            report = self._report()
            store, grant = self._store_and_grant(Path(directory), report)
            result = engineering_controller.reconcile_stage1_failure(
                store,
                grant,
                repository=REPOSITORY,
                failure_case=report,
                current_head_sha=HEAD_SHA,
            )
            self.assertEqual(result["decision"], "REPAIR_PRODUCT")
            self.assertEqual(result["action"], "repair_meaningful_product_red")
            self.assertTrue(result["allowed"])
            self.assertFalse(result["human_required"])
            self.assertEqual(result["failure_class"], "PRODUCT_SOURCE_FAILURE")
            self.assertTrue(result["product_write_allowed"])
            self.assertFalse(result["merge_allowed"])
            self.assertFalse(result["deploy_allowed"])
            self.assertFalse(result["production_closed"])
            self.assertEqual(store.payload["task_kind"], "github-governed-repair")

    def test_environment_stage1_failure_retries_exact_same_candidate_without_write_authority(self) -> None:
        self.assertTrue(hasattr(engineering_controller, "reconcile_stage1_failure"))
        with TemporaryDirectory() as directory:
            report = self._report(classification="environment", repair_allowed=False)
            store, grant = self._store_and_grant(Path(directory), report)
            result = engineering_controller.reconcile_stage1_failure(
                store,
                grant,
                repository=REPOSITORY,
                failure_case=report,
                current_head_sha=HEAD_SHA,
            )
            self.assertEqual(result["decision"], "RETRY_CI")
            self.assertEqual(result["action"], "retry_transient_ci")
            self.assertTrue(result["allowed"])
            self.assertFalse(result["human_required"])
            self.assertEqual(result["failure_class"], "ENVIRONMENT_FAILURE")
            self.assertFalse(result["product_write_allowed"])

    def test_stage1_reconciliation_fails_closed_on_current_head_drift(self) -> None:
        self.assertTrue(hasattr(engineering_controller, "reconcile_stage1_failure"))
        with TemporaryDirectory() as directory:
            report = self._report()
            store, grant = self._store_and_grant(Path(directory), report)
            with self.assertRaises(Exception):
                engineering_controller.reconcile_stage1_failure(
                    store,
                    grant,
                    repository=REPOSITORY,
                    failure_case=report,
                    current_head_sha="c" * 40,
                )


if __name__ == "__main__":
    unittest.main()
