# 目标
- 目标 ID：migration-v20.17-b38-context-reference-goal-coverage
- 变更标识：migration-v20.17-b38-context-reference-goal-coverage
- 执行上下文：local-change
- 目标类型：migration

以 frozen B38 30-path candidate 为唯一产品候选范围，先建立 exact-B36 formal RED baseline；本阶段 baseline 不写产品源码。

## 允许范围
- 允许变更路径：docs/architecture/V20_17_B38_CONTEXT_REFERENCE_GOAL_COVERAGE.md，services/agent-service/.env.example，services/agent-service/src/agent_core/config.py，services/agent-service/src/agent_core/context/context_bundle.py，services/agent-service/src/agent_core/context/reference_resolution.py，services/agent-service/src/agent_core/context/state_projection.py，services/agent-service/src/agent_core/context/visible_result_refs.py，services/agent-service/src/agent_core/kernel/plan_projection_contract.py，services/agent-service/src/agent_core/kernel/semantic_contract.py，services/agent-service/src/agent_core/lifecycle/condition_expression.py，services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py，services/agent-service/src/agent_core/lifecycle/goal_capability_coverage.py，services/agent-service/src/agent_core/lifecycle/goal_granularity.py，services/agent-service/src/agent_core/lifecycle/goal_lifecycle.py，services/agent-service/src/agent_core/lifecycle/goal_outputs.py，services/agent-service/src/agent_core/lifecycle/goal_planning.py，services/agent-service/src/agent_core/lifecycle/pretool_execution_policy.py，services/agent-service/src/agent_core/lifecycle/pretool_planner.py，services/agent-service/src/agent_core/lifecycle/protocol.py，services/agent-service/src/agent_core/lifecycle/semantic_contract.py，services/agent-service/src/agent_core/lifecycle/workflow_runtime.py，services/agent-service/src/agent_core/runtime/capability_effects.py，services/agent-service/src/agent_core/runtime/capability_gate.py，services/agent-service/tests/architecture/test_production_security_contract.py，services/agent-service/tests/context/test_reference_resolution_contract.py，services/agent-service/tests/runtime/test_global_goal_capability_coverage.py，services/agent-service/tests/runtime/test_goal_granularity_and_conditions.py，services/agent-service/tests/runtime/test_pretool_execution_policy.py，services/agent-service/tests/runtime/test_semantic_reference_binding.py，services/agent-service/tests/runtime/test_stage4_goal_output_refs.py
- 新增抽象记录：无

## 禁止范围
不修改 skill-system/**、architecture-skill/**、governance/quality-loop-policy.json、governance/evidence/**、.quality/** 控制证据作为产品实现；baseline 阶段不修改任何产品源码。

## 验收条件
- 最低质量模式：quick
- 声明清单：.quality/stage5g4-c3h-a4/formal-baseline/claim.json
- 验收 ID：B1.PREFERRED.RED

## 基线
baseline = untouched exact B36 product source + immutable B1 BaselineOracleOverlay + exact B1 focused quick policy bytes。

## 修复轮次
- 最大轮次：8
- 当前轮次：1
