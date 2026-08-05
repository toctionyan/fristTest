from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


def test_stage2_consumes_only_bound_stage1_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: governed-ci-repair-stage2" in text
    assert "workflow_run:" in text
    assert "- governed-ci-failure-ingest" in text
    assert "workflow_dispatch:" in text
    assert "source_run_id:" in text
    assert "source_run_attempt:" in text
    assert "stage1_artifact_pattern=governed-ci-failure-*" in text
    assert "stage1_artifact_pattern=governed-ci-quality-stage1-${DISPATCH_SOURCE_RUN_ID}" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "Stage-1 TaskRun binding mismatch" in text
    assert "approved source Run ID does not match Stage-1 evidence" in text
    assert "approved source run attempt does not match Stage-1 evidence" in text


def test_stage2_secrets_are_gated_behind_read_only_inspection() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    inspect, repair = text.split("  repair:\n", maxsplit=1)
    assert "secrets.PRODUCTION_MODEL_API_KEY" not in inspect
    assert "environment: production-certification" not in inspect
    assert "needs.inspect.outputs.repair_allowed == 'true'" in repair
    assert "environment: production-certification" in repair
    assert "secrets.PRODUCTION_MODEL_API_KEY" in repair


def test_stage2_uses_low_cost_deepseek_defaults_without_overriding_environment() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert (
        "GOVERNED_REPAIR_MODEL_PROVIDER: ${{ vars.REAL_MODEL_CERTIFICATION_PROVIDER || "
        "vars.PRODUCTION_MODEL_PROVIDER || vars.MODEL_PROVIDER || 'deepseek' }}"
    ) in text
    assert (
        "GOVERNED_REPAIR_MODEL: ${{ vars.OPENAI_MODEL || vars.PRODUCTION_MODEL_ID || "
        "vars.MODEL_ID || 'deepseek-v4-flash' }}"
    ) in text
    assert (
        "GOVERNED_REPAIR_MODEL_API_BASE: ${{ vars.OPENAI_API_BASE || "
        "vars.PRODUCTION_MODEL_API_BASE || vars.MODEL_API_BASE || 'https://api.deepseek.com' }}"
    ) in text
    assert text.index("vars.REAL_MODEL_CERTIFICATION_PROVIDER") < text.index("'deepseek'")
    assert text.index("vars.OPENAI_MODEL") < text.index("'deepseek-v4-flash'")
    assert text.index("vars.OPENAI_API_BASE") < text.index("'https://api.deepseek.com'")


def test_stage2_manual_handoff_remains_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '[[ "${DISPATCH_SOURCE_RUN_ID}" =~ ^[1-9][0-9]*$ ]]' in text
    assert '[[ "${DISPATCH_SOURCE_RUN_ATTEMPT}" =~ ^[1-9][0-9]*$ ]]' in text
    assert "run-id: ${{ steps.source.outputs.stage1_run_id }}" in text
    assert "run-id: ${{ needs.inspect.outputs.stage1_run_id }}" in text
    assert "pattern: ${{ steps.source.outputs.stage1_artifact_pattern }}" in text
    assert "pattern: ${{ needs.inspect.outputs.stage1_artifact_pattern }}" in text


def test_stage2_uses_trusted_scope_normalizer() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "control/scripts/github_repair_orchestrator_control_plane.py" in text
    assert "control/scripts/github_repair_orchestrator.py" not in text
    assert "normalized-failure-case.json" not in text


def test_stage2_publishes_redacted_machine_readable_blocker() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "Build redacted Stage-2 public summary",
        "github-stage2-public-summary@1",
        "from github_failure_ingest import redact",
        "stage2-evidence/public-summary.json",
        "Stage-2 workflow run: https://github.com/${GITHUB_REPOSITORY}/actions/runs/${STAGE2_RUN_ID}",
        "Stage-2 Run ID / attempt:",
        "Code:",
        "Redacted reason:",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing Stage-2 observability fragments: {missing}"
    assert "reason = redact(" in text
    assert "[:1000]" in text


def test_stage2_cannot_publish_or_close_production() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "contents: write",
        "git push",
        "gh pr create",
        "merge_pull_request",
        "production_closed: true",
    )
    for fragment in forbidden:
        assert fragment not in text
    assert "--max-cycles 8" in text
    assert "Full targeted/Quick regression: not yet performed" in text
    assert "Draft PR created: no" in text
    assert "production_closed: false" in text
