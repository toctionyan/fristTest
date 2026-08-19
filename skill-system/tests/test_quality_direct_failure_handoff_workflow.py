from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


def test_quality_workflow_has_terminal_direct_stage1_handoff() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "governed-failure-stage1:",
        "always() && github.event_name == 'pull_request'",
        "needs.skill-control-plane.result == 'failure'",
        "needs.quality-static.result == 'failure'",
        "needs.quality-quick.result == 'failure'",
        "Record direct quality failure receipt",
        "QUALITY_RUN_RECEIVED",
        "quality-in-run-stage1",
        "scripts/github_quality_failure_event.py",
        "Checkout failed PR head as untrusted data",
        "Download current-run artifacts as untrusted evidence",
        "Download completed Quality job logs as untrusted evidence",
        "scripts/github_failure_ingest_control_plane.py",
        "governed-ci-quality-stage1-${{ github.run_id }}",
        "QUALITY_RUN_INGESTED",
        "Stage 2 started by this job: \\`false\\`",
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing direct handoff fragments: {missing}"


def test_receipt_precedes_all_trusted_and_untrusted_processing() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    receipt = text.index("- name: Record direct quality failure receipt")
    trusted = text.index("- name: Checkout trusted Stage-1 control plane")
    bind = text.index("- name: Bind current failed Quality run")
    candidate = text.index("- name: Checkout failed PR head as untrusted data")
    artifacts = text.index("- name: Download current-run artifacts as untrusted evidence")
    logs = text.index("- name: Download completed Quality job logs as untrusted evidence")
    ingest = text.index("- name: Ingest failure and create durable TaskRun")
    assert receipt < trusted < bind < candidate < artifacts < logs < ingest


def test_skill_control_plane_failure_evidence_is_always_uploaded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    validate = text.index("- name: Validate Skill control plane")
    upload = text.index("- name: Upload Skill control-plane evidence")
    authority = text.index("- name: Validate quality toolchain authority")
    assert validate < upload < authority
    assert 'tee "${RUNNER_TEMP}/skill-control-plane.log"' in text
    assert "name: skill-control-plane-evidence" in text
    assert "if-no-files-found: warn" in text


def test_direct_handoff_has_no_model_or_source_write_authority() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    job = text[text.index("  governed-failure-stage1:") :]
    forbidden = (
        "environment: production-certification",
        "secrets.PRODUCTION_MODEL_API_KEY",
        "secrets.PRODUCTION_EMBEDDING_API_KEY",
        "QUALITY_EVIDENCE_SIGNING_KEY",
        "contents: write",
        "actions: write",
        "git push",
        "gh pr create",
        "github_repair_orchestrator.py",
        "github_stage2_handoff.py",
        "governed-ci-repair-stage2-",
    )
    present = [fragment for fragment in forbidden if fragment in job]
    assert not present, f"direct Stage-1 handoff gained forbidden authority: {present}"


def test_direct_handoff_cannot_trigger_stage2_by_workflow_name() -> None:
    stage2 = STAGE2.read_text(encoding="utf-8")
    assert "workflow_run:" not in stage2
    assert "- quality" not in stage2
    assert "- governed-ci-failure-ingest" not in stage2
    assert "governed-failure-stage1" not in stage2
    assert "workflow_dispatch:" in stage2
    assert "remote_repair_approval:" in stage2


def test_completed_job_logs_are_fetched_by_immutable_current_run_id() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "SOURCE_RUN_ID: ${{ github.run_id }}" in text
    assert "actions/runs/${SOURCE_RUN_ID}/jobs?filter=latest&per_page=100" in text
    assert 'select(.status == "completed")' in text
    assert "actions/jobs/${job_id}/logs" in text
    assert "steps.source.outputs.source_pr_number" in text


class QualityDirectFailureCommentEscapingTests(unittest.TestCase):
    def test_stage1_issue_markdown_cannot_execute_backtick_command_substitution(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        job = text[text.index("  governed-failure-stage1:") :]

        # Keep normal shell interpolation for the evidence values, but quote every
        # Markdown backtick from shell command substitution inside the two heredocs
        # and the fallback comment. The backslash is consumed by the shell while the
        # resulting GitHub issue body still receives literal Markdown backticks.
        self.assertEqual(job.count("BODY=$(cat <<EOF"), 2)
        required = (
            "- Stage: \\`QUALITY_RUN_RECEIVED\\`",
            "- Ingestion trigger: \\`quality-in-run-stage1\\`",
            "- Source workflow: \\`quality\\`",
            "- Run ID / attempt: \\`${SOURCE_RUN_ID}/${SOURCE_RUN_ATTEMPT}\\`",
            "- Head SHA: \\`${SOURCE_SHA}\\`",
            "- Stage 2 started: \\`false\\`",
            "- Stage: \\`QUALITY_RUN_INGESTED\\`",
            "- Classification: \\`${CLASSIFICATION}\\`",
            "- Failure signature: \\`${FAILURE_SIGNATURE}\\`",
            "- Structurally eligible for later repair: \\`${REPAIR_ALLOWED}\\`",
            "- Stage 2 started by this job: \\`false\\`",
            "- Evidence artifact: \\`governed-ci-quality-stage1-${SOURCE_RUN_ID}\\`",
            "for run \\`${SOURCE_RUN_ID}\\`.",
            "and \\`production_closed=false\\`.",
        )
        missing = [fragment for fragment in required if fragment not in job]
        self.assertFalse(missing, f"unsafe or missing Stage-1 Markdown escaping: {missing}")

        forbidden = (
            "- Stage: `QUALITY_RUN_RECEIVED`",
            "- Ingestion trigger: `quality-in-run-stage1`",
            "- Run ID / attempt: `${SOURCE_RUN_ID}/${SOURCE_RUN_ATTEMPT}`",
            "- Stage: `QUALITY_RUN_INGESTED`",
            "- Classification: `${CLASSIFICATION}`",
            "for run `${SOURCE_RUN_ID}`.",
            "and `production_closed=false`.",
        )
        present = [fragment for fragment in forbidden if fragment in job]
        self.assertFalse(present, f"raw backtick command substitutions remain: {present}")


if __name__ == "__main__":
    unittest.main()
