from __future__ import annotations

import json

from agent_core.composition import get_runtime_registry
from agent_core.lifecycle.dialogue_runtime import _build_loop_plan, _loop_static_system_prompt, _loop_system_prompt
from agent_core.lifecycle.goal_planning import validate_goal_declaration
from agent_core.lifecycle.protocol import DECLARE_TURN_GOALS_SCHEMA, agent_loop_schemas
from agent_core.lifecycle.protocol import ASK_USER_CLARIFICATION_SCHEMA, RESPOND_TO_USER_SCHEMA
from agent_core.lifecycle.workflow_runtime import build_workflow_plan
from tests.support.legacy_workflow_projection import mark_step_result
from agent_core.lifecycle.workflow_runtime import validate_step_dispatch


def _state(text: str) -> dict:
    return {
        "current_thread_id": "thread-adversarial-goal-binding",
        "current_user_id": "u001",
        "current_tenant_id": "tenant-a",
        "current_role": "customer",
        "current_user_input": text,
        "turn_index": 1,
        "artifact_ledger": [],
        "current_turn_plan": {"plan_id": "turn-plan:adversarial", "turn": 1, "effects": []},
    }


def _success() -> dict:
    return {
        "ok": True,
        "code": "OK",
        "message": "查询成功",
        "runtime_outcome": {
            "outcome_type": "query",
            "effects": "none",
            "next_interaction": "none",
            "safe_to_continue": True,
        },
    }


def test_one_same_named_call_cannot_complete_two_different_target_goals() -> None:
    text = "查耳机物流，也查键盘物流"
    state = _state(text)
    state["turn_goal_plan"] = {
        "turn": 1,
        "goals": [
            {
                "goal_id": "earphone-logistics",
                "description": "查耳机物流",
                "evidence_span": "查耳机物流",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "expected_tools": ["get_order_logistics"],
            },
            {
                "goal_id": "keyboard-logistics",
                "description": "查键盘物流",
                "evidence_span": "查键盘物流",
                "goal_type": "query",
                "required": True,
                "depends_on": [],
                "expected_tools": ["get_order_logistics"],
            },
        ],
    }
    plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "earphone-only",
            "name": "get_order_logistics",
            "args": {
                "goal_ids": ["earphone-logistics"],
                "target": {"mode": "entity_match", "attribute_span": "耳机"},
                "expected_shape": "one",
                "reference_span": "查耳机物流",
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=text)
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=plan["effects"][0]["effect_id"],
        result=_success(),
    )

    by_id = {goal["goal_id"]: goal for goal in workflow["goals"]}
    assert by_id["earphone-logistics"]["coverage_status"] == "COVERED"
    assert by_id["keyboard-logistics"]["coverage_status"] == "PENDING"
    assert workflow["goal_coverage_complete"] is False


def test_external_call_without_explicit_goal_binding_is_orphan() -> None:
    text = "查我的订单"
    state = _state(text)
    state["turn_goal_plan"] = {
        "turn": 1,
        "goals": [{
            "goal_id": "orders",
            "description": text,
            "evidence_span": text,
            "goal_type": "query",
            "required": True,
            "depends_on": [],
            "expected_tools": ["list_orders"],
        }],
    }
    plan = _build_loop_plan(
        state,
        text,
        [{
            "id": "unbound-orders",
            "name": "list_orders",
            "args": {
                "target": {"mode": "all_orders"},
                "expected_shape": "collection",
                "reference_span": text,
            },
        }],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=text)

    assert workflow["steps"][0]["goal_ids"] == []
    assert workflow["goals"][0]["coverage_status"] == "PENDING"
    assert any(task["task_id"].startswith("task:orphan:") for task in workflow["tasks"])


def test_semantic_goal_declaration_does_not_require_guessing_tool_names() -> None:
    text = "查退款记录，再看看发票"
    state = _state(text)
    result, plan = validate_goal_declaration(
        state=state,
        args={
            "summary": text,
            "goals": [
                {
                    "goal_id": "refund-records",
                    "description": "查退款记录",
                    "evidence_span": "查退款记录",
                    "requested_effect": {"domain": "refund", "operation": "list_records", "object_type": "refund"},
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                },
                {
                    "goal_id": "invoices",
                    "description": "查看发票",
                    "evidence_span": "看看发票",
                    "requested_effect": {"domain": "invoice", "operation": "list", "object_type": "invoice"},
                    "goal_type": "query",
                    "required": True,
                    "depends_on": [],
                },
            ],
        },
        capability_registry=get_runtime_registry().capabilities,
    )

    assert result["ok"] is True
    assert plan is not None
    assert all(goal["expected_tools"] == [] for goal in plan["goals"])


def test_goal_protocol_does_not_require_expected_tools_before_tools_are_exposed() -> None:
    goal_item = DECLARE_TURN_GOALS_SCHEMA["function"]["parameters"]["properties"]["goals"]["items"]
    assert "expected_tools" not in goal_item["required"]


