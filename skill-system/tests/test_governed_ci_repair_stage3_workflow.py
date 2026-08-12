from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"
STAGE3 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage3.yml"
TARGET_FACTORY = ROOT / "scripts" / "create_ci_quality_target.py"


def test_stage3_is_event_driven_and_independently_validates_before_publication() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert "governed-ci-repair-stage2" in text
    assert "workflow_run:" in text
    assert "workflow_dispatch:" in text
    assert "stage2_run_id:" in text
    assert "stage2_run_attempt:" in text
    assert "Resolve exact successful Stage-2 source" in text
    assert "github_repair_stage3.py inspect" in text
    assert "github_repair_stage3.py prepare" in text
    assert "github_repair_stage3_tree.py" in text
    assert "github_repair_stage3.py targeted" in text
    assert "--mode quick" in text
    assert "github_repair_stage3.py validate" in text
    assert "github_repair_stage3_publish.py" in text
    assert "github_repair_stage3_complete.py" in text
    assert "gh pr create --draft" in text
    assert "gh workflow run quality.yml" in text


def test_stage3_binds_exact_rerun_attempt_and_artifact() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert '.name == "governed-ci-repair-stage2"' in text
    assert '.conclusion == "success"' in text
    assert '((.run_attempt | tostring) == $attempt)' in text
    assert 'run_started_at=$(jq -r' in text
    assert 'select(.created_at >= $started)' in text
    assert 'sort_by(.created_at)' in text
    assert 'stage2_artifact_id=${stage2_artifact_id}' in text
    assert 'actions/artifacts/${STAGE2_ARTIFACT_ID}/zip' in text
    assert '.schema == "github-governed-repair-stage2@1"' in text
    assert '.status == "REPAIR_CANDIDATE_READY"' in text
    assert '.stage3_handoff_bound == true' in text
    assert "merge-multiple: true" not in text
    assert "run-id: ${{ github.event.workflow_run.id }}" not in text


def test_stage3_has_an_explicit_complete_quick_target_contract() -> None:
    workflow = STAGE3.read_text(encoding="utf-8")
    factory = TARGET_FACTORY.read_text(encoding="utf-8")
    assert "--workflow governed-ci-repair-stage3" in workflow
    assert '"governed-ci-repair-stage3": "quick"' in factory
    assert '"governed-ci-repair-stage3": [' in factory
    for gate in (
        "adversarial-runtime-counterexamples",
        "python-test-suites",
        "frontend-vitest",
        "coverage-baseline",
        "full-lifecycle-canary",
        "product-browser-journey",
    ):
        assert gate in factory


def test_stage3_uses_one_runtime_exported_judge_bundle_for_projection_and_quick() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    runtime_root = "${{ runner.temp }}/governed-repair-stage3-judge"
    assert f"STAGE3_TRUSTED_JUDGE_ROOT: {runtime_root}" in text
    assert f"SKILL_JUDGE_ROOT: {runtime_root}" in text
    assert "SKILL_JUDGE_ROOT: ${{ github.workspace }}/control" not in text
    create_start = text.index("      - name: Create full Quick validation target\n")
    quick_start = text.index("      - name: Run independent complete Quick Quality Loop\n")
    record_start = text.index("      - name: Record independent validation evidence\n")
    assert runtime_root in text[create_start:quick_start]
    assert runtime_root in text[quick_start:record_start]


def test_validation_job_is_read_only_and_publisher_does_not_run_candidate_code() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    validate_start = text.index("  validate:\n")
    publish_start = text.index("  publish:\n")
    validate_block = text[validate_start:publish_start]
    publish_block = text[publish_start:]
    assert "contents: read" in validate_block
    assert "contents: write" not in validate_block
    assert "pull-requests: write" not in validate_block
    assert "Run fixed targeted regression suites" in validate_block
    assert "Run independent complete Quick Quality Loop" in validate_block
    assert "contents: write" in publish_block
    assert "Recreate and verify the validated tree without running candidate code" in publish_block
    assert "npm ci" not in publish_block
    assert "pytest" not in publish_block
    assert "quality_loop.py" not in publish_block


def test_stage2_and_stage3_pin_all_split_jobs_to_one_control_sha() -> None:
    stage2 = STAGE2.read_text(encoding="utf-8")
    stage3 = STAGE3.read_text(encoding="utf-8")
    assert "Bind trusted Stage-2 control SHA" in stage2
    assert "control_sha: ${{ steps.control.outputs.control_sha }}" in stage2
    assert "ref: ${{ needs.inspect.outputs.control_sha }}" in stage2
    assert "Bind trusted Stage-3 control SHA" in stage3
    assert "control_sha: ${{ steps.control.outputs.control_sha }}" in stage3
    assert stage3.count("ref: ${{ needs.inspect.outputs.control_sha }}") == 2


def test_stage3_has_no_model_or_production_secret_access() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert "environment: production-certification" not in text
    assert "PRODUCTION_MODEL_API_KEY" not in text
    assert "PRODUCTION_EMBEDDING_API_KEY" not in text
    assert "QUALITY_EVIDENCE_SIGNING_KEY" not in text
    assert "merge_pull_request" not in text
    assert "gh pr merge" not in text
    assert "production_closed: false" in text


def test_stage3_rejects_stale_base_branch_collision_and_non_draft_pr() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert '"${base_sha}" != "${SOURCE_HEAD_SHA}"' in text
    assert "already points to different evidence" in text
    assert "existing repair PR is not the expected Draft PR" in text
    assert ".isDraft == true" in text
    assert "--force" not in text
    assert "--force-with-lease" not in text


def test_stage2_binds_handoff_blocks_recursion_and_skips_without_failure_artifact() -> None:
    text = STAGE2.read_text(encoding="utf-8")
    assert "github_stage2_handoff.py" in text
    assert 'startswith("governed-repair/")' in text
    assert "and not" in text
    assert "continue-on-error: true" in text
    assert "No governed failure artifact was produced by Stage 1; Stage 2 skipped." in text
    assert 'echo "repair_allowed=false"' in text
