from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-failure-recovery.yml"
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


def test_recovery_is_owner_bound_and_stage1_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "name: governed-ci-failure-recovery",
        "issue_comment:",
        "types: [created]",
        "github.event.comment.author_association == 'OWNER'",
        "startsWith(github.event.comment.body, '/governed-repair-ingest ')",
        "/governed-repair-ingest <numeric-run-id>",
        "actions/runs/${SOURCE_RUN_ID}",
        "pulls/${ISSUE_NUMBER}",
        "scripts/github_failure_recovery_event.py",
        "scripts/github_failure_ingest_control_plane.py",
        "governed-ci-recovery-stage1-${{ steps.source.outputs.source_run_id }}",
        "Recovery mode: \\`stage1-only\\`",
        "Stage 2 started by this workflow: \\`false\\`",
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing recovery workflow fragments: {missing}"


def test_recovery_has_no_model_or_source_write_authority() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "environment: production-certification",
        "secrets.PRODUCTION_MODEL_API_KEY",
        "secrets.PRODUCTION_EMBEDDING_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "contents: write",
        "git push",
        "gh pr create",
        "github_repair_orchestrator.py",
        "github_stage2_handoff.py",
        "governed-ci-repair-stage2-",
    )
    present = [fragment for fragment in forbidden if fragment in text]
    assert not present, f"Stage-1 recovery gained forbidden authority: {present}"


def test_recovery_receipt_precedes_untrusted_candidate_processing() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    bind = text.index("- name: Bind owner-authorized failed source run")
    receipt = text.index("- name: Record Stage-1 recovery receipt")
    candidate = text.index("- name: Checkout failed source as untrusted data")
    artifacts = text.index("- name: Download failed-run artifacts as untrusted evidence")
    ingest = text.index("- name: Ingest failure and create durable TaskRun")
    assert bind < receipt < candidate < artifacts < ingest
    assert text.count("gh issue create") == 1


def test_stage2_does_not_subscribe_to_recovery_workflow() -> None:
    stage2 = STAGE2.read_text(encoding="utf-8")
    assert "- governed-ci-failure-ingest" in stage2
    assert "governed-ci-failure-recovery" not in stage2
