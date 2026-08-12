from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-stage3-recovery-handoff.yml"


def test_stage3_recovery_has_scheduled_push_and_manual_recovery() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "name: governed-ci-stage3-recovery-handoff",
        "schedule:",
        "cron: '*/10 * * * *'",
        "push:",
        "branches: [main]",
        "workflow_dispatch:",
        "GH_REPO: ${{ github.repository }}",
        "Discover newest candidate-ready Stage-2 run missing Stage 3",
        "governed-ci-repair-stage2",
        "REPAIR_CANDIDATE_READY",
        "stage3_handoff_bound == true",
        "governed-ci-repair-stage3-inspect-${run_id}-${run_attempt}",
        "AUTO_STAGE3_RECOVERY_DISPATCHED:${run_id}/${run_attempt}",
        "Dispatch exact governed Stage 3 recovery",
        "governed-ci-repair-stage3.yml/dispatches",
        'inputs[stage2_run_id]=${STAGE2_RUN_ID}',
        'inputs[stage2_run_attempt]=${STAGE2_RUN_ATTEMPT}',
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing Stage-3 recovery fragments: {missing}"


def test_stage3_recovery_can_only_dispatch_and_comment() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions: write" in text
    assert "issues: write" in text
    forbidden = (
        "contents: write",
        "environment: production-certification",
        "PRODUCTION_MODEL_API_KEY",
        "PRODUCTION_EMBEDDING_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "git push",
        "gh pr create",
        "github_repair_stage3.py prepare",
        "quality_loop.py",
        "production_closed: true",
    )
    present = [fragment for fragment in forbidden if fragment in text]
    assert not present, f"Stage-3 recovery gained forbidden authority: {present}"


def test_stage3_recovery_never_checks_out_or_executes_candidate_code() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ref: main" in text
    assert "path: control" in text
    assert "persist-credentials: false" in text
    assert "path: candidate" not in text
    assert "Candidate code executed by this handoff workflow: \\`false\\`" in text
    assert "Model Secret read by this handoff workflow: \\`false\\`" in text


def test_stage3_recovery_is_attempt_bound_and_idempotent() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'run_attempt=$(jq -r' in text
    assert 'run_started_at=$(jq -r' in text
    assert 'select(.created_at >= $started)' in text
    assert '.schema == "github-governed-repair-stage2@1"' in text
    assert '.status == "REPAIR_CANDIDATE_READY"' in text
    assert '.stage3_handoff_bound == true' in text
    assert 'sort_by(.created_at)' in text
    assert 'actions/artifacts?name=${inspect_artifact_name}' in text
    assert 'gh issue view "${issue_number}" --json body,comments' in text
    assert 'grep -Fq "${marker}"' in text
