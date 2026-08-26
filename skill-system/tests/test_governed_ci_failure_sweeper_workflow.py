from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-failure-sweeper.yml"
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


def test_sweeper_is_automatic_and_bounded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "name: governed-ci-failure-sweeper",
        "schedule:",
        "cron: '*/15 * * * *'",
        "push:",
        "branches: [main]",
        "workflow_dispatch:",
        "group: governed-ci-failure-sweeper",
        "Record sweeper activation heartbeat",
        "[governed-ci-sweeper] heartbeat",
        "Discover newest unprocessed failed PR run",
        'select(.name == "quality")',
        "actions/runs/${run_id}",
        "scripts/github_failure_sweeper_event.py",
        "Record automatic sweeper receipt",
        "Checkout failed source as untrusted data",
        "scripts/github_failure_ingest_control_plane.py",
        "governed-ci-sweeper-stage1-${{ steps.discover.outputs.source_run_id }}",
        "Stage 2 started by this workflow: \\`false\\`",
        "Finalize sweeper heartbeat",
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing sweeper fragments: {missing}"


def test_heartbeat_and_failure_receipt_precede_untrusted_processing() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    heartbeat = text.index("- name: Record sweeper activation heartbeat")
    checkout = text.index("- name: Checkout trusted sweeper control plane")
    discover = text.index("- name: Discover newest unprocessed failed PR run")
    receipt = text.index("- name: Record automatic sweeper receipt")
    candidate = text.index("- name: Checkout failed source as untrusted data")
    artifacts = text.index("- name: Download failed-run artifacts as untrusted evidence")
    logs = text.index("- name: Download failed-run logs as untrusted evidence")
    ingest = text.index("- name: Ingest failure and create durable TaskRun")
    assert heartbeat < checkout < discover < receipt < candidate < artifacts < logs < ingest
    assert text.count("gh issue create") == 2


def test_issue_commands_have_explicit_repository_context() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "GH_REPO: ${{ github.repository }}" in text
    commands = [
        line.strip()
        for line in text.splitlines()
        if "gh issue " in line
    ]
    assert commands
    assert all('--repo "${GH_REPO}"' in line for line in commands)
    assert ") --repo" not in text
    assert text.count(' --repo "${GH_REPO}")') == 2


def test_discovery_fetches_full_run_before_reading_pr_binding() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    fetch = text.index('gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}"')
    derive = text.index("pr_number=$(jq -r '.pull_requests[0].number // empty'")
    pr_fetch = text.index('gh api "repos/${GITHUB_REPOSITORY}/pulls/${pr_number}"')
    binder = text.index("control/scripts/github_failure_sweeper_event.py")
    assert fetch < derive < pr_fetch < binder
    assert 'select((.pull_requests // []) | length > 0)' not in text


def test_sweeper_has_no_model_or_source_write_authority() -> None:
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
    assert not present, f"sweeper gained forbidden authority: {present}"


def test_sweeper_is_not_a_stage2_trigger() -> None:
    stage2 = STAGE2.read_text(encoding="utf-8")
    assert "- governed-ci-failure-ingest" in stage2
    assert "governed-ci-failure-sweeper" not in stage2


def test_sweeper_deduplicates_and_rejects_recursive_repair_branches() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gh issue list --state all" in text
    assert "[governed-ci-failure] run ${run_id} in:title" in text
    assert 'startswith("governed-repair/") | not' in text
    assert "No current unprocessed failed PR run was found." in text


def test_pipeline_failure_is_reported_on_stable_heartbeat() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Mark sweeper pipeline failure on heartbeat" in text
    assert "failure() && steps.heartbeat.outputs.issue_number != ''" in text
    assert "Pipeline state: \\`${JOB_STATUS}\\`" in text
    assert "This stable Issue is updated by each scheduled, push, or manual sweep." in text
