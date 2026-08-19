from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPT = ROOT / "scripts" / "github_failure_ingest.py"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from autonomy_grant import (  # noqa: E402
    AutonomyGrantError,
    create_autonomy_grant,
    validate_autonomy_grant,
)
from task_run import TaskRunBindingError, TaskRunStore, stable_task_id  # noqa: E402


def _load_ingest():
    spec = importlib.util.spec_from_file_location("stage1_autonomy_ingest", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INGEST = _load_ingest()


class Stage1AutonomyBindingTests(unittest.TestCase):
    def _report(self) -> dict:
        return {
            "repository": "toctionyan/fristTest",
            "workflow_name": "quality",
            "workflow_run_id": "32157584844",
            "workflow_run_attempt": "1",
            "head_sha": "a" * 40,
            "head_branch": "feature/source-candidate",
            "repair_branch": "governed-repair/quality-32157584844",
            "failure_signature": "b" * 64,
            "classification": "code_or_contract",
            "repair_allowed": True,
            "same_repository": True,
            "failed_gates": [{"gate_id": "unit"}],
            "candidate_paths": ["services/agent-service/app/runtime.py"],
        }

    def _legacy_identity(self, report: dict) -> dict:
        return {
            "repository": report["repository"],
            "workflow_name": report["workflow_name"],
            "workflow_run_id": report["workflow_run_id"],
            "workflow_run_attempt": report["workflow_run_attempt"],
            "head_sha": report["head_sha"],
            "failure_signature": report["failure_signature"],
        }

    def test_stage1_keeps_historical_task_id_but_enriches_single_immutable_binding(self) -> None:
        with TemporaryDirectory() as directory:
            report = self._report()
            task_path = Path(directory) / "task-run.json"
            INGEST._create_task_run(report, task_path)
            task = json.loads(task_path.read_text(encoding="utf-8"))

            self.assertEqual(
                task["task_id"],
                stable_task_id("github-repair", self._legacy_identity(report)),
            )
            self.assertEqual(task["binding"]["branch"], report["repair_branch"])
            self.assertEqual(task["binding"]["base_sha"], report["head_sha"])
            for key, value in self._legacy_identity(report).items():
                self.assertEqual(task["binding"][key], value)

    def test_m2_autonomy_grant_consumes_exact_stage1_task_without_second_task_owner(self) -> None:
        with TemporaryDirectory() as directory:
            report = self._report()
            task_path = Path(directory) / "task-run.json"
            INGEST._create_task_run(report, task_path)
            task = json.loads(task_path.read_text(encoding="utf-8"))

            grant = create_autonomy_grant(
                task=task,
                repository=report["repository"],
                branch=report["repair_branch"],
                base_sha=report["head_sha"],
                issued_by="repository-owner",
                allowed_actions=["analyze_failure", "dispatch_ci"],
            )
            validated = validate_autonomy_grant(
                grant,
                task=task,
                repository=report["repository"],
                branch=report["repair_branch"],
                base_sha=report["head_sha"],
            )
            self.assertEqual(validated["task_id"], task["task_id"])
            self.assertFalse(validated["write_authority_effect"])
            self.assertFalse(validated["test_authority_effect"])
            self.assertFalse(validated["merge_allowed"])
            self.assertFalse(validated["deploy_allowed"])
            self.assertFalse(validated["production_closed"])

            with self.assertRaises(AutonomyGrantError):
                validate_autonomy_grant(
                    grant,
                    task=task,
                    repository=report["repository"],
                    branch="governed-repair/other-run",
                    base_sha=report["head_sha"],
                )
            with self.assertRaises(AutonomyGrantError):
                validate_autonomy_grant(
                    grant,
                    task=task,
                    repository=report["repository"],
                    branch=report["repair_branch"],
                    base_sha="c" * 40,
                )

    def test_existing_legacy_binding_is_not_silently_mutated(self) -> None:
        with TemporaryDirectory() as directory:
            report = self._report()
            task_path = Path(directory) / "task-run.json"
            identity = self._legacy_identity(report)
            TaskRunStore.open_or_create(
                task_path,
                task_id=stable_task_id("github-repair", identity),
                task_kind="github-governed-repair",
                binding=identity,
                required_conditions=(
                    "failure_ingested",
                    "classification_complete",
                    "source_changed",
                    "validation_passed",
                    "draft_pr_published",
                    "governance_closed",
                    "baseline_accepted",
                    "exact_head_certified",
                    "ready_for_review",
                ),
            )
            with self.assertRaises(TaskRunBindingError):
                INGEST._create_task_run(report, task_path)

    def test_missing_autonomy_coordinates_fail_closed(self) -> None:
        report = self._report()
        report["repair_branch"] = ""
        with self.assertRaises(ValueError):
            INGEST._task_immutable_binding(report)

        report = self._report()
        report["head_sha"] = "not-a-sha"
        with self.assertRaises(ValueError):
            INGEST._task_immutable_binding(report)


if __name__ == "__main__":
    unittest.main()
