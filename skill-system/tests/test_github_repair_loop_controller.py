from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_repair_loop_controller as loop  # noqa: E402
from task_run import TaskRunStore, stable_task_id  # noqa: E402


def _failure(path: str) -> dict:
    return {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": "acme/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "1",
        "head_sha": "a" * 40,
        "failure_signature": "b" * 64,
        "repair_allowed": True,
        "same_repository": True,
        "classification": "code_or_contract",
        "candidate_paths": [path],
        "source_changed_files": [path, "tests/runtime/test_contract.py"],
        "failed_gates": [],
        "failure_summary": "initial failure",
        "head_branch": "feature/test",
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/test",
    }


def _targeted(text: str, *, timed_out: bool = False) -> dict:
    return {
        "schema": "github-governed-repair-stage3@1",
        "status": "TARGETED_VALIDATION_FAILED",
        "candidate_sha": "c" * 40,
        "results": [
            {
                "component": "agent-python",
                "exit_code": None if timed_out else 1,
                "passed": False,
                "timed_out": timed_out,
                "stdout": text,
                "stderr": "",
            }
        ],
    }


def test_semantic_assertion_without_source_path_requires_contract_review() -> None:
    failure = _failure("services/agent-service/src/agent_core/lifecycle/goal_planning.py")
    targeted = _targeted(
        "FAILED tests/runtime/test_contract.py::test_semantic\n"
        "AssertionError\nDiffering items:\nFull diff:"
    )
    failure_class, paths, reason = loop.classify_targeted_failure(
        targeted,
        original_failure=failure,
    )
    assert failure_class == "TEST_CONTRACT_REVIEW_REQUIRED"
    assert paths == []
    assert "oracle/semantic assertion mismatch" in reason


def test_product_failure_requires_exact_governed_source_evidence() -> None:
    path = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    failure = _failure(path)
    targeted = _targeted(
        "Traceback (most recent call last):\n"
        f"  File \"{path}\", line 42, in validate_goal\n"
        "RuntimeError: invalid goal state"
    )
    failure_class, paths, _reason = loop.classify_targeted_failure(
        targeted,
        original_failure=failure,
    )
    assert failure_class == "PRODUCT_SOURCE_FAILURE"
    assert paths == [path]


def test_harness_and_timeout_do_not_become_product_repairs() -> None:
    failure = _failure("services/agent-service/src/agent_core/lifecycle/goal_planning.py")
    harness_class, harness_paths, _ = loop.classify_targeted_failure(
        _targeted("RuntimeError: APP_PROFILE is required. Set APP_PROFILE=local"),
        original_failure=failure,
    )
    timeout_class, timeout_paths, _ = loop.classify_targeted_failure(
        _targeted("", timed_out=True),
        original_failure=failure,
    )
    assert harness_class == "HARNESS_FAILURE"
    assert harness_paths == []
    assert timeout_class == "TRANSIENT_INFRA_FAILURE"
    assert timeout_paths == []


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


def _bound_inputs(tmp_path: Path, *, source_path: str, targeted_text: str) -> dict[str, Path]:
    failure = _failure(source_path)
    task_path = tmp_path / "task-run.json"
    _task_run(task_path, failure)
    stage2 = {
        "schema": "github-governed-repair-stage2@1",
        "status": "REPAIR_CANDIDATE_READY",
        "repository": failure["repository"],
        "workflow_run_id": failure["workflow_run_id"],
        "head_sha": failure["head_sha"],
        "failure_signature": failure["failure_signature"],
        "patch_sha256": "d" * 64,
        "repair_round": 1,
    }
    plan = {
        "schema": "github-governed-repair-stage3@1",
        "status": "CANDIDATE_PREPARED",
        "source_run_id": failure["workflow_run_id"],
        "head_sha": failure["head_sha"],
        "patch_sha256": stage2["patch_sha256"],
        "candidate_sha": "c" * 40,
        "validated_tree_sha": "e" * 40,
    }
    paths = {
        "task": task_path,
        "failure": tmp_path / "failure-case.json",
        "stage2": tmp_path / "stage2-result.json",
        "plan": tmp_path / "stage3-plan.json",
        "targeted": tmp_path / "targeted-result.json",
        "patch": tmp_path / "repair.patch",
    }
    paths["failure"].write_text(json.dumps(failure), encoding="utf-8")
    paths["stage2"].write_text(json.dumps(stage2), encoding="utf-8")
    paths["plan"].write_text(json.dumps(plan), encoding="utf-8")
    paths["targeted"].write_text(json.dumps(_targeted(targeted_text)), encoding="utf-8")
    paths["patch"].write_text("diff --git a/x b/x\n", encoding="utf-8")
    return paths


def test_product_failure_dispatches_round_two_without_counting_workflow_attempt(tmp_path: Path) -> None:
    source_path = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    inputs = _bound_inputs(
        tmp_path,
        source_path=source_path,
        targeted_text=f'File "{source_path}", line 8\nRuntimeError: wrong state',
    )
    output = tmp_path / "out"
    state = loop.route_failure(
        task_run_path=inputs["task"],
        stage2_result_path=inputs["stage2"],
        stage3_plan_path=inputs["plan"],
        targeted_result_path=inputs["targeted"],
        original_failure_path=inputs["failure"],
        seed_patch_path=inputs["patch"],
        output_dir=output,
        stage3_run_id="9001",
        stage3_run_attempt=5,
    )
    assert state["repair_round"] == 1
    assert state["next_repair_round"] == 2
    assert state["verification_attempt"] == 5
    assert state["action"] == "DISPATCH_REPAIR"
    assert state["repair_budget_remaining"] == 7
    feedback = json.loads((output / "failure-case.json").read_text(encoding="utf-8"))
    assert feedback["candidate_paths"] == [source_path]
    assert feedback["loop_feedback"]["next_repair_round"] == 2
    assert (output / "seed.patch").is_file()
    persisted_task = json.loads((output / "task-run.json").read_text(encoding="utf-8"))
    assert persisted_task["status"] == "FAILED_RECOVERABLE"
    assert persisted_task["metadata"]["repair_loop"]["repair_round"] == 1


def test_contract_review_blocks_automatic_product_mutation(tmp_path: Path) -> None:
    source_path = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    inputs = _bound_inputs(
        tmp_path,
        source_path=source_path,
        targeted_text=(
            "FAILED tests/runtime/test_contract.py::test_rule\n"
            "AssertionError\nDiffering items:\nFull diff:"
        ),
    )
    output = tmp_path / "out"
    state = loop.route_failure(
        task_run_path=inputs["task"],
        stage2_result_path=inputs["stage2"],
        stage3_plan_path=inputs["plan"],
        targeted_result_path=inputs["targeted"],
        original_failure_path=inputs["failure"],
        seed_patch_path=inputs["patch"],
        output_dir=output,
        stage3_run_id="9002",
        stage3_run_attempt=1,
    )
    assert state["repair_round"] == 1
    assert state["next_repair_round"] is None
    assert state["action"] == "TEST_CONTRACT_REVIEW_REQUIRED"
    assert not (output / "failure-case.json").exists()
    persisted_task = json.loads((output / "task-run.json").read_text(encoding="utf-8"))
    assert persisted_task["status"] == "BLOCKED"
    assert persisted_task["phase"] == "TEST_CONTRACT_REVIEW_REQUIRED"
