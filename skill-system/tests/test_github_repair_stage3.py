from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from task_run import TaskRunStore, stable_task_id


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STAGE3 = _load("github_repair_stage3", SCRIPTS / "github_repair_stage3.py")
COMPLETE = _load("github_repair_stage3_complete", SCRIPTS / "github_repair_stage3_complete.py")
HANDOFF = _load("github_stage2_handoff", SCRIPTS / "github_stage2_handoff.py")
TREE = _load("github_repair_stage3_tree", SCRIPTS / "github_repair_stage3_tree.py")
PUBLISH = _load("github_repair_stage3_publish", SCRIPTS / "github_repair_stage3_publish.py")


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=path, text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def _fixture(tmp_path: Path):
    workspace = tmp_path / "candidate"
    source = workspace / "services" / "agent-service" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 0\n", encoding="utf-8")
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "baseline")
    head_sha = _git(workspace, "rev-parse", "HEAD")

    source.write_text("value = 1\n", encoding="utf-8")
    patch_text = _git(workspace, "diff", "--no-ext-diff", "--binary") + "\n"
    patch_path = tmp_path / "repair.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    _git(workspace, "reset", "--hard", "HEAD")

    failure = {
        "schema": "github-failure-ingest@1",
        "repository": "owner/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "1",
        "head_sha": head_sha,
        "head_branch": "feature/source",
        "source_pr_number": 7,
        "failure_signature": "f" * 64,
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/source",
    }
    failure_path = tmp_path / "failure-case.json"
    failure_path.write_text(json.dumps(failure), encoding="utf-8")

    stage2 = {
        "schema": "github-governed-repair-stage2@1",
        "status": "REPAIR_CANDIDATE_READY",
        "workflow_run_id": "123",
        "head_sha": head_sha,
        "failure_signature": "f" * 64,
        "changed_paths": ["services/agent-service/app.py"],
        "deterministic_file_verification_passed": True,
        "full_validation_passed": False,
        "draft_pr_published": False,
        "production_closed": False,
    }
    stage2_path = tmp_path / "repair-result.json"
    stage2_path.write_text(json.dumps(stage2), encoding="utf-8")
    HANDOFF.bind_handoff(
        failure_path=failure_path,
        result_path=stage2_path,
        patch_path=patch_path,
    )

    binding = {
        "repository": failure["repository"],
        "workflow_name": failure["workflow_name"],
        "workflow_run_id": failure["workflow_run_id"],
        "workflow_run_attempt": failure["workflow_run_attempt"],
        "head_sha": failure["head_sha"],
        "failure_signature": failure["failure_signature"],
    }
    task_path = tmp_path / "task-run.json"
    task = TaskRunStore.open_or_create(
        task_path,
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
        evidence_refs=[str(failure_path)],
    )
    task.mark_condition("failure_ingested", evidence_refs=[str(failure_path)])
    task.mark_condition("classification_complete", evidence_refs=["code_or_contract"])
    task.mark_condition("source_changed", evidence_refs=[str(patch_path)])
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE3_VALIDATION_REQUIRED",
        workspace_fingerprint=None,
        evidence_refs=[str(stage2_path), str(patch_path)],
    )
    return workspace, source, stage2_path, patch_path, task_path


def _prepare_bound_plan(tmp_path: Path):
    workspace, source, stage2_path, patch_path, task_path = _fixture(tmp_path)
    plan_path = tmp_path / "stage3-plan.json"
    STAGE3.prepare_candidate(
        workspace=workspace,
        result_path=stage2_path,
        task_run_path=task_path,
        patch_path=patch_path,
        plan_path=plan_path,
    )
    plan = TREE.bind_tree(workspace=workspace, plan_path=plan_path)
    return workspace, source, stage2_path, patch_path, task_path, plan_path, plan


def test_stage2_handoff_binds_patch_and_branch_metadata(tmp_path: Path) -> None:
    _workspace, _source, stage2_path, patch_path, _task_path = _fixture(tmp_path)
    result = json.loads(stage2_path.read_text(encoding="utf-8"))
    assert result["stage3_handoff_bound"] is True
    assert result["patch_sha256"] == hashlib.sha256(patch_path.read_bytes()).hexdigest()
    assert result["repair_branch"] == "governed-repair/quality-123"
    assert result["repair_base_branch"] == "feature/source"


