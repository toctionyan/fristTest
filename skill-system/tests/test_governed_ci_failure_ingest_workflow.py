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
        "scripts/github_failure_ingest_control_plane.py",
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
    assert text.count("gh issue create") == 1


def test_ingestion_failure_remains_observable_and_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "- name: Finalize governed failure issue",
        "- name: Mark ingestion pipeline failure",
        "if: ${{ failure() && steps.receipt.outputs.issue_number != '' }}",
        "- Stage: \\`INGESTION_FAILED\\`",
        "- Automatic repair authorized: false",
        "- Source changes: none",
        "- production_closed: false",
        "if: always()",
        "Receipt issue: \\`#${ISSUE_NUMBER}\\`",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing observability fragments: {missing}"
