from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_post_merge_validation.py"
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-post-merge-validation.yml"
RELEASE_WORKFLOW = ".github/workflows/release.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("github_post_merge_validation", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_pr():
    return {
        "number": 1710,
        "state": "closed",
        "merged": True,
        "merged_at": "2026-08-18T00:10:58Z",
        "merge_commit_sha": "b" * 40,
        "html_url": "https://github.com/toctionyan/fristTest/pull/1710",
        "head": {"sha": "7" * 40},
        "base": {"ref": "main", "sha": "1" * 40},
    }


def _valid_merge_commit():
    return {
        "sha": "b" * 40,
        "parents": [{"sha": "1" * 40}, {"sha": "7" * 40}],
    }


def _valid_quality_run():
    return {
        "id": 101,
        "run_attempt": 1,
        "name": "quality",
        "event": "workflow_dispatch",
        "head_sha": "b" * 40,
        "status": "completed",
        "conclusion": "success",
    }


def _valid_convergence_run():
    return {
        "id": 202,
        "run_attempt": 1,
        "name": "project-convergence",
        "event": "workflow_run",
        "head_sha": "b" * 40,
        "status": "completed",
        "conclusion": "success",
    }


def test_exact_post_merge_validation_is_validation_only():
    module = _load_module()
    target = module.verify_target(
        _valid_pr(),
        _valid_merge_commit(),
        source_pr_number=1710,
        merge_sha="b" * 40,
        actor="toctionyan",
        repository_owner="toctionyan",
    )
    result = module.finalize(target, _valid_quality_run(), _valid_convergence_run())

    assert target["status"] == "POST_MERGE_TARGET_VERIFIED"
    assert result["status"] == "POST_MERGE_VALIDATED"
    assert result["merge_sha"] == "b" * 40
    assert result["authority_effect"] is False
    assert result["merge_allowed"] is False
    assert result["deploy_allowed"] is False
    assert result["production_closed"] is False


def test_non_owner_cannot_start_post_merge_validation():
    module = _load_module()
    with pytest.raises(module.PostMergeValidationError, match="repository owner"):
        module.verify_target(
            _valid_pr(),
            _valid_merge_commit(),
            source_pr_number=1710,
            merge_sha="b" * 40,
            actor="someone-else",
            repository_owner="toctionyan",
        )


def test_requested_merge_must_be_the_pr_merge_commit():
    module = _load_module()
    pr = _valid_pr()
    pr["merge_commit_sha"] = "c" * 40
    with pytest.raises(module.PostMergeValidationError, match="not the pull request merge commit"):
        module.verify_target(
            pr,
            _valid_merge_commit(),
            source_pr_number=1710,
            merge_sha="b" * 40,
            actor="toctionyan",
            repository_owner="toctionyan",
        )


def test_merge_second_parent_must_be_exact_pr_head():
    module = _load_module()
    commit = _valid_merge_commit()
    commit["parents"][1]["sha"] = "8" * 40
    with pytest.raises(module.PostMergeValidationError, match="second parent"):
        module.verify_target(
            _valid_pr(),
            commit,
            source_pr_number=1710,
            merge_sha="b" * 40,
            actor="toctionyan",
            repository_owner="toctionyan",
        )


def test_quality_must_run_on_exact_merge_sha():
    module = _load_module()
    target = module.verify_target(
        _valid_pr(),
        _valid_merge_commit(),
        source_pr_number=1710,
        merge_sha="b" * 40,
        actor="toctionyan",
        repository_owner="toctionyan",
    )
    quality = _valid_quality_run()
    quality["head_sha"] = "c" * 40
    with pytest.raises(module.PostMergeValidationError, match="exact merge SHA"):
        module.finalize(target, quality, _valid_convergence_run())


def test_project_convergence_must_be_chained_and_successful_on_exact_merge():
    module = _load_module()
    target = module.verify_target(
        _valid_pr(),
        _valid_merge_commit(),
        source_pr_number=1710,
        merge_sha="b" * 40,
        actor="toctionyan",
        repository_owner="toctionyan",
    )
    convergence = _valid_convergence_run()
    convergence["event"] = "workflow_dispatch"
    with pytest.raises(module.PostMergeValidationError, match="not chained"):
        module.finalize(target, _valid_quality_run(), convergence)


def test_workflow_closes_github_token_merge_cascade_gap_without_release_authority():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_run:" in text
    assert "governed-ci-repair-merge" in text
    assert "governed-repair-merge-consumption@1" in text
    assert "MERGED_GRANT_CONSUMED" in text
    assert "quality.yml/dispatches" in text
    assert "workflow_dispatch&branch=${TEMP_BRANCH}" in text
    assert "project-convergence.yml/runs?event=workflow_run&branch=${TEMP_BRANCH}" in text
    assert "merge-base --is-ancestor" in text
    assert "POST_MERGE_VALIDATED" in text
    assert "authority_effect=false" in text
    assert "merge_allowed=false" in text
    assert "deploy_allowed=false" in text
    assert "production_closed=false" in text
    assert RELEASE_WORKFLOW not in text
    assert "production-certification-release" not in text


def test_pr_comment_transport_is_rest_and_never_authoritative():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "gh pr comment" not in text
    assert text.count('repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments') == 2
    assert text.count("COMMENT_WRITE_FAILED_NON_AUTHORITATIVE") == 2
    assert "non-authoritative start comment could not be written; validation continues" in text
    assert "validated receipt remains authoritative" in text
    assert text.index("Finalize immutable post-merge validation receipt") < text.index(
        "Record completed post-merge validation"
    )
    assert text.index("Record completed post-merge validation") < text.index(
        "Upload immutable post-merge evidence"
    )


def test_post_merge_waits_use_deadline_budgets_instead_of_short_attempt_counts():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "timeout-minutes: 45" in text
    assert "QUALITY_WAIT_TIMEOUT_SECONDS: '1200'" in text
    assert "CONVERGENCE_WAIT_TIMEOUT_SECONDS: '900'" in text
    assert "for attempt in $(seq 1 90)" not in text
    assert "for attempt in $(seq 1 60)" not in text
    assert "quality_deadline=$(( $(date +%s) + QUALITY_WAIT_TIMEOUT_SECONDS ))" in text
    assert "convergence_deadline=$(( $(date +%s) + CONVERGENCE_WAIT_TIMEOUT_SECONDS ))" in text
    assert "exact post-merge Quality timed out before completion" in text
    assert "exact project-convergence timed out before completion" in text
