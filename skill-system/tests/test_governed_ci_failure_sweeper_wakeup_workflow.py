from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-failure-sweeper-wakeup.yml"
SWEEPER = ROOT / ".github" / "workflows" / "governed-ci-failure-sweeper.yml"


def test_wakeup_uses_trusted_pull_request_target_and_same_repo_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "name: governed-ci-failure-sweeper-wakeup",
        "pull_request_target:",
        "types: [opened, reopened, synchronize, ready_for_review]",
        "actions: write",
        "github.event.pull_request.head.repo.full_name == github.repository",
        "Dispatch trusted main sweeper",
        "governed-ci-failure-sweeper.yml/dispatches",
        "-f ref=main",
        "Candidate code checked out or executed: \\`false\\`",
        "Stage 2 started: \\`false\\`",
        "production_closed: false",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing wakeup fragments: {missing}"


def test_wakeup_never_checks_out_or_executes_candidate_content() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "actions/checkout@",
        "github.event.pull_request.head.ref",
        "github.event.pull_request.body",
        "github.event.pull_request.title",
        "secrets.",
        "environment:",
        "contents: write",
        "issues: write",
        "pull-requests: write",
        "git push",
        "gh pr create",
        "github_failure_ingest",
        "github_repair_orchestrator",
    )
    present = [fragment for fragment in forbidden if fragment in text]
    assert not present, f"wakeup gained untrusted or write authority: {present}"


def test_wakeup_dispatches_only_input_free_main_sweeper() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    sweeper = SWEEPER.read_text(encoding="utf-8")
    assert text.count("/dispatches") == 1
    assert "workflow_dispatch:" in sweeper
    assert "workflow_dispatch:\n    inputs:" not in sweeper
    assert "environment: production-certification" not in sweeper
