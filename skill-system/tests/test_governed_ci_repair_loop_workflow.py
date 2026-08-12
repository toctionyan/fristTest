from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COORDINATOR = ROOT / ".github" / "workflows" / "governed-ci-repair-loop-coordinator.yml"
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"
STAGE3 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage3.yml"


def test_outer_loop_is_stage3_feedback_driven_and_owns_product_round_budget() -> None:
    text = COORDINATOR.read_text(encoding="utf-8")
    assert "governed-ci-repair-stage3" in text
    assert "workflow_run:" in text
    assert "workflow_dispatch:" in text
    assert "github_repair_loop_controller.py" in text
    assert "governed-repair-loop-state-" in text
    assert "governed-repair-loop-feedback-" in text
    assert "repair_round" in text
    assert "verification_attempt" in text
    assert "repair_budget_remaining" in text
    assert "PRODUCT_SOURCE_FAILURE" in text
    assert "TEST_CONTRACT_REVIEW_REQUIRED" in text
    assert "RETRY_VALIDATION_SAME_CANDIDATE" in text
    assert "HARNESS_REPAIR_REQUIRED" in text
    assert "attempts/${stage3_run_attempt}/jobs" in text
    assert "actions/jobs/${INSPECT_JOB_ID}/rerun" in text


def test_stage3_explicitly_dispatches_failed_validation_to_outer_loop() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert "handoff-failed-validation:" in text
    assert "always() && needs.inspect.result == 'success' && needs.validate.result == 'failure'" in text
    assert "actions: write" in text
    assert "actions/workflows/governed-ci-repair-loop-coordinator.yml/dispatches" in text
    assert 'inputs[stage3_run_id]=${GITHUB_RUN_ID}' in text
    assert 'inputs[stage3_run_attempt]=${GITHUB_RUN_ATTEMPT}' in text
    assert "the outer controller is idempotent on run-id/run-attempt" in text


def test_outer_loop_dispatches_stage2_only_for_an_authorized_product_failure() -> None:
    text = COORDINATOR.read_text(encoding="utf-8")
    assert "if: steps.route.outputs.action == 'DISPATCH_REPAIR'" in text
    assert "actions/workflows/governed-ci-repair-stage2.yml/dispatches" in text
    assert 'inputs[loop_feedback_run_id]' in text
    assert 'inputs[loop_feedback_run_attempt]' in text
    assert 'inputs[repair_round]' in text
    assert "contents: write" not in text
    assert "PRODUCTION_MODEL_API_KEY" not in text
    assert "production_closed: false" in text


def test_stage2_accepts_seeded_later_rounds_without_counting_fixer_cycles_as_rounds() -> None:
    text = STAGE2.read_text(encoding="utf-8")
    assert "loop_feedback_run_id:" in text
    assert "loop_feedback_run_attempt:" in text
    assert "repair_round:" in text
    assert "governed-repair-loop-feedback-" in text
    assert "Download exact outer-loop feedback attempt" in text
    assert "input_artifact_id" in text
    assert "outer-loop feedback is missing seed.patch" in text
    assert "outer-loop state did not authorize another product repair" in text
    assert "--seed-patch" in text
    assert "--repair-round" in text
    assert "--max-repair-rounds 8" in text
    assert "--max-cycles 8" in text
    assert "Fixer cycles are internal to this repair round" in text


def test_later_round_feedback_must_preserve_original_binding_and_cannot_expand_authority() -> None:
    stage2 = STAGE2.read_text(encoding="utf-8")
    assert "Stage-1 TaskRun binding mismatch" in stage2
    assert "approved source Run ID does not match governed evidence" in stage2
    assert "outer-loop next repair round mismatch" in stage2
    assert "outer-loop dispatch round mismatch" in stage2
    assert "failure.get(\"candidate_paths\")" in stage2
    assert 'startswith("governed-repair/")' in stage2
