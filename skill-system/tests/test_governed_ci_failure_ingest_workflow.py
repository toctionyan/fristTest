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
        "types: [completed]",
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


def test_workflow_run_receipt_precedes_all_untrusted_processing() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    receipt = text.index("- name: Record workflow-run receipt")
    trusted_checkout = text.index("- name: Checkout trusted ingestion control plane")
    candidate_checkout = text.index("- name: Checkout failed source as untrusted data")
    artifact_download = text.index("- name: Download failed-run artifacts as untrusted evidence")

    assert receipt < trusted_checkout < candidate_checkout < artifact_download
    assert "- Stage: \\`RECEIVED\\`" in text
    assert 'echo "issue_number=${ISSUE_NUMBER}" >> "$GITHUB_OUTPUT"' in text
    assert text.count("gh issue create") == 2


def test_owner_recovery_lane_is_exact_bound_and_precedes_untrusted_checkout() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    resolve = text.index("- name: Resolve and bind failed source run")
    recovery_receipt = text.index("- name: Record owner-recovery receipt")
    candidate_checkout = text.index("- name: Checkout failed source as untrusted data")
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
        "The recovery binder verified the same repository, current open PR, exact current PR head",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing recovery binding fragments: {missing}"
    assert resolve < recovery_receipt < candidate_checkout


def test_ingestion_failure_remains_observable_and_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "- name: Finalize governed failure issue",
        "- name: Mark ingestion pipeline failure",
        "if: ${{ failure() && steps.issue.outputs.issue_number != '' }}",
        "- Stage: \\`INGESTION_FAILED\\`",
        "- Automatic repair authorized: false",
        "- Source changes: none",
        "- production_closed: false",
        "if: always()",
        "Receipt issue: \\`#${ISSUE_NUMBER}\\`",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing observability fragments: {missing}"
