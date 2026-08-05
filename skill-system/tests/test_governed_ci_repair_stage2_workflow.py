from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


def test_stage2_is_event_driven_and_consumes_only_stage1_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: governed-ci-repair-stage2" in text
    assert "workflow_run:" in text
    assert "- governed-ci-failure-ingest" in text
    assert "pattern: governed-ci-failure-*" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "Stage-1 TaskRun binding mismatch" in text


def test_stage2_secrets_are_gated_behind_read_only_inspection() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    inspect, repair = text.split("  repair:\n", maxsplit=1)
    assert "secrets.PRODUCTION_MODEL_API_KEY" not in inspect
    assert "environment: production-certification" not in inspect
    assert "needs.inspect.outputs.repair_allowed == 'true'" in repair
    assert "environment: production-certification" in repair
    assert "secrets.PRODUCTION_MODEL_API_KEY" in repair


def test_stage2_cannot_publish_or_close_production() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "contents: write",
        "git push",
        "gh pr create",
        "merge_pull_request",
        "production_closed: true",
    )
    for fragment in forbidden:
        assert fragment not in text
    assert "--max-cycles 8" in text
    assert "Full targeted/Quick regression: not yet performed" in text
    assert "Draft PR created: no" in text
    assert "production_closed: false" in text
