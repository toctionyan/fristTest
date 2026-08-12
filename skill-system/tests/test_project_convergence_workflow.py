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


def test_project_convergence_binds_exact_quality_attempt_and_artifact() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '.name == "quality"' in text
    assert '((.run_attempt | tostring) == $attempt)' in text
    assert 'run_started_at=$(jq -r' in text
    assert 'actions/runs/${quality_run_id}/attempts/${quality_run_attempt}/jobs' in text
    assert 'select(.created_at >= $started)' in text
    assert 'actions/artifacts/${ARTIFACT_ID}/zip' in text
    assert 'quality-integration-evidence' in text
    assert 'quality-quick-evidence' in text


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