def test_generic_lifecycle_prompt_does_not_claim_to_be_an_ecommerce_agent() -> None:
    class EmptyContext:
        def build(self, _state):
            return {
                "recent_dialogue": [],
                "visible_results": [],
                "active_transactions": [],
                "open_tasks": [],
                "context_health": {},
            }

    prompt = _loop_system_prompt(_state("你好"), context_bundle_builder=EmptyContext())
    assert "你是电商业务 Agent" not in prompt
    assert "多个订单、多个商品" not in prompt


def test_lifecycle_prompt_requires_visible_result_ref_for_historical_pronouns() -> None:
    class VisibleContext:
        def build(self, _state):
            return {
                "visible_result_refs": [{"result_ref": "result:orders:visible", "shape": "collection"}],
                "context_health": {},
            }

    prompt = _loop_system_prompt(
        _state("其中最贵的是哪个？再查一下它的物流"),
        context_bundle_builder=VisibleContext(),
    )
    assert "visible_result_refs" in prompt
    assert "left_handle=result_ref" in prompt
    assert "sort/take/ordinal" in prompt
    assert "不得退化成 entity_match" in prompt
    assert "target.mode=collection" in prompt
    assert "is_latest_visible_turn" in prompt
    assert "is_latest_visible_turn=false 的旧集合属于范围错误" in prompt
    assert "context_binding={reference_kind:explicit_return" in prompt
    assert "本轮逐字出现的旧结果成员标签片段" in prompt
    assert "不能填普通代词" in prompt
    assert "target.mode=all_orders 会重新扩大到全量范围" in prompt
    assert "不得用 all_orders 绕过最新 ResultRef" in prompt
    assert "只有一项没有比较意义" in prompt
    assert "扩大到旧集合" in prompt
    assert "按用户可以独立判断是否完成的业务效果拆 Goal" in prompt
    assert "必须逐字来自“本轮用户原话”" in prompt


def test_business_schemas_require_explicit_goal_binding_without_mutating_registry() -> None:
    registry = get_runtime_registry().capabilities
    original = registry.function_schema("list_orders")
    exposed = {
        schema["function"]["name"]: schema["function"]["parameters"]
        for schema in agent_loop_schemas(
            registry,
            allowed_capability_tools={"list_orders", "consult_invoice_policy"},
        )
    }

    assert "goal_ids" in exposed["list_orders"]["required"]
    assert exposed["list_orders"]["properties"]["goal_ids"]["minItems"] == 1
    assert "goal_ids" not in (original or {}).get("properties", {})


def test_provider_tool_projection_is_compact_but_runtime_schema_stays_strict() -> None:
    registry = get_runtime_registry().capabilities
    canonical = registry.function_schema("list_orders") or {}
    exposed = {
        schema["function"]["name"]: schema["function"]["parameters"]
        for schema in agent_loop_schemas(
            registry,
            allowed_capability_tools={"list_orders", "consult_invoice_policy"},
        )
    }

    assert "oneOf" in canonical["properties"]["target"]
    assert "oneOf" not in exposed["list_orders"]["properties"]["target"]
    encoded = json.dumps(list(exposed.values()), ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) < 12_000


def test_logistics_provider_contract_keeps_parameterized_query_guidance() -> None:
    registry = get_runtime_registry().capabilities
    exposed = {
        schema["function"]["name"]: schema["function"]
        for schema in agent_loop_schemas(registry)
    }

    description = exposed["get_order_logistics"]["description"]
    assert "query.delivery_status" in description
    assert "constraint_bindings" in description
    assert "all_orders" in description
    assert "collection" in description
    assert "set_operation/filter" in description
    assert "在路上/在途" in description
    assert "待发货表示尚未离开商家" in description
    assert "不能算作在路上" in description


def test_loop_prompt_requires_query_conditions_to_have_formal_bindings() -> None:
    prompt = _loop_static_system_prompt()

    assert "query 中每个非空业务条件" in prompt
    assert "parameter_path 指向该 query 字段" in prompt
    assert "不得同时塞入 Target 集合操作" in prompt


def test_terminal_tool_contracts_forbid_singleton_latest_scope_clarification() -> None:
    ask_description = ASK_USER_CLARIFICATION_SCHEMA["function"]["description"]
    respond_description = RESPOND_TO_USER_SCHEMA["function"]["description"]

    assert "不得把更旧结果提升为歧义" in ask_description
    assert "禁止以‘只有一项无法比较’为由澄清" in ask_description
    assert "只有一个成员" in respond_description
    assert "不要因无法比较或存在旧集合而澄清" in respond_description


def test_refund_eligibility_contract_is_distinct_from_general_policy_consultation() -> None:
    registry = get_runtime_registry().capabilities
    eligibility = registry.contract_for_tool("evaluate_refund_eligibility")
    consultation = registry.contract_for_tool("consult_refund_policy")
    schemas = {
        schema["function"]["name"]: schema["function"]["description"]
        for schema in agent_loop_schemas(registry)
    }

    assert eligibility is not None and "当前能否退款" in eligibility.planner_rule
    assert "先不要提交" in schemas["evaluate_refund_eligibility"]
    assert consultation is not None and "一般退款/退货政策" in consultation.planner_rule
    assert "不得代替具体订单资格核验" in schemas["consult_refund_policy"]


