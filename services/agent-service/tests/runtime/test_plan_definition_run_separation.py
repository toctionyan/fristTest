from copy import deepcopy

import pytest


def _legacy_plan():
    return {
        "plan_contract_version": "grounded-execution-plan@2",
        "workflow_id": "workflow:test",
        "turn_plan_id": "turn-plan:test",
        "formal_semantic_contract_id": "semantic:test",
        "formal_semantic_digest": "semantic-digest",
        "goal_source": "frozen_semantic_contract",
        "level": "SEQUENTIAL",
        "status": "PLANNED",
        "goals": [
            {
                "goal_id": "goal:refund",
                "required": True,
                "depends_on": [],
                "coverage_status": "PENDING",
            }
        ],
        "tasks": [{"task_id": "task:refund", "goal_ids": ["goal:refund"], "status": "PLANNED"}],
        "steps": [
            {
                "step_id": "step:assess",
                "effect_id": "effect:assess",
                "tool_name": "evaluate_refund_eligibility",
                "capability_id": "ecommerce.refund.eligibility",
                "goal_ids": ["goal:refund"],
                "depends_on": [],
                "required": True,
                "status": "PLANNED",
                "verification": {"goal_effect_role": "support"},
            },
            {
                "step_id": "step:draft",
                "effect_id": "effect:draft",
                "tool_name": "prepare_refund_from_eligibility",
                "capability_id": "ecommerce.refund.prepare_from_eligibility",
                "goal_ids": ["goal:refund"],
                "depends_on": ["effect:assess"],
                "required": True,
                "status": "PLANNED",
                "verification": {
                    "goal_effect_role": "completion",
                    "completion_owner": "transaction_runtime",
                },
            },
        ],
        "reasons": ["multi_step"],
    }


def test_frozen_plan_definition_excludes_runtime_progress_and_detects_mutation():
    from agent_core.lifecycle.plan_execution import (
        freeze_plan_definition,
        validate_frozen_plan_definition,
    )

    definition = freeze_plan_definition(_legacy_plan())
    assert definition["version"] == "frozen-plan-definition@1"
    assert definition["immutable"] is True
    assert all("status" not in step for step in definition["steps"])
    assert validate_frozen_plan_definition(definition)["ok"] is True

    tampered = deepcopy(definition)
    tampered["steps"][0]["tool_name"] = "prepare_refund"
    integrity = validate_frozen_plan_definition(tampered)
    assert integrity["ok"] is False
    assert integrity["code"] == "FROZEN_PLAN_DEFINITION_DIGEST_INVALID"


def test_plan_run_tracks_attempt_and_outcome_without_mutating_definition():
    from agent_core.lifecycle.plan_execution import (
        begin_step_attempt,
        complete_step_attempt,
        create_plan_run,
        freeze_plan_definition,
        validate_plan_run,
    )

    definition = freeze_plan_definition(_legacy_plan())
    original = deepcopy(definition)
    run = create_plan_run(definition, turn_index=4)
    run, attempt = begin_step_attempt(
        definition=definition,
        plan_run=run,
        effect_id="effect:assess",
        tool_name="evaluate_refund_eligibility",
        args={"order_id": "10002"},
        execution_permit={"permit_id": "permit:1"},
    )
    run, outcome = complete_step_attempt(
        definition=definition,
        plan_run=run,
        attempt_id=attempt["attempt_id"],
        result={"ok": True, "code": "ELIGIBLE", "data": {"assessment_id": "assess:1"}},
        step_status="SUCCEEDED",
        failure_type="NONE",
    )

    assert definition == original
    assert run["step_states"]["effect:assess"]["status"] == "SUCCEEDED"
    assert outcome["assessment_id"] == "assess:1"
    assert validate_plan_run(definition=definition, plan_run=run)["ok"] is True


