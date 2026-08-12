from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "project-convergence.yml"
CONTROLLER = ROOT / "scripts" / "project_convergence_controller.py"


def test_project_convergence_is_a_post_quality_read_only_assessment() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflows:\n      - quality" in text
    assert "types: [completed]" in text
    assert "contents: read" in text
    assert "actions: read" in text
    assert "contents: write" not in text
    assert "issues: write" not in text
    assert "pull-requests: write" not in text
    assert "environment: production-certification" not in text
    assert "PRODUCTION_MODEL_API_KEY" not in text


def test_project_convergence_binds_exact_quality_attempt_head_and_cumulative_artifacts() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '.name == "quality"' in text
    assert '((.run_attempt | tostring) == $attempt)' in text
    assert 'run_started_at=$(jq -r' in text
    assert "source_head_sha=$(jq -r '.head_sha // empty' incoming/run.json)" in text
    assert '[[ "${source_head_sha}" =~ ^[0-9a-f]{40}$ ]]' in text
    assert 'echo "source_head_sha=${source_head_sha}" >> "$GITHUB_OUTPUT"' in text
    assert 'EXPECTED_REF: ${{ steps.source.outputs.source_head_sha }}' in text
    assert '--expected-ref "${EXPECTED_REF}"' in text
    assert "source_ref=$(jq -r '.target_identity.change_ref // empty'" not in text
    assert 'actions/runs/${quality_run_id}/attempts/${quality_run_attempt}/jobs' in text
    assert 'select(.created_at >= $started)' in text
    assert 'quick_artifact_id=$(select_artifact "quality-quick-evidence")' in text
    assert 'integration_artifact_id=$(select_artifact "quality-integration-evidence")' in text
    assert 'steps.source.outputs.quick_artifact_id' in text
    assert 'steps.source.outputs.integration_artifact_id' in text
    assert 'actions/artifacts/${ARTIFACT_ID}/zip' in text


def test_integration_assessment_reuses_same_run_quick_and_incremental_evidence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    assert '[[ "$(jq -r \'.requirement_profile // empty\' "${quick_summary}")" == "project-quick" ]]' in workflow
    assert '[[ "$(jq -r \'.requirement_profile // empty\' "${integration_summary}")" == "project-integration" ]]' in workflow
    assert 'cmp -s "${requirements}" "${integration_requirements}"' in workflow
    assert 'args+=(--summary "${integration_summary}")' in workflow
    assert 'PROFILE_CHAIN = {' in controller
    assert '"project-integration": ("project-quick", "project-integration")' in controller
    assert "one immutable workspace snapshot" in controller
    assert "one requirement catalog fingerprint" in controller


def test_project_convergence_uses_structured_quality_evidence_not_log_guessing() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    assert "run-summary.json" in workflow
    assert "requirement-catalog.json" in workflow
    assert "--enforce" in workflow
    assert "claim_results" in controller
    assert "required_gate_ids" in controller
    assert "parse free-form logs" in controller
    assert "stdout" not in controller
    assert "stderr" not in controller


def test_project_convergence_does_not_duplicate_the_repair_actor() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    controller = CONTROLLER.read_text(encoding="utf-8")
    assert "github_repair_orchestrator" not in workflow
    assert "governed-ci-repair-stage2.yml/dispatches" not in workflow
    assert "GOVERNED_REPAIR_MODEL" not in workflow
    assert "subprocess" not in controller
    assert "git apply" not in controller
    assert "project-release" in controller
    assert '"production_closed": False' in controller
    assert '"release_authorized": False' in controller
