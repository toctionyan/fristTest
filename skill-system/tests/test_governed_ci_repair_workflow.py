from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-repair.yml"


def test_governed_repair_workflow_is_event_driven_and_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert "wp08-full-stack-certification" in text
    assert "quality" in text
    assert "types: [completed]" in text
    assert "github.event.workflow_run.conclusion != 'success'" in text
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in text
    assert "environment: production-certification" in text
    assert "GOVERNED_REPAIR_MODEL_API_KEY" in text
    assert "--max-cycles 8" in text
    assert "gh pr create --draft" in text
    assert "merge" not in text.casefold()
    assert "production_closed=true" not in text.casefold()