def test_plan_run_rejects_definition_mismatch_and_unknown_attempt():
    from agent_core.lifecycle.plan_execution import (
        complete_step_attempt,
        create_plan_run,
        freeze_plan_definition,
        validate_plan_run,
    )

    definition = freeze_plan_definition(_legacy_plan())
    run = create_plan_run(definition, turn_index=1)
    bad = deepcopy(run)
    bad["plan_definition_digest"] = "other"
    assert validate_plan_run(definition=definition, plan_run=bad)["code"] == "PLAN_RUN_DEFINITION_MISMATCH"

    with pytest.raises(ValueError, match="STEP_ATTEMPT_NOT_FOUND"):
        complete_step_attempt(
            definition=definition,
            plan_run=run,
            attempt_id="attempt:missing",
            result={"ok": False},
            step_status="FAILED_FINAL",
            failure_type="UNKNOWN",
        )


def test_draft_outcome_is_not_receipt_completion():
    from agent_core.lifecycle.plan_execution import (
        begin_step_attempt,
        complete_step_attempt,
        create_plan_run,
        freeze_plan_definition,
    )

    definition = freeze_plan_definition(_legacy_plan())
    run = create_plan_run(definition, turn_index=2)
    run, attempt = begin_step_attempt(
        definition=definition,
        plan_run=run,
        effect_id="effect:draft",
        tool_name="prepare_refund_from_eligibility",
        args={"assessment_id": "assess:1"},
        execution_permit={"permit_id": "permit:2"},
    )
    run, outcome = complete_step_attempt(
        definition=definition,
        plan_run=run,
        attempt_id=attempt["attempt_id"],
        result={
            "ok": True,
            "code": "DRAFT_READY",
            "data": {"draft_id": "draft:1"},
            "runtime_outcome": {"effects": "draft_created", "next_interaction": "open_authority"},
        },
        step_status="AWAITING_AUTHORIZATION",
        failure_type="NONE",
    )

    assert outcome["draft_id"] == "draft:1"
    assert outcome["receipt_id"] is None
    assert outcome["completion_proof"] is False
    assert run["status"] == "AWAITING_AUTHORIZATION"

    # A verified transaction Receipt on the transaction-owned step is the only
    # result shape that may become business completion proof.
    run, receipt_attempt = begin_step_attempt(
        definition=definition,
        plan_run=run,
        effect_id="effect:draft",
        tool_name="prepare_refund_from_eligibility",
        args={"assessment_id": "assess:1"},
        execution_permit={"permit_id": "permit:3"},
    )
    run, receipt_outcome = complete_step_attempt(
        definition=definition,
        plan_run=run,
        attempt_id=receipt_attempt["attempt_id"],
        result={
            "ok": True,
            "code": "COMMITTED",
            "data": {"receipt_id": "receipt:1"},
            "runtime_outcome": {"effects": "business_committed"},
        },
        step_status="SUCCEEDED",
        failure_type="NONE",
    )
    assert receipt_outcome["completion_proof"] is True
    assert receipt_outcome["completion_proof_kind"] == "transaction_receipt"

    # A receipt-shaped value on a non-transaction support step is not authority.
    support_run = create_plan_run(definition, turn_index=3)
    support_run, support_attempt = begin_step_attempt(
        definition=definition,
        plan_run=support_run,
        effect_id="effect:assess",
        tool_name="evaluate_refund_eligibility",
        args={"order_id": "10002"},
        execution_permit={"permit_id": "permit:4"},
    )
    _support_run, fake_receipt = complete_step_attempt(
        definition=definition,
        plan_run=support_run,
        attempt_id=support_attempt["attempt_id"],
        result={"ok": True, "data": {"receipt_id": "receipt:fake"}},
        step_status="SUCCEEDED",
        failure_type="NONE",
    )
    assert fake_receipt["completion_proof"] is False


def test_compatibility_projection_is_derived_from_definition_and_run():
    from agent_core.lifecycle.plan_execution import (
        create_plan_run,
        freeze_plan_definition,
        project_grounded_execution_plan,
    )

    definition = freeze_plan_definition(_legacy_plan())
    run = create_plan_run(definition, turn_index=3)
    projected = project_grounded_execution_plan(definition=definition, plan_run=run)

    assert projected["authority"] == "compatibility_projection_from_frozen_definition_and_plan_run"
    assert projected["plan_definition_id"] == definition["plan_definition_id"]
    assert projected["plan_run_id"] == run["plan_run_id"]
    assert projected["steps"][0]["status"] == "PLANNED"
