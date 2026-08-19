from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_repair_loop_controller as loop  # noqa: E402
from task_run import TaskRunStore, stable_task_id  # noqa: E402


SOURCE_PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
SOURCE_RUN_ID = "123"
SOURCE_RUN_ATTEMPT = "1"
SOURCE_HEAD = "a" * 40
FAILURE_SIGNATURE = "b" * 64
CANDIDATE_SHA = "c" * 40


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _continuation(*, max_repair_rounds: int, max_validation_retries: int) -> dict:
    payload = {
        "schema": "engineering-autonomy-continuation@1",
        "grant_id": "grant-autonomy-budget",
        "grant_sha256": "d" * 64,
        "authorization_id": "owner-autonomy:budget",
        "authorization_sha256": "e" * 64,
        "source_run_id": SOURCE_RUN_ID,
        "source_run_attempt": SOURCE_RUN_ATTEMPT,
        "source_head_sha": SOURCE_HEAD,
        "failure_signature": FAILURE_SIGNATURE,
        "max_repair_rounds": max_repair_rounds,
        "max_validation_retries": max_validation_retries,
        "write_authority_effect": False,
        "test_authority_effect": False,
        "merge_allowed": False,
        "deploy_allowed": False,
        "production_closed": False,
    }
    payload["continuation_sha256"] = _digest(payload)
    return payload


def _failure() -> dict:
    return {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": "acme/repo",
        "workflow_name": "quality",
        "workflow_run_id": SOURCE_RUN_ID,
        "workflow_run_attempt": SOURCE_RUN_ATTEMPT,
        "head_sha": SOURCE_HEAD,
        "failure_signature": FAILURE_SIGNATURE,
        "repair_allowed": True,
        "same_repository": True,
        "classification": "code_or_contract",
        "candidate_paths": [SOURCE_PATH],
        "source_changed_files": [SOURCE_PATH, "tests/runtime/test_contract.py"],
        "failed_gates": [],
        "failure_summary": "initial failure",
        "head_branch": "feature/test",
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/test",
    }


def _task_run(path: Path, failure: dict) -> None:
    binding = {
        "repository": failure["repository"],
        "workflow_name": failure["workflow_name"],
        "workflow_run_id": failure["workflow_run_id"],
        "workflow_run_attempt": failure["workflow_run_attempt"],
        "head_sha": failure["head_sha"],
        "failure_signature": failure["failure_signature"],
    }
    task = TaskRunStore.open_or_create(
        path,
        task_id=stable_task_id("github-repair", binding),
        task_kind="github-governed-repair",
        binding=binding,
        required_conditions=(
            "failure_ingested",
            "classification_complete",
            "source_changed",
            "validation_passed",
            "draft_pr_published",
        ),
    )
    task.checkpoint(
        status="RUNNING",
        phase="FAILURE_INGESTED",
        workspace_fingerprint=None,
        evidence_refs=["failure-case.json"],
    )
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE3_VALIDATION_REQUIRED",
        workspace_fingerprint="seed",
        evidence_refs=["repair.patch"],
    )
    task.checkpoint(
        status="VALIDATING",
        phase="STAGE3_CANDIDATE_PREPARED",
        workspace_fingerprint="tree",
        evidence_refs=["stage3-plan.json"],
    )


def _write_inputs(
    root: Path,
    *,
    repair_round: int,
    max_repair_rounds: int,
    max_validation_retries: int,
    timed_out: bool = False,
) -> dict[str, Path]:
    failure = _failure()
    task_path = root / "task-run.json"
    _task_run(task_path, failure)
    stage2 = {
        "schema": "github-governed-repair-stage2@1",
        "status": "REPAIR_CANDIDATE_READY",
        "repository": failure["repository"],
        "workflow_run_id": failure["workflow_run_id"],
        "workflow_run_attempt": failure["workflow_run_attempt"],
        "head_sha": failure["head_sha"],
        "failure_signature": failure["failure_signature"],
        "patch_sha256": "f" * 64,
        "repair_round": repair_round,
        "autonomy_continuation": _continuation(
            max_repair_rounds=max_repair_rounds,
            max_validation_retries=max_validation_retries,
        ),
    }
    plan = {
        "schema": "github-governed-repair-stage3@1",
        "status": "CANDIDATE_PREPARED",
        "source_run_id": failure["workflow_run_id"],
        "head_sha": failure["head_sha"],
        "patch_sha256": stage2["patch_sha256"],
        "candidate_sha": CANDIDATE_SHA,
        "validated_tree_sha": "1" * 40,
    }
    targeted = {
        "schema": "github-governed-repair-stage3@1",
        "status": "TARGETED_VALIDATION_FAILED",
        "candidate_sha": CANDIDATE_SHA,
        "results": [
            {
                "component": "agent-python",
                "exit_code": None if timed_out else 1,
                "passed": False,
                "timed_out": timed_out,
                "stdout": "" if timed_out else f'File "{SOURCE_PATH}", line 8\nRuntimeError: wrong state',
                "stderr": "",
            }
        ],
    }
    paths = {
        "task": task_path,
        "failure": root / "failure-case.json",
        "stage2": root / "stage2-result.json",
        "plan": root / "stage3-plan.json",
        "targeted": root / "targeted-result.json",
        "patch": root / "repair.patch",
    }
    paths["failure"].write_text(json.dumps(failure), encoding="utf-8")
    paths["stage2"].write_text(json.dumps(stage2), encoding="utf-8")
    paths["plan"].write_text(json.dumps(plan), encoding="utf-8")
    paths["targeted"].write_text(json.dumps(targeted), encoding="utf-8")
    paths["patch"].write_text("diff --git a/x b/x\n", encoding="utf-8")
    return paths