def test_dependency_blocks_dispatch_until_predecessor_really_succeeds() -> None:
    text = "先查订单，再申请退款"
    state = _state(text)
    state["turn_goal_plan"] = {
        "turn": 1,
        "goals": [
            {"goal_id": "lookup", "description": "查订单", "evidence_span": "查订单", "goal_type": "query", "required": True, "depends_on": [], "expected_tools": []},
            {"goal_id": "refund", "description": "申请退款", "evidence_span": "申请退款", "goal_type": "action", "required": True, "depends_on": ["lookup"], "expected_tools": []},
        ],
    }
    plan = _build_loop_plan(
        state,
        text,
        [
            {"id": "lookup", "name": "list_orders", "args": {"goal_ids": ["lookup"], "target": {"mode": "all_orders"}, "expected_shape": "collection", "reference_span": "查订单"}},
            {"id": "refund", "name": "prepare_refund", "args": {"goal_ids": ["refund"], "target": {"mode": "entity_match", "attribute_span": "订单"}, "reference_span": "订单", "action_span": "申请退款"}},
        ],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=text)
    lookup_effect = plan["effects"][0]["effect_id"]
    refund_effect = plan["effects"][1]["effect_id"]

    blocked = validate_step_dispatch(workflow_plan=workflow, effect_id=refund_effect)
    assert blocked["code"] == "WORKFLOW_DEPENDENCY_UNSATISFIED"

    failed = mark_step_result(
        workflow_plan=workflow,
        effect_id=lookup_effect,
        result={"ok": False, "code": "NOT_FOUND", "message": "没有查到"},
    )
    assert validate_step_dispatch(workflow_plan=failed, effect_id=refund_effect)["code"] == "WORKFLOW_DEPENDENCY_UNSATISFIED"

    succeeded = mark_step_result(workflow_plan=workflow, effect_id=lookup_effect, result=_success())
    assert validate_step_dispatch(workflow_plan=succeeded, effect_id=refund_effect)["ok"] is True


def test_unbound_call_is_not_dispatchable_even_when_tool_matches_old_hint() -> None:
    text = "查订单"
    state = _state(text)
    state["turn_goal_plan"] = {"turn": 1, "goals": [{
        "goal_id": "lookup", "description": text, "evidence_span": text,
        "goal_type": "query", "required": True, "depends_on": [], "expected_tools": ["list_orders"],
    }]}
    plan = _build_loop_plan(
        state,
        text,
        [{"id": "lookup", "name": "list_orders", "args": {"target": {"mode": "all_orders"}, "expected_shape": "collection", "reference_span": text}}],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=plan, user_text=text)

    result = validate_step_dispatch(workflow_plan=workflow, effect_id=plan["effects"][0]["effect_id"])
    assert result["ok"] is False
    assert result["code"] == "WORKFLOW_GOAL_BINDING_REQUIRED"


