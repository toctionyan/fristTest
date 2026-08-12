from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-stage2-auto-handoff.yml"
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"
STAGE3 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage3.yml"


def test_auto_handoff_has_primary_event_and_scheduled_recovery() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "name: governed-ci-stage2-auto-handoff",
        "workflow_run:",
        "- quality",
        "types: [completed]",
        "schedule:",
        "cron: '*/10 * * * *'",
        "push:",
        "branches: [main]",
        "workflow_dispatch:",
        "GH_REPO: ${{ github.repository }}",
        "group: governed-ci-stage2-auto-handoff",
        "Discover newest unprocessed governed Stage-1 failure",
        "scripts/github_stage2_auto_handoff.py",
        "governed-ci-quality-stage1-${run_id}",
        "AUTO_STAGE2_DISPATCHED:${run_id}/${run_attempt}",
        "Dispatch governed Stage 2 automatically",
        "governed-ci-repair-stage2.yml/dispatches",
        'inputs[source_run_id]=${SOURCE_RUN_ID}',
        'inputs[source_run_attempt]=${SOURCE_RUN_ATTEMPT}',
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing automatic handoff fragments: {missing}"


def test_auto_handoff_has_only_dispatch_authority() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions: write" in text
    assert "issues: write" in text
    forbidden = (
        "contents: write",
        "environment: production-certification",
        "secrets.PRODUCTION_MODEL_API_KEY",
        "secrets.PRODUCTION_EMBEDDING_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "git push",
        "gh pr create",
        "github_repair_orchestrator",
        "repair.patch",
        "production_closed: true",
    )
    present = [fragment for fragment in forbidden if fragment in text]
    assert not present, f"automatic handoff gained forbidden authority: {present}"


def test_candidate_code_is_never_checked_out_or_executed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ref: main" in text
    assert "path: control" in text
    assert "persist-credentials: false" in text
    assert "Checkout failed" not in text
    assert "path: candidate" not in text
    assert "Candidate code executed by this handoff workflow: \\`false\\`" in text


def test_handoff_is_idempotent_and_ignores_recursive_repair_branches() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gh issue view \"${issue_number}\" --json body,comments" in text
    assert "grep -Fq \"${marker}\"" in text
    assert 'startswith("governed-repair/") | not' in text
    assert "No current unprocessed repairable Stage-1 failure was found." in text


def test_existing_stage2_and_stage3_form_the_bounded_downstream_loop() -> None:
    stage2 = STAGE2.read_text(encoding="utf-8")
    stage3 = STAGE3.read_text(encoding="utf-8")
    assert "--max-cycles 8" in stage2
    assert "environment: production-certification" in stage2
    assert "governed-ci-repair-stage2" in stage3
    assert "github.event.workflow_run.conclusion == 'success'" in stage3
