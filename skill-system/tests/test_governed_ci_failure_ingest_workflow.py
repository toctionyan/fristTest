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
        "issue_comment:",
        "types: [created]",
        "github.event.workflow_run.conclusion != 'success'",
        "github.event.comment.author_association == 'OWNER'",
        "startsWith(github.event.comment.body, '/governed-repair-run ')",
        "actions/download-artifact@",
        "actions/runs/${SOURCE_RUN_ID}/logs",
        "scripts/github_failure_recovery_event.py",
        "scripts/github_failure_ingest_control_plane.py",
        "--event incoming/source-event.json",
        "governed-ci-failure-${{ steps.source.outputs.source_run_id }}",
        "persist-credentials: false",
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing workflow fragments: {missing}"

    assert "pull_request_target:" not in text
    assert "environment: production-certification" not in text
    assert "secrets.PRODUCTION_MODEL_API_KEY" not in text
    assert "secrets.PRODUCTION_EMBEDDING_API_KEY" not in text
    assert "QUALITY_EVIDENCE_SIGNING_KEY" not in text
    assert "contents: write" not in text
    assert "git push" not in text
    assert "gh pr create" not in text
    assert "repair:" not in text


def test_owner_recovery_lane_rebinds_run_to_current_pr() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "COMMENT_ASSOCIATION",
        '"${COMMENT_ASSOCIATION}" != "OWNER"',
        "^/governed-repair-run[[:space:]]+([1-9][0-9]*)[[:space:]]*$",
        'gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${SOURCE_RUN_ID}"',
        'gh api "repos/${GITHUB_REPOSITORY}/pulls/${ISSUE_NUMBER}"',
        "--comment \"${COMMENT_BODY}\"",
        "--repository \"${GITHUB_REPOSITORY}\"",
        "--issue-number \"${ISSUE_NUMBER}\"",
        "--run-json incoming/source-run.json",
        "--pr-json incoming/source-pr.json",
        "trigger_mode=owner-comment-recovery",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing recovery binding fragments: {missing}"


def test_control_plane_adapter_is_the_only_ingestion_entrypoint() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    invocation = (
        '"${pythonLocation}/bin/python" -B '
        "control/scripts/github_failure_ingest_control_plane.py"
    )
    assert text.count(invocation) == 1
    assert (
        '"${pythonLocation}/bin/python" -B control/scripts/github_failure_ingest.py'
        not in text
    )
