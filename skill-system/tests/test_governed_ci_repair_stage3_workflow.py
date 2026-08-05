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


def test_stage2_binds_handoff_and_blocks_recursive_repair() -> None:
    text = STAGE2.read_text(encoding="utf-8")
    assert "github_stage2_handoff.py" in text
    assert 'startswith("governed-repair/")' in text
    assert "and not" in text