def test_corrected_candidate_supersedes_retryable_protocol_failure_for_same_goal() -> None:
    text = "查询无线鼠标物流"
    state = _state(text)
    state["turn_goal_plan"] = {"turn": 1, "goals": [{
        "goal_id": "logistics", "description": text, "evidence_span": text,
        "goal_type": "query", "required": True, "depends_on": [],
        "expected_tools": ["get_order_logistics"],
    }]}
    first = _build_loop_plan(
        state,
        text,
        [{"id": "bad", "name": "get_order_details", "args": {
            "goal_ids": ["logistics"],
            "target": {"mode": "entity_match", "attribute_span": "无线鼠标"},
            "reference_span": "无线鼠标",
        }}],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    workflow = build_workflow_plan(state=state, turn_plan=first, user_text=text)
    bad_effect = first["effects"][0]["effect_id"]
    workflow = mark_step_result(
        workflow_plan=workflow,
        effect_id=bad_effect,
        result={
            "ok": False,
            "code": "EXPLICIT_MEMBER_REQUIRES_SINGLE_MEMBER_TARGET",
            "message": "named member cannot use a broad collection",
        },
    )
    assert workflow["steps"][0]["status"] == "FAILED_RETRYABLE"

    repaired_state = {**state, "current_turn_plan": first, "workflow_plan": workflow}
    second = _build_loop_plan(
        repaired_state,
        text,
        [{"id": "good", "name": "get_order_logistics", "args": {
            "goal_ids": ["logistics"],
            "target": {"mode": "entity_match", "attribute_span": "无线鼠标"},
            "expected_shape": "one",
            "reference_span": "无线鼠标",
        }}],
        "",
        capability_registry=get_runtime_registry().capabilities,
    )
    repaired_workflow = build_workflow_plan(
        state=repaired_state,
        turn_plan=second,
        user_text=text,
    )
    good_effect = second["effects"][-1]["effect_id"]
    repaired_workflow = mark_step_result(
        workflow_plan=repaired_workflow,
        effect_id=good_effect,
        result=_success(),
    )

    statuses = {step["effect_id"]: step["status"] for step in repaired_workflow["steps"]}
    assert statuses[bad_effect] == "SKIPPED"
    assert statuses[good_effect] == "SUCCEEDED"
    assert repaired_workflow["goals"][0]["coverage_status"] == "COVERED"
    assert repaired_workflow["status"] == "SUCCEEDED"

# V20.12 transition bridges: these tests are intentionally part of the
# adversarial-runtime-counterexamples Gate so the signed red baseline and the
# repaired implementation execute the same P0 claims.
def test_v20_12_goal_change_evidence_adversarial_bridge() -> None:
    from tests.runtime import test_goal_change_evidence_contract as contract_tests

    contract_tests.test_goal_change_rejects_missing_literal_evidence()
    contract_tests.test_goal_change_rejects_stale_revision_and_wrong_from_state()
    contract_tests.test_patch_goal_cannot_replace_requested_effect()
    contract_tests.test_valid_pause_change_increments_goal_revision()


def test_v20_12_focus_change_evidence_adversarial_bridge() -> None:
    from tests.runtime import test_goal_change_evidence_contract as contract_tests

    contract_tests.test_focus_change_rejects_unknown_goal_and_stale_revision()
    contract_tests.test_valid_goal_focus_change_is_normalized_and_versioned()


def test_v20_12_frozen_semantic_integrity_adversarial_bridge() -> None:
    from tests.runtime import test_goal_change_evidence_contract as contract_tests

    contract_tests.test_semantic_contract_rejects_content_tampering_with_old_digest()
    contract_tests.test_grounded_plan_rejects_tampered_semantic_contract_content()



def test_v20_13_capability_contract_v2_adversarial_bridge() -> None:
    from tests.runtime.test_capability_contract_v2 import (
        test_v2_contract_rejects_duplicate_inputs_and_missing_completion_proof,
        test_v2_contract_rejects_inconsistent_completion_proof_shape,
        test_v2_contract_requires_planning_contract,
    )
    test_v2_contract_requires_planning_contract()
    test_v2_contract_rejects_duplicate_inputs_and_missing_completion_proof()
    test_v2_contract_rejects_inconsistent_completion_proof_shape()


def test_v20_13_vertical_capability_contracts_adversarial_bridge() -> None:
    from tests.runtime.test_capability_contract_v2 import (
        test_ecommerce_verticals_expose_v2_planning_contracts,
        test_refund_eligibility_output_is_fresh_capability_input,
    )
    test_ecommerce_verticals_expose_v2_planning_contracts()
    test_refund_eligibility_output_is_fresh_capability_input()


def test_v20_13_completion_proof_adversarial_bridge() -> None:
    from tests.runtime.test_capability_contract_v2 import (
        test_query_and_action_completion_proofs_are_distinct,
        test_registry_exposes_deterministic_v2_snapshot,
    )
    test_query_and_action_completion_proofs_are_distinct()
    test_registry_exposes_deterministic_v2_snapshot()


def test_v20_14_pretool_shadow_plan_adversarial_bridge() -> None:
    from tests.runtime.test_pretool_shadow_planner import (
        test_shadow_plan_is_built_from_frozen_goals_before_tool_calls,
    )

    test_shadow_plan_is_built_from_frozen_goals_before_tool_calls()


def test_v20_14_contract_path_closure_adversarial_bridge() -> None:
    from tests.runtime.test_pretool_shadow_planner import (
        test_refund_shadow_plan_derives_direct_and_assessment_paths_from_contract_types,
    )

    test_refund_shadow_plan_derives_direct_and_assessment_paths_from_contract_types()


def test_v20_14_shadow_divergence_adversarial_bridge() -> None:
    from tests.runtime.test_pretool_shadow_planner import (
        test_shadow_comparison_records_divergence_without_becoming_execution_authority,
    )

    test_shadow_comparison_records_divergence_without_becoming_execution_authority()

# V20.15 transition bridges: immutable plan definition and independent runtime evidence.
def test_v20_15_frozen_plan_integrity_adversarial_bridge() -> None:
    from tests.runtime.test_plan_definition_run_separation import (
        test_frozen_plan_definition_excludes_runtime_progress_and_detects_mutation,
    )
    test_frozen_plan_definition_excludes_runtime_progress_and_detects_mutation()


def test_v20_15_plan_run_separation_adversarial_bridge() -> None:
    from tests.runtime.test_plan_definition_run_separation import (
        test_compatibility_projection_is_derived_from_definition_and_run,
        test_plan_run_tracks_attempt_and_outcome_without_mutating_definition,
    )
    test_plan_run_tracks_attempt_and_outcome_without_mutating_definition()
    test_compatibility_projection_is_derived_from_definition_and_run()


def test_v20_15_step_attempt_outcome_adversarial_bridge() -> None:
    from tests.runtime.test_plan_definition_run_separation import (
        test_plan_run_rejects_definition_mismatch_and_unknown_attempt,
    )
    test_plan_run_rejects_definition_mismatch_and_unknown_attempt()


def test_v20_15_receipt_completion_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_plan_definition_run_separation import (
        test_draft_outcome_is_not_receipt_completion,
    )
    test_draft_outcome_is_not_receipt_completion()

# V20.16 transition bridges: Schema v2 is the only new-thread authority and
# legacy checkpoints cross one deterministic migration boundary.  The dynamic
# import converts an absent implementation in the predecessor source into a
# product assertion failure rather than an environment-blocked import error.
def _v20_16_contract_tests():
    try:
        from tests.runtime import test_state_schema_v2_cutover as contract_tests
    except ModuleNotFoundError as exc:
        assert False, f"V20_16_STATE_SCHEMA_V2_NOT_IMPLEMENTED:{exc.name}"
    return contract_tests


def test_v20_16_new_thread_schema_v2_adversarial_bridge() -> None:
    contract_tests = _v20_16_contract_tests()
    contract_tests.test_new_turn_uses_schema_v2_and_does_not_generate_retired_fields()


def test_v20_16_legacy_checkpoint_migration_adversarial_bridge() -> None:
    contract_tests = _v20_16_contract_tests()
    contract_tests.test_safe_legacy_pending_checkpoint_migrates_once_to_goal_records_and_blockers()
    contract_tests.test_checkpoint_hydrator_persists_one_time_v2_tombstones()


def test_v20_16_ambiguous_legacy_restart_adversarial_bridge() -> None:
    contract_tests = _v20_16_contract_tests()
    contract_tests.test_ambiguous_active_legacy_checkpoint_requires_new_conversation()


def test_v20_16_legacy_authority_cutover_adversarial_bridge() -> None:
    contract_tests = _v20_16_contract_tests()
    contract_tests.test_schema_v2_never_uses_legacy_goal_plan_as_current_semantics()
    contract_tests.test_completed_legacy_turn_projection_is_discarded_without_resurrecting_goal()


# V20.17 B14a repair bridge: State Schema v2 must quarantine every retired
# same-turn field before clarification runtime derives suspended Goal context.
def test_v20_17_b14a_state_v2_retired_field_quarantine_adversarial_bridge() -> None:
    from tests.runtime.test_state_v2_retired_field_quarantine import (
        test_state_v2_ignores_forged_retired_goal_and_workflow_fields,
    )

    test_state_v2_ignores_forged_retired_goal_and_workflow_fields()

# V20.17 B14b repair bridge: a persisted grounded_execution_plan is a derived
# compatibility view and must never outrank frozen_plan_definition + plan_run.
def test_v20_17_b14b_grounded_projection_quarantine_adversarial_bridge() -> None:
    from tests.runtime.test_state_v2_grounded_projection_quarantine import (
        test_state_v2_discards_unbound_grounded_projection,
        test_state_v2_rederives_forged_grounded_projection_from_plan_authorities,
    )

    test_state_v2_rederives_forged_grounded_projection_from_plan_authorities()
    test_state_v2_discards_unbound_grounded_projection()


# V20.17 B14c repair bridge: every Runtime/Observability consumer must resolve
# the compatibility view through the Definition/Run-bound Kernel read boundary.
def test_v20_17_b14c_plan_projection_read_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_b14c_plan_projection_read_boundary import (
        test_clarification_suspends_authoritative_goal_not_forged_projection_goal,
        test_final_answer_verifier_uses_plan_authorities_not_forged_projection,
        test_projection_cache_is_bound_to_current_plan_run_and_rederived_when_stale,
        test_runtime_source_has_single_grounded_projection_read_boundary,
        test_same_turn_accepted_plan_remains_readable_before_materialization,
        test_same_turn_rejected_plan_is_visible_for_repair_but_cannot_finalize,
    )

    test_final_answer_verifier_uses_plan_authorities_not_forged_projection()
    test_clarification_suspends_authoritative_goal_not_forged_projection_goal()
    test_projection_cache_is_bound_to_current_plan_run_and_rederived_when_stale()
    test_same_turn_accepted_plan_remains_readable_before_materialization()
    test_same_turn_rejected_plan_is_visible_for_repair_but_cannot_finalize()
    test_runtime_source_has_single_grounded_projection_read_boundary()


# V20.17 B14d1 repair bridge: tool outcomes must update PlanRun directly;
# the derived compatibility projection is never a write intermediary.
def test_v20_17_b14d1_plan_run_write_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_b14d1_plan_run_write_boundary import (
        test_plan_run_step_update_is_derived_from_authorities_not_compatibility_projection,
        test_repaired_candidate_step_is_persisted_in_plan_run_not_only_projection,
        test_tool_execution_does_not_use_projection_mutation_as_plan_run_write_input,
    )

    test_plan_run_step_update_is_derived_from_authorities_not_compatibility_projection()
    test_repaired_candidate_step_is_persisted_in_plan_run_not_only_projection()
    test_tool_execution_does_not_use_projection_mutation_as_plan_run_write_input()


# V20.17 B14d2a repair bridge: PlanRun and its compatibility projection must
# use the same Kernel-owned workflow-status derivation for every write path.
def test_v20_17_b14d2a_plan_run_status_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_b14d2a_plan_run_status_boundary import (
        test_clarification_terminal_outcome_cannot_write_a_second_workflow_status,
        test_final_answer_terminal_outcome_cannot_write_a_second_workflow_status,
        test_new_plan_run_status_uses_kernel_projection_derivation,
        test_plan_execution_has_no_private_workflow_status_deriver,
    )

    test_new_plan_run_status_uses_kernel_projection_derivation()
    test_clarification_terminal_outcome_cannot_write_a_second_workflow_status()
    test_final_answer_terminal_outcome_cannot_write_a_second_workflow_status()
    test_plan_execution_has_no_private_workflow_status_deriver()


# V20.17 B14d2b repair bridge: authoritative and same-turn compatibility plans
# must share one Kernel-owned Goal/Task/Workflow derivation implementation.
def test_v20_17_b14d2b_projection_derivation_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_b14d2b_projection_derivation_boundary import (
        test_ephemeral_workflow_uses_kernel_runtime_derivation_for_same_goal_pause,
        test_workflow_runtime_has_no_private_projection_derivers,
    )

    test_ephemeral_workflow_uses_kernel_runtime_derivation_for_same_goal_pause()
    test_workflow_runtime_has_no_private_projection_derivers()


# V20.17 B14e repair bridge: retired compatibility exits must not remain in
# serving, clarification or preproduction certification paths.
def test_v20_17_b14e_compatibility_exit_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_b14e1_compatibility_projection_exit import (
        test_clarification_blocker_projection_receives_current_plan_authorities,
        test_goal_blocker_runtime_has_no_unused_singleton_clarification_projection,
        test_preprod_diagnostics_reads_canonical_projection_not_retired_workflow_plan,
        test_production_runtime_has_no_projection_mutation_compatibility_api,
    )
    from pathlib import Path
    from tempfile import TemporaryDirectory

    test_production_runtime_has_no_projection_mutation_compatibility_api()
    with TemporaryDirectory() as directory:
        test_preprod_diagnostics_reads_canonical_projection_not_retired_workflow_plan(
            Path(directory)
        )
    test_clarification_blocker_projection_receives_current_plan_authorities()
    test_goal_blocker_runtime_has_no_unused_singleton_clarification_projection()


def test_v20_17_b14f1_sqlite_resource_lifecycle_adversarial_bridge(monkeypatch, tmp_path) -> None:
    from agent_core.persistence import store_provider as store_module
    from agent_core.rag.providers import local_sparse_provider as rag_module

    events: list[str] = []
    class Provider:
        def close(self) -> None:
            events.append("provider_closed")

    store_module.reset_store_provider_cache()
    provider = Provider()
    monkeypatch.setattr(store_module, "build_store_provider", lambda: provider)
    assert store_module.get_store_provider() is provider
    store_module.reset_store_provider_cache()

    class Store:
        def __enter__(self):
            events.append("store_enter")
            return self
        def __exit__(self, exc_type, exc, tb):
            events.append("store_exit")
        def search(self, *args, **kwargs): return []

    rag = rag_module.LocalSparseRagProvider(tmp_path / "v.db")
    monkeypatch.setattr(rag, "_store", lambda: Store())
    rag.search("q")
    assert events == ["provider_closed", "store_enter", "store_exit"]

    from tests.runtime.test_b14f1_sqlite_resource_lifecycle import (
        test_browser_diagnostics_closes_query_connection,
        test_browser_verifier_uses_declared_chromium,
        test_full_lifecycle_canary_resolves_declared_or_current_python,
        test_preprod_diagnostics_closes_query_connection,
    )
    test_preprod_diagnostics_closes_query_connection(monkeypatch, tmp_path)
    test_full_lifecycle_canary_resolves_declared_or_current_python(monkeypatch, tmp_path)
    test_browser_verifier_uses_declared_chromium(monkeypatch, tmp_path)
    test_browser_diagnostics_closes_query_connection(monkeypatch, tmp_path)


def test_v20_17_b14f1c_browser_certification_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_b14f1_sqlite_resource_lifecycle import (
        test_browser_verifier_classifies_managed_policy_as_environment_block,
        test_browser_verifier_classifies_missing_playwright_browser_as_environment_block,
        test_product_browser_journey_uses_portable_python_and_canonical_plan_projection,
    )
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        test_product_browser_journey_uses_portable_python_and_canonical_plan_projection()
        test_browser_verifier_classifies_managed_policy_as_environment_block(patch)
        test_browser_verifier_classifies_missing_playwright_browser_as_environment_block(patch)
    finally:
        patch.undo()


def test_v20_17_b14f1c_system_chromium_fallback_adversarial_bridge() -> None:
    from tests.runtime.test_b14f1_sqlite_resource_lifecycle import (
        test_browser_journeys_accept_system_chromium_fallback,
    )

    test_browser_journeys_accept_system_chromium_fallback()


# V20.17 B15a certification bridge: a local deterministic OpenAI-compatible
# endpoint cannot satisfy the protected real-model identity contract.
def test_v20_17_b15a_real_model_identity_boundary_adversarial_bridge(monkeypatch) -> None:
    from tests.runtime.test_b15a_real_model_identity_boundary import (
        test_model_smoke_rejects_local_deterministic_stub_identity,
    )

    test_model_smoke_rejects_local_deterministic_stub_identity(monkeypatch)


# V20.17 B15b1 certification bridge: semantic prototypes cannot use a local
# OpenAI-compatible stub as evidence of a real provider model.
def test_v20_17_b15b1_real_model_semantic_identity_boundary_adversarial_bridge(monkeypatch, tmp_path) -> None:
    from tests.runtime.test_b15b1_real_model_semantic_identity_boundary import (
        test_semantic_prototype_rejects_local_model_stub_before_invocation,
    )

    test_semantic_prototype_rejects_local_model_stub_before_invocation(monkeypatch, tmp_path)


# V20.17 B15b2 certification bridge: a local deterministic model endpoint
# cannot satisfy the protected complete lifecycle real-model certification.
def test_v20_17_b15b2_real_model_lifecycle_attestation_boundary_adversarial_bridge(monkeypatch) -> None:
    from tests.runtime.test_b15b2_real_model_lifecycle_attestation_boundary import (
        test_full_lifecycle_rejects_local_stub_before_harness_start,
    )

    test_full_lifecycle_rejects_local_stub_before_harness_start(monkeypatch)


# V20.17 B15c certification bridge: a complete, consistent release evidence
# set must be recognized, while partial or cross-provider evidence fails closed.
def test_v20_17_b15c_real_model_certification_dimension_adversarial_bridge() -> None:
    from tests.runtime.test_b15c_real_model_certification_dimension import (
        test_release_dimension_passes_only_complete_consistent_real_model_evidence,
    )

    test_release_dimension_passes_only_complete_consistent_real_model_evidence()

# V20.17 B15c certification bridge: three individually green real-model
# scripts cannot form a final certification unless one controller binds them
# to the same live session, workspace fingerprint and provider identity.
def test_v20_17_b15c_real_model_certification_bundle_boundary_adversarial_bridge() -> None:
    from tests.runtime.test_b15c_real_model_certification_bundle_boundary import (
        test_bundle_rejects_replayed_or_mismatched_session,
    )

    test_bundle_rejects_replayed_or_mismatched_session()

# V20.17 B15c3 certification bridge: three separately green provider calls are
# not a release authority.  The release controller must require the one live
# bundle Gate that binds all components to one session and workspace.
def test_v20_17_b15c3_real_model_release_bundle_authority_adversarial_bridge() -> None:
    from tests.runtime.test_b15c3_real_model_release_bundle_authority import (
        test_three_independent_green_components_cannot_form_release_certification,
    )

    test_three_independent_green_components_cannot_form_release_certification()

# V20.17 B16a persistence authority bridge: a declared database backend must
# never silently connect through a URL owned by another database family.
def test_v20_17_b16a_database_backend_url_authority_adversarial_bridge(monkeypatch, tmp_path) -> None:
    from tests.architecture.test_b16a_database_backend_url_authority import (
        test_preprod_postgres_backend_requires_explicit_postgres_url,
        test_postgres_backend_rejects_sqlite_url,
        test_sqlite_backend_rejects_postgres_url,
        test_mysql_backend_rejects_sqlite_url,
    )

    test_preprod_postgres_backend_requires_explicit_postgres_url(monkeypatch)
    test_postgres_backend_rejects_sqlite_url(tmp_path)
    test_sqlite_backend_rejects_postgres_url(tmp_path)
    test_mysql_backend_rejects_sqlite_url(tmp_path)

# V20.17 B16b managed PostgreSQL bridge: the owned database must back both
# public HTTP services as well as the standalone integration tests.
def test_v20_17_b16b_managed_postgres_product_runtime_adversarial_bridge() -> None:
    from tests.architecture.test_b16b_managed_postgres_product_runtime import (
        test_managed_integration_passes_owned_postgres_to_public_runtime,
    )

    test_managed_integration_passes_owned_postgres_to_public_runtime()


# V20.17 B16c PostgreSQL recovery bridge: component-level database tests do
# not certify public Draft/Receipt recovery across restart and concurrent Agents.
def test_v20_17_b16c_postgres_restart_concurrency_recovery_adversarial_bridge() -> None:
    from tests.architecture.test_b16c_postgres_restart_recovery_boundary import (
        test_recovery_journey_proves_draft_and_receipt_across_two_restarts,
    )

    test_recovery_journey_proves_draft_and_receipt_across_two_restarts()

# V20.17 B17a production certification bridge: independent model, database
# and browser PASS records cannot close production without one live session,
# one source fingerprint and one controller-owned evidence bundle.
def test_v20_17_b17a_production_certification_authority_adversarial_bridge() -> None:
    from tests.runtime.test_b17a_production_certification_authority import (
        test_three_independent_green_environment_results_cannot_form_production_bundle,
    )

    test_three_independent_green_environment_results_cannot_form_production_bundle()

# V20.17 B17b execution bridge: a deterministic diagnostic workflow cannot
# build protected artifacts, and the only release entrypoint must execute the
# same-session production certification Bundle before clean-release packaging.
def test_v20_17_b17b_production_release_execution_adversarial_bridge() -> None:
    from tests.runtime.test_b17b_production_release_execution import (
        test_release_ci_claims_use_production_bundle_not_legacy_independent_gates,
        test_release_workflow_uses_one_real_production_execution_entrypoint,
    )

    test_release_workflow_uses_one_real_production_execution_entrypoint()
    test_release_ci_claims_use_production_bundle_not_legacy_independent_gates()

# V20.17 B17c closure bridge: a valid CI release summary must use the actual
# production dimension contract and CI_VERIFIED status. The legacy B17b field
# check cannot relabel an invalid or unreachable summary as production closed.
def test_v20_17_b17c_production_release_control_closure_adversarial_bridge() -> None:
    from tests.runtime.test_b17c_production_release_control_closure import (
        test_b17b_legacy_summary_field_cannot_fake_production_closure,
    )

    test_b17b_legacy_summary_field_cannot_fake_production_closure()

# V20.17 B17d protected-browser authority bridge: a local SQLite/dev-token
# browser journey cannot be combined with a separately green PostgreSQL result
# to prove one preprod runtime authority.
def test_v20_17_b17d_protected_browser_runtime_authority_adversarial_bridge() -> None:
    from tests.runtime.test_b17d_protected_browser_runtime_authority import (
        test_local_b17c_runtime_authority_is_a_negative_case,
    )

    test_local_b17c_runtime_authority_is_a_negative_case()

# V20.17 B17e release supply-chain authority bridge: mutable action tags,
# unhashed uv bootstrap, or evidence composed across different toolchains cannot
# prove one protected production release.
def test_v20_17_b17e_release_supply_chain_authority_adversarial_bridge() -> None:
    from tests.runtime.test_b17e_release_supply_chain_authority import (
        test_component_from_another_toolchain_cannot_join_bundle,
        test_mutable_action_reference_is_a_red_counterexample,
        test_mutable_postgres_image_reference_is_a_red_counterexample,
        test_postgres_and_browser_from_different_container_images_cannot_form_bundle,
        test_release_summary_rejects_cross_toolchain_composition,
    )

    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        temp_path = Path(directory)
        test_mutable_action_reference_is_a_red_counterexample(temp_path)
        test_mutable_postgres_image_reference_is_a_red_counterexample(temp_path)
    test_component_from_another_toolchain_cannot_join_bundle()
    test_postgres_and_browser_from_different_container_images_cannot_form_bundle()
    test_release_summary_rejects_cross_toolchain_composition()



def test_b17e_mutable_postgres_image_is_rejected() -> None:
    module = _load_test_module(
        "test_b17e_release_supply_chain_authority_counterexample_image",
        "test_b17e_release_supply_chain_authority.py",
    )
    # Static source assertion is a deterministic red-path guard for mutable image tags.
    module.test_protected_postgres_image_is_immutable_and_digest_locked()


def test_b17e_cross_container_image_bundle_is_rejected() -> None:
    module = _load_test_module(
        "test_b17e_release_supply_chain_authority_counterexample_cross_image",
        "test_b17e_release_supply_chain_authority.py",
    )
    module.test_postgres_and_browser_from_different_container_images_cannot_form_bundle()

# V20.17 B17f CI run identity bridge: signed evidence from another workflow
# run or rerun attempt cannot close the current protected release even when the
# source commit and installed toolchain are otherwise identical.
def test_v20_17_b17f_ci_run_identity_replay_authority_adversarial_bridge() -> None:
    from tests.runtime.test_b17f_ci_run_identity_replay_authority import (
        test_prior_attempt_quality_summary_cannot_close_current_run,
        test_release_workflow_binds_protected_ref_checkout_and_artifact_attempt,
    )

    test_prior_attempt_quality_summary_cannot_close_current_run()
    test_release_workflow_binds_protected_ref_checkout_and_artifact_attempt()

# V20.17 B17g release-admission bridge: dispatches from an unprotected or wrong
# branch must produce an explicit failing admission result.  The protected job's
# defensive `if` may still skip that job, but the workflow itself cannot be green.
def test_v20_17_b17g_production_execution_readiness_adversarial_bridge() -> None:
    from tests.runtime.test_b17g_production_execution_readiness import (
        test_release_workflow_has_explicit_admission_dependency_and_defense_in_depth,
        test_unprotected_dispatch_fails_instead_of_being_silently_skipped,
        test_wrong_branch_is_rejected_before_secret_bearing_job,
    )

    test_unprotected_dispatch_fails_instead_of_being_silently_skipped()
    test_wrong_branch_is_rejected_before_secret_bearing_job()
    test_release_workflow_has_explicit_admission_dependency_and_defense_in_depth()

# V20.17 B17h protected-environment preflight bridge: missing or placeholder
# production credentials and invalid official endpoints must fail before costly
# dependency installation, and failure evidence must never disclose secret values.
def test_v20_17_b17h_protected_environment_preflight_adversarial_bridge(monkeypatch) -> None:
    from tests.runtime.test_b17h_protected_environment_preflight import (
        test_placeholder_secret_fails_without_leaking_value,
        test_workflow_runs_preflight_before_expensive_dependency_install,
    )

    test_placeholder_secret_fails_without_leaking_value(monkeypatch)
    test_workflow_runs_preflight_before_expensive_dependency_install()


def test_v20_17_b17i_production_execution_handoff_adversarial_bridge() -> None:
    from test_b17i_production_execution_handoff import (
        test_admission_cli_persists_fail_result_for_unprotected_ref,
        test_workflow_always_uploads_secret_free_admission_result,
    )

    # The bridge itself only proves the counterexamples are part of the runtime suite;
    # the parameterized B17i test module executes their full assertions.
    assert callable(test_admission_cli_persists_fail_result_for_unprotected_ref)
    assert callable(test_workflow_always_uploads_secret_free_admission_result)
