from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-failure-ingest.yml"


def test_failure_ingest_workflow_is_read_only_and_event_driven() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "name: governed-ci-failure-ingest",
        "workflow_run:",
        "- quality",
        "- wp08-full-stack-certification",
        "types: [completed]",
        "github.event.workflow_run.conclusion != 'success'",
        "actions/download-artifact@",
        "actions/runs/${SOURCE_RUN_ID}/logs",
        "scripts/github_failure_ingest.py",
        "governed-ci-failure-${{ github.event.workflow_run.id }}",
        "persist-credentials: false",
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing workflow fragments: {missing}"

    assert "environment: production-certification" not in text
    assert "secrets.PRODUCTION_MODEL_API_KEY" not in text
    assert "secrets.PRODUCTION_EMBEDDING_API_KEY" not in text
    assert "QUALITY_EVIDENCE_SIGNING_KEY" not in text
    assert "contents: write" not in text
    assert "git push" not in text
    assert "gh pr create" not in text
    assert "repair:" not in text
