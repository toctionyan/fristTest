# 目标

- 目标 ID：migration-v20.19-goal-input-binding-r3
- 变更标识：portable-migration-v20.19-goal-input-binding-r3
- 执行上下文：local-change
- 目标类型：migration

Replace model-authored raw Goal dependency edges with verified Goal input bindings and deterministic Typed Goal Graph compilation as the sole dependency authority for new turns, including migration of executable semantic fixtures.

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/protocol.py`, `services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py`, `services/agent-service/src/agent_core/lifecycle/semantic_contract.py`, `services/agent-service/src/agent_core/kernel/semantic_contract.py`, `services/agent-service/src/agent_core/lifecycle/semantic_state_changes.py`, `services/agent-service/src/agent_core/lifecycle/goal_planning.py`, `services/agent-service/src/agent_core/lifecycle/goal_capability_coverage.py`, `services/agent-service/src/agent_core/lifecycle/pretool_planner.py`, `services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py`, `services/agent-service/src/agent_core/lifecycle/goal_outputs.py`, `services/agent-service/src/agent_core/lifecycle/workflow_runtime.py`, `services/agent-service/src/agent_core/goal_graph/contracts.py`, `services/agent-service/src/agent_core/goal_graph/compiler.py`, `services/agent-service/src/agent_core/goal_graph/verifier.py`, `services/agent-service/src/agent_core/goal_graph/dependency_alignment.py`, `services/agent-service/tests/architecture/test_v2019_goal_input_binding_recovery_oracle.py`, `services/agent-service/tests/architecture/test_typed_goal_graph_foundation.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `services/agent-service/tests/runtime/test_stage4_goal_output_refs.py`, `services/agent-service/tests/runtime/test_pretool_execution_policy.py`, `services/agent-service/tests/runtime/test_semantic_dependency_counterfactual_contract.py`, `services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py`, `services/agent-service/tests/support/scripted_chat_model.py`, `services/agent-service/tests/support/conversation_case_runner.py`, `services/agent-service/tests/integration/model_stub.py`, `services/agent-service/scripts/verify_preprod_conversation_smoke.py`, `services/agent-service/tests/runtime/test_wp08_attempt4_graph_semantic_repair.py`, `docs/architecture/GOAL_INPUT_BINDING_V1.md`
- 新增抽象记录：docs/architecture/GOAL_INPUT_BINDING_V1.md

## 禁止范围

No raw depends_on in new provider schema, no legacy fallback for typed contracts, no model verifier graph authority, no unsafe collection projection.

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.19-goal-input-binding-r3.json`
- 验收 ID：`V2019.GOAL_INPUT_BINDING_SINGLE_AUTHORITY_R3`

RED oracle turns green; typed graph/proof/cardinality/target counterexamples pass; executable semantic fixtures validate input bindings and compiled dependencies; Product Quick passes.

## 基线

Baseline: exact origin/main aeb11e0a plus the separately validated Quality Controller repair and immutable test-only RED oracle overlay, before any Goal Input Binding product implementation.

## 修复轮次

- 最大轮次：5
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
