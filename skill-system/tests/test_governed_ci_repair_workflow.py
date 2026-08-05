from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-repair.yml"
QUALITY_WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"


def test_governed_repair_workflow_is_event_driven_and_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert "wp08-full-stack-certification" in text
    assert "skill-self-validation" in text
    assert "quality" in text
    assert "types: [completed]" in text
    assert "github.event.workflow_run.conclusion != 'success'" in text
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in text
    assert "Prepare non-repairable fork workspace" in text
    assert "environment: production-certification" in text
    assert "GOVERNED_REPAIR_MODEL_API_KEY" in text
    assert "--max-cycles 8" in text
    assert "github_repair_validation.py" in text
    assert "pgvector/pgvector@sha256:" in text
    assert "gh pr create --draft" in text
    assert "gh workflow run quality.yml" in text
    assert "integration_profile=skip" in text
    assert "gh workflow run skill-self-validation.yml" in text
    assert "merge" not in text.casefold()
    assert "production_closed=true" not in text.casefold()


def test_quality_dispatch_skip_is_internal_and_full_is_default() -> None:
    text = QUALITY_WORKFLOW.read_text(encoding="utf-8")
    assert "integration_profile:" in text
    assert "default: full" in text
    assert "- full" in text
    assert "- skip" in text
    assert "inputs.integration_profile != 'skip'" in text
    assert "github.event_name != 'pull_request'" in text
