from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_repair_loop_controller as loop  # noqa: E402
import github_stage2_handoff as handoff  # noqa: E402
from task_run import TaskRunStore, stable_task_id  # noqa: E402


SOURCE_PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"


def _failure(*, signature: str = "b" * 64) -> dict:
    return {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": "acme/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "1",
        "head_sha": "a" * 40,
        "head_branch": "feature/test",
        "source_pr_number": 44,
        "failure_signature": signature,
        "classification": "code_or_contract",
        "repair_allowed": True,
        "same_repository": True,
        "candidate_paths": [SOURCE_PATH],
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/test",
        "source_changed_files": [SOURCE_PATH, "tests/runtime/test_contract.py"],
        "failed_gates": [{"gate_id": "quality", "status": "FAIL"}],
        "failure_summary": "sensitive failure diagnostics that must not become authority",
    }


def _task(path: Path, failure: dict) -> None:
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


def _bound_stage2(tmp_path: Path, failure: dict) -> dict:
    failure_path = tmp_path / "fresh-failure.json"
    result_path = tmp_path / "stage2-result.json"
    patch_path = tmp_path / "repair.patch"
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    result_path.write_text(
        json.dumps(
            {
                "schema": "github-governed-repair-stage2@1",
                "status": "REPAIR_CANDIDATE_READY",
                "workflow_run_id": failure["workflow_run_id"],
                "head_sha": failure["head_sha"],
                "failure_signature": failure["failure_signature"],
                "repair_round": 1,
            }
        ),
        encoding="utf-8",
    )
    patch_path.write_text(
        "diff --git a/services/agent-service/src/agent_core/lifecycle/goal_planning.py "
        "b/services/agent-service/src/agent_core/lifecycle/goal_planning.py\n",
        encoding="utf-8",
    )
    return handoff.bind_handoff(
        failure_path=failure_path,
        result_path=result_path,
        patch_path=patch_path,
    )


def test_stage2_handoff_binds_minimal_exact_source_authority(tmp_path: Path) -> None:
    failure = _failure()
    bound = _bound_stage2(tmp_path, failure)

    authority = bound["source_failure_authority"]
    assert authority["authority_schema"] == handoff.SOURCE_AUTHORITY_SCHEMA
    assert authority["failure_signature"] == failure["failure_signature"]
    assert authority["candidate_paths"] == [SOURCE_PATH]
    assert authority["repair_branch"] == failure["repair_branch"]
    assert authority["repair_base_branch"] == failure["repair_base_branch"]
    assert "failure_summary" not in authority
    assert "failed_gates" not in authority
    assert "source_changed_files" not in authority
    assert len(bound["source_failure_authority_sha256"]) == 64


def test_outer_loop_uses_stage2_bound_authority_not_stale_coordinator_artifact(tmp_path: Path) -> None:
    failure = _failure()
    stage2 = _bound_stage2(tmp_path, failure)
    task_path = tmp_path / "task-run.json"
    _task(task_path, failure)

    stage2_path = tmp_path / "stage2-bound.json"
    stage2_path.write_text(json.dumps(stage2), encoding="utf-8")
    plan_path = tmp_path / "stage3-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema": "github-governed-repair-stage3@1",
                "status": "CANDIDATE_PREPARED",
                "source_run_id": failure["workflow_run_id"],
                "head_sha": failure["head_sha"],
                "patch_sha256": stage2["patch_sha256"],
                "candidate_sha": "c" * 40,
                "validated_tree_sha": "d" * 40,
            }
        ),
        encoding="utf-8",
    )
    targeted_path = tmp_path / "targeted-result.json"
    targeted_path.write_text(
        json.dumps(
            {
                "schema": "github-governed-repair-stage3@1",
                "status": "TARGETED_VALIDATION_FAILED",
                "candidate_sha": "c" * 40,
                "results": [
                    {
                        "component": "agent-python",
                        "exit_code": 1,
                        "passed": False,
                        "timed_out": False,
                        "stdout": (
                            "FAILED tests/runtime/test_contract.py::test_semantic\n"
                            "AssertionError\nDiffering items:\nFull diff:"
                        ),
                        "stderr": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    stale = _failure(signature="e" * 64)
    stale_path = tmp_path / "stale-failure.json"
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    patch_path = tmp_path / "seed.patch"
    patch_path.write_text("diff --git a/x b/x\n", encoding="utf-8")

    state = loop.route_failure(
        task_run_path=task_path,
        stage2_result_path=stage2_path,
        stage3_plan_path=plan_path,
        targeted_result_path=targeted_path,
        quick_summary_path=None,
        validation_result_path=None,
        original_failure_path=stale_path,
        seed_patch_path=patch_path,
        output_dir=tmp_path / "out",
        stage3_run_id="9001",
        stage3_run_attempt=1,
    )

    assert state["failure_signature"] == failure["failure_signature"]
    assert state["repair_round"] == 1
    assert state["action"] == "TEST_CONTRACT_REVIEW_REQUIRED"
    assert state["next_repair_round"] is None
    assert not (tmp_path / "out" / "failure-case.json").exists()


def test_outer_loop_fails_closed_when_bound_authority_digest_is_tampered(tmp_path: Path) -> None:
    failure = _failure()
    stage2 = _bound_stage2(tmp_path, failure)
    tampered = copy.deepcopy(stage2)
    tampered["source_failure_authority"]["candidate_paths"] = [
        "services/agent-service/src/agent_core/modules/registry.py"
    ]

    with pytest.raises(loop.RepairLoopError, match="authority digest mismatch"):
        loop._resolve_original_failure(stage2=tampered, fallback=_failure(signature="e" * 64))


def test_legacy_stage2_without_bound_authority_keeps_fail_closed_fallback_behavior() -> None:
    fallback = _failure()
    stage2 = {"schema": "github-governed-repair-stage2@1"}
    assert loop._resolve_original_failure(stage2=stage2, fallback=fallback) is fallback
