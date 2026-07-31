# 目标

- 目标 ID：migration-v20.15-frozen-plan-definition-plan-run
- 变更标识：portable-migration-v20.15-frozen-plan-definition-plan-run
- 执行上下文：local-change
- 目标类型：migration

把计划的不可变结构与运行轨迹分开：`FrozenPlanDefinition` 只保存 Goal、Step、Capability、依赖和摘要；`PlanRun`、`StepAttempt`、`StepOutcome` 只保存运行进度和证据。旧 `grounded_execution_plan/workflow_plan` 仅由新对象投影，不能继续作为正式写入 Owner。

## 允许范围

- 新增抽象记录：docs/architecture/V20_15_FROZEN_PLAN_DEFINITION_PLAN_RUN.md
- 允许变更路径：services/agent-service/src/agent_core/lifecycle/plan_execution.py, services/agent-service/src/agent_core/lifecycle/context_runtime.py, services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py, services/agent-service/src/agent_core/lifecycle/state.py, services/agent-service/src/agent_core/lifecycle/state_contracts.py, services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py, services/agent-service/src/agent_core/lifecycle/workflow_runtime.py, services/agent-service/tests/runtime/test_plan_definition_run_separation.py, services/agent-service/tests/runtime/test_goal_binding_counterexamples.py, docs/architecture/**
- `services/agent-service/src/agent_core/lifecycle/plan_execution.py`
- `services/agent-service/src/agent_core/lifecycle/context_runtime.py`
- `services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py`
- `services/agent-service/src/agent_core/lifecycle/state.py`
- `services/agent-service/src/agent_core/lifecycle/state_contracts.py`
- `services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py`
- `services/agent-service/src/agent_core/lifecycle/workflow_runtime.py`
- `services/agent-service/tests/runtime/test_plan_definition_run_separation.py`
- `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`
- `docs/architecture/**`

## 禁止范围

不得修改 Skill、Quality Policy、Judge、Business Service、事务状态机、Capability Contract v2 业务定义、Presentation、正式前端或旧 checkpoint schema。本阶段不得删除旧兼容字段。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.15-frozen-plan-definition-plan-run.json`
- 验收 ID：FROZEN-PLAN-INTEGRITY-001, PLAN-RUN-SEPARATION-001, STEP-ATTEMPT-OUTCOME-001, RECEIPT-COMPLETION-BOUNDARY-001
- 定义内容变化必须导致摘要验证失败。
- 运行状态不得写回定义。
- 每次执行都可追踪到独立 Attempt/Outcome。
- Draft/Attempt 不得冒充 Receipt。
- 计划版本增加步骤时必须创建新定义，只继承结构未变化步骤的已验证执行证据。
- V20.12、V20.13、V20.14、强上下文、多意图、事务和浏览器代表链不得回归。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后只修改计划定义/运行分离、接入和对应测试，不得提前执行 State v2 或 Legacy Cutover。

## 基线

在 V20.14.0 源码上只加入本阶段目标、Decision、Claims 和反例，记录计划定义/运行尚未分离的真实红基线。