def test_stage3_prepare_applies_exact_patch_and_binds_tree(tmp_path: Path) -> None:
    workspace, source, _stage2, _patch, task_path, _plan_path, plan = _prepare_bound_plan(tmp_path)
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    assert plan["status"] == "CANDIDATE_PREPARED"
    assert plan["targeted_components"] == ["agent-python"]
    assert plan["tree_binding_complete"] is True
    assert plan["validated_parent_sha"] == plan["head_sha"]
    assert plan["derived_paths"] == []
    assert plan["publication_paths"] == plan["changed_paths"]
    assert _git(workspace, "rev-parse", "HEAD^{tree}") == plan["validated_tree_sha"]
    assert _git(workspace, "status", "--porcelain") == ""
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert task["status"] == "VALIDATING"
    assert task["phase"] == "STAGE3_CANDIDATE_PREPARED"


def test_stage3_rejects_patch_digest_mismatch(tmp_path: Path) -> None:
    _workspace, _source, stage2_path, patch_path, task_path = _fixture(tmp_path)
    patch_path.write_text(patch_path.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
    with pytest.raises(STAGE3.Stage3Error, match="digest mismatch"):
        STAGE3.inspect_handoff(
            result_path=stage2_path,
            task_run_path=task_path,
            patch_path=patch_path,
        )


def test_stage3_rejects_patch_path_scope_mismatch(tmp_path: Path) -> None:
    workspace, _source, stage2_path, patch_path, task_path = _fixture(tmp_path)
    result = json.loads(stage2_path.read_text(encoding="utf-8"))
    result["changed_paths"] = ["services/business-service/app.py"]
    stage2_path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(STAGE3.Stage3Error):
        STAGE3.prepare_candidate(
            workspace=workspace,
            result_path=stage2_path,
            task_run_path=task_path,
            patch_path=patch_path,
            plan_path=tmp_path / "plan.json",
        )


def test_quick_evidence_requires_current_completion_eligible_pass(tmp_path: Path) -> None:
    summary_path = tmp_path / "run-summary.json"
    summary = {
        "mode": "quick",
        "run_kind": "verification",
        "decision": "PASS",
        "loop_status": "CI_VERIFIED",
        "completion_eligible": True,
        "required_gate_ids": ["unit"],
        "results": [{"id": "unit", "status": "PASS"}],
        "workspace_snapshot_fingerprint": "s" * 64,
    }
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    assert STAGE3.validate_quick_evidence(summary_path)["decision"] == "PASS"
    summary["completion_eligible"] = False
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(STAGE3.Stage3Error):
        STAGE3.validate_quick_evidence(summary_path)


def test_privileged_publisher_recreates_same_validated_tree(tmp_path: Path) -> None:
    validation_workspace, _source, _stage2, patch_path, _task, plan_path, plan = _prepare_bound_plan(tmp_path)
    validation = {
        "schema": "github-governed-repair-stage3@1",
        "status": "VALIDATED_FOR_DRAFT_PR",
        "source_run_id": plan["source_run_id"],
        "head_sha": plan["head_sha"],
        "candidate_sha": plan["candidate_sha"],
        "repair_branch": plan["repair_branch"],
        "repair_base_branch": plan["repair_base_branch"],
        "changed_paths": plan["changed_paths"],
        "targeted_validation_passed": True,
        "full_validation_passed": True,
        "quick_loop_status": "CI_VERIFIED",
        "quick_workspace_snapshot_fingerprint": "s" * 64,
        "draft_pr_published": False,
        "production_closed": False,
    }
    validation_path = tmp_path / "validation-result.json"
    validation_path.write_text(json.dumps(validation), encoding="utf-8")

    publisher = tmp_path / "publisher"
    _git(tmp_path, "clone", str(validation_workspace), str(publisher))
    _git(publisher, "checkout", plan["head_sha"])
    output = tmp_path / "publication-commit.json"
    result = PUBLISH.prepare_publication(
        workspace=publisher,
        plan_path=plan_path,
        validation_path=validation_path,
        patch_path=patch_path,
        output_path=output,
    )
    assert result["validated_tree_sha"] == plan["validated_tree_sha"]
    assert result["derived_paths"] == []
    assert result["publication_paths"] == result["changed_paths"]
    assert _git(publisher, "rev-parse", "HEAD^{tree}") == plan["validated_tree_sha"]
    assert _git(publisher, "rev-parse", "HEAD^") == plan["head_sha"]
    assert result["published_candidate_sha"] != ""


def test_publisher_rejects_validation_tree_drift(tmp_path: Path) -> None:
    validation_workspace, _source, _stage2, patch_path, _task, plan_path, plan = _prepare_bound_plan(tmp_path)
    plan["validated_tree_sha"] = "0" * 40
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    validation = {
        "schema": "github-governed-repair-stage3@1",
        "status": "VALIDATED_FOR_DRAFT_PR",
        "source_run_id": plan["source_run_id"],
        "head_sha": plan["head_sha"],
        "candidate_sha": plan["candidate_sha"],
        "repair_branch": plan["repair_branch"],
        "repair_base_branch": plan["repair_base_branch"],
        "changed_paths": plan["changed_paths"],
        "targeted_validation_passed": True,
        "full_validation_passed": True,
        "quick_loop_status": "CI_VERIFIED",
        "draft_pr_published": False,
        "production_closed": False,
    }
    validation_path = tmp_path / "validation-result.json"
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    publisher = tmp_path / "publisher"
    _git(tmp_path, "clone", str(validation_workspace), str(publisher))
    _git(publisher, "checkout", plan["head_sha"])
    with pytest.raises(PUBLISH.PublicationError, match="tree mismatch"):
        PUBLISH.prepare_publication(
            workspace=publisher,
            plan_path=plan_path,
            validation_path=validation_path,
            patch_path=patch_path,
            output_path=tmp_path / "publication.json",
        )


def _green_ci_evidence(candidate_sha: str) -> dict:
    checks = {}
    for index, name in enumerate(COMPLETE.REQUIRED_PR_WORKFLOWS, start=1):
        checks[name] = {
            "run_id": 100 + index,
            "status": "completed",
            "conclusion": "success",
            "head_sha": candidate_sha,
            "event": "pull_request",
            "html_url": f"https://github.com/owner/repo/actions/runs/{100 + index}",
        }
    return {
        "status": "VERIFIED_GREEN",
        "closure_eligible": True,
        "continue_repair": False,
        "exit_reason": "VERIFIED_GREEN",
        "missing": [],
        "pending": [],
        "failed": [],
        "required_checks": checks,
        "production_closed": False,
    }


def test_stage3_publication_waits_for_exact_head_pr_ci_before_completion(tmp_path: Path) -> None:
    _workspace, _source, _stage2, _patch, task_path = _fixture(tmp_path)
    task = TaskRunStore(task_path, json.loads(task_path.read_text(encoding="utf-8")))
    task.checkpoint(
        status="VALIDATING",
        phase="STAGE3_CANDIDATE_PREPARED",
        workspace_fingerprint="a" * 64,
        evidence_refs=["plan"],
    )
    task.mark_condition("validation_passed", evidence_refs=["quick-summary"])
    task.checkpoint(
        status="WAITING_EXTERNAL_RESULT",
        phase="STAGE3_DRAFT_PR_REQUIRED",
        workspace_fingerprint="b" * 64,
        evidence_refs=["quick-summary"],
    )
    changed = ["services/agent-service/app.py"]
    validation = {
        "schema": "github-governed-repair-stage3@1",
        "status": "VALIDATED_FOR_DRAFT_PR",
        "source_run_id": "123",
        "head_sha": "h" * 40,
        "candidate_sha": "c" * 40,
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/source",
        "changed_paths": changed,
        "full_validation_passed": True,
        "quick_workspace_snapshot_fingerprint": "b" * 64,
        "draft_pr_published": False,
        "production_closed": False,
    }
    publication = {
        "schema": "github-governed-repair-stage3-publication@1",
        "status": "PUBLICATION_COMMIT_PREPARED",
        "source_run_id": "123",
        "source_head_sha": "h" * 40,
        "validated_candidate_sha": "c" * 40,
        "validated_tree_sha": "t" * 40,
        "published_candidate_sha": "p" * 40,
        "repair_branch": "governed-repair/quality-123",
        "repair_base_branch": "feature/source",
        "changed_paths": changed,
        "full_validation_passed": True,
        "draft_pr_published": False,
        "production_closed": False,
    }
    validation_path = tmp_path / "validation-result.json"
    publication_path = tmp_path / "publication-commit.json"
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    publication_path.write_text(json.dumps(publication), encoding="utf-8")
    output_path = tmp_path / "publication-result.json"

    pending = COMPLETE.complete_publication(
        validation_result_path=validation_path,
        publication_commit_path=publication_path,
        task_run_path=task_path,
        pr_url="https://github.com/owner/repo/pull/99",
        output_path=output_path,
        ci_evidence=None,
    )
    assert pending["status"] == "AWAITING_PR_CI"
    assert pending["closure_eligible"] is False
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    assert task_payload["status"] == "WAITING_EXTERNAL_RESULT"
    assert task_payload["phase"] == "STAGE3_PR_CI_REQUIRED"
    assert task_payload["conditions"]["draft_pr_published"]["satisfied"] is False

    completed = COMPLETE.complete_publication(
        validation_result_path=validation_path,
        publication_commit_path=publication_path,
        task_run_path=task_path,
        pr_url="https://github.com/owner/repo/pull/99",
        output_path=output_path,
        ci_evidence=_green_ci_evidence("p" * 40),
    )
    assert completed["status"] == "VERIFIED_GREEN"
    assert completed["closure_eligible"] is True
    assert completed["exit_reason"] == "VERIFIED_GREEN"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    assert task_payload["status"] == "COMPLETED"
    assert task_payload["phase"] == "COMPLETED"
    assert task_payload["conditions"]["draft_pr_published"]["satisfied"] is True


def test_pr_ci_failure_is_continue_condition_not_success_exit() -> None:
    sha = "a" * 40
    runs = [
        {
            "id": 11,
            "name": "quality",
            "head_sha": sha,
            "status": "completed",
            "conclusion": "failure",
            "event": "pull_request",
            "html_url": "https://github.com/owner/repo/actions/runs/11",
        },
        {
            "id": 12,
            "name": "skill-self-validation",
            "head_sha": sha,
            "status": "completed",
            "conclusion": "success",
            "event": "pull_request",
            "html_url": "https://github.com/owner/repo/actions/runs/12",
        },
    ]
    decision = COMPLETE.evaluate_pr_ci_runs(runs, candidate_sha=sha)
    assert decision["status"] == "PR_CI_FAILED_RETRYABLE"
    assert decision["closure_eligible"] is False
    assert decision["continue_repair"] is True
    assert decision["exit_reason"] is None


def test_pr_ci_evidence_must_match_exact_latest_head() -> None:
    sha = "a" * 40
    old_sha = "b" * 40
    runs = [
        {
            "id": 21,
            "name": "quality",
            "head_sha": old_sha,
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 22,
            "name": "skill-self-validation",
            "head_sha": old_sha,
            "status": "completed",
            "conclusion": "success",
        },
    ]
    decision = COMPLETE.evaluate_pr_ci_runs(runs, candidate_sha=sha)
    assert decision["status"] == "AWAITING_PR_CI"
    assert set(decision["missing"]) == set(COMPLETE.REQUIRED_PR_WORKFLOWS)
    assert decision["closure_eligible"] is False


def test_repair_terminal_exit_reason_vocabulary_is_fail_closed() -> None:
    assert COMPLETE.ALLOWED_TERMINAL_EXIT_REASONS == {
        "VERIFIED_GREEN",
        "ATTEMPT_BUDGET_EXHAUSTED",
        "ENVIRONMENT_BLOCKED",
        "HUMAN_DECISION_REQUIRED",
    }