class EngineeringAutonomyLoopBudgetTests(unittest.TestCase):
    def test_lower_autonomy_repair_budget_stops_before_global_round_eight(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = _write_inputs(
                root,
                repair_round=2,
                max_repair_rounds=2,
                max_validation_retries=1,
            )
            state = loop.route_failure(
                task_run_path=inputs["task"],
                stage2_result_path=inputs["stage2"],
                stage3_plan_path=inputs["plan"],
                targeted_result_path=inputs["targeted"],
                quick_summary_path=None,
                validation_result_path=None,
                original_failure_path=inputs["failure"],
                seed_patch_path=inputs["patch"],
                output_dir=root / "out",
                stage3_run_id="9001",
                stage3_run_attempt=1,
            )
            self.assertEqual(state["max_repair_rounds"], 2)
            self.assertEqual(state["action"], "STOP_MAX_REPAIR_ROUNDS")
            self.assertEqual(state["repair_budget_remaining"], 0)
            self.assertEqual(
                state["autonomy_continuation"]["continuation_sha256"],
                _continuation(max_repair_rounds=2, max_validation_retries=1)[
                    "continuation_sha256"
                ],
            )

    def test_lower_autonomy_validation_retry_budget_survives_outer_loop(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = _write_inputs(
                root,
                repair_round=1,
                max_repair_rounds=2,
                max_validation_retries=1,
                timed_out=True,
            )
            first_out = root / "first"
            first = loop.route_failure(
                task_run_path=inputs["task"],
                stage2_result_path=inputs["stage2"],
                stage3_plan_path=inputs["plan"],
                targeted_result_path=inputs["targeted"],
                quick_summary_path=None,
                validation_result_path=None,
                original_failure_path=inputs["failure"],
                seed_patch_path=inputs["patch"],
                output_dir=first_out,
                stage3_run_id="9101",
                stage3_run_attempt=1,
            )
            self.assertEqual(first["action"], "RETRY_VALIDATION_SAME_CANDIDATE")
            self.assertEqual(first["same_candidate_retry_count"], 1)
            self.assertEqual(first["max_validation_retries_per_candidate"], 1)

            second = loop.route_failure(
                task_run_path=first_out / "task-run.json",
                stage2_result_path=inputs["stage2"],
                stage3_plan_path=inputs["plan"],
                targeted_result_path=inputs["targeted"],
                quick_summary_path=None,
                validation_result_path=None,
                original_failure_path=inputs["failure"],
                seed_patch_path=inputs["patch"],
                output_dir=root / "second",
                stage3_run_id="9102",
                stage3_run_attempt=2,
                previous_state_path=first_out / "loop-state.json",
            )
            self.assertEqual(second["same_candidate_retry_count"], 2)
            self.assertEqual(second["max_validation_retries_per_candidate"], 1)
            self.assertEqual(second["action"], "VALIDATION_RETRY_EXHAUSTED")

    def test_two_round_product_loop_uses_durable_task_state_without_previous_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = _write_inputs(
                root,
                repair_round=1,
                max_repair_rounds=2,
                max_validation_retries=1,
            )
            first_out = root / "round-1"
            first = loop.route_failure(
                task_run_path=inputs["task"],
                stage2_result_path=inputs["stage2"],
                stage3_plan_path=inputs["plan"],
                targeted_result_path=inputs["targeted"],
                quick_summary_path=None,
                validation_result_path=None,
                original_failure_path=inputs["failure"],
                seed_patch_path=inputs["patch"],
                output_dir=first_out,
                stage3_run_id="9201",
                stage3_run_attempt=1,
            )
            self.assertEqual(first["action"], "DISPATCH_REPAIR")
            self.assertEqual(first["next_repair_round"], 2)
            feedback = json.loads((first_out / "failure-case.json").read_text(encoding="utf-8"))
            self.assertEqual(feedback["candidate_paths"], [SOURCE_PATH])
            self.assertEqual(
                feedback["loop_feedback"]["autonomy_continuation_sha256"],
                first["autonomy_continuation"]["continuation_sha256"],
            )

            stage2_round2 = json.loads(inputs["stage2"].read_text(encoding="utf-8"))
            stage2_round2["repair_round"] = 2
            round2_stage2 = root / "stage2-round-2.json"
            round2_stage2.write_text(json.dumps(stage2_round2), encoding="utf-8")

            second = loop.route_failure(
                task_run_path=first_out / "task-run.json",
                stage2_result_path=round2_stage2,
                stage3_plan_path=inputs["plan"],
                targeted_result_path=inputs["targeted"],
                quick_summary_path=None,
                validation_result_path=None,
                original_failure_path=first_out / "failure-case.json",
                seed_patch_path=inputs["patch"],
                output_dir=root / "round-2",
                stage3_run_id="9202",
                stage3_run_attempt=1,
            )
            self.assertEqual(second["repair_round"], 2)
            self.assertEqual(second["max_repair_rounds"], 2)
            self.assertEqual(second["action"], "STOP_MAX_REPAIR_ROUNDS")
            self.assertEqual(second["repair_budget_remaining"], 0)
            self.assertEqual(
                second["autonomy_continuation"]["continuation_sha256"],
                first["autonomy_continuation"]["continuation_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
