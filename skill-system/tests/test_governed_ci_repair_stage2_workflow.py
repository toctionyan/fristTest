from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"


def test_stage2_initial_entry_is_manual_explicit_fallback_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: governed-ci-repair-stage2" in text
    assert "workflow_dispatch:" in text
    assert "workflow_run:" not in text
    assert "- governed-ci-failure-ingest" not in text
    assert "source_run_id:" in text
    assert "source_run_attempt:" in text
    assert "remote_repair_approval:" in text
    assert "must be exactly explicitly-approved" in text
    assert '"${REMOTE_REPAIR_APPROVAL}" != "explicitly-approved"' in text
    assert "Normal CI code failures must return to the local Patch Owner instead." in text
    assert "Initial remote fallback must start at repair_round=1." in text


def test_stage2_later_rounds_require_bound_outer_loop_feedback() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "loop_feedback_run_id:",
        "loop_feedback_run_attempt:",
        '[[ "${REPAIR_ROUND}" =~ ^[2-8]$ ]]',
        "governed-repair-loop-feedback-${DISPATCH_SOURCE_RUN_ID}",
        '.name == "governed-ci-repair-loop-coordinator"',
        '.conclusion == "success"',
        "outer-loop feedback is missing seed.patch",
        "outer-loop feedback is missing loop-state.json",
        'loop.get("action") != "DISPATCH_REPAIR"',
        "outer-loop next repair round mismatch",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing bounded fallback continuation fragments: {missing}"


def test_stage2_exact_source_and_task_binding_remain_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        '[[ "${DISPATCH_SOURCE_RUN_ID}" =~ ^[1-9][0-9]*$ ]]',
        '[[ "${DISPATCH_SOURCE_RUN_ATTEMPT}" =~ ^[1-9][0-9]*$ ]]',
        "Stage-1 TaskRun binding mismatch",
        "approved source Run ID does not match governed evidence",
        "approved source run attempt does not match governed evidence",
        "initial remote fallback lacks explicit approval",
        "unsupported Stage-2 input kind",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"missing Stage-2 binding fragments: {missing}"


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


def test_stage2_uses_trusted_scope_normalizer() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "control/scripts/github_repair_orchestrator_control_plane.py" in text
    assert "control/scripts/github_repair_orchestrator.py" not in text


def test_stage2_publishes_redacted_machine_readable_blocker() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "Build redacted Stage-2 public summary",
        "github-stage2-public-summary@1",
        "from github_failure_ingest import redact",
        "stage2-evidence/public-summary.json",
        "Stage-2 workflow run: https://github.com/${GITHUB_REPOSITORY}/actions/runs/${STAGE2_RUN_ID}",
        "Explicit remote fallback Stage 2 completed",
        "This workflow is a manually entered fallback, not the default CI repair lane.",
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
