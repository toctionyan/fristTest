# 目标

- 目标 ID：repair-v20.17-b14d1-plan-run-write-boundary
- 变更标识：portable-repair-v20.17-b14d1-plan-run-write-boundary
- 执行上下文：local-change
- 目标类型：repair

将工具结果的步骤状态、失败类型、验证证据和候选修复关系直接写入 `plan_run`，禁止先修改 `grounded_execution_plan` 再把兼容视图反向作为 PlanRun 写入输入。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/plan_execution.py`, `services/agent-service/src/agent_core/lifecycle/workflow_runtime.py`, `services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py`, `services/agent-service/tests/runtime/test_b14d1_plan_run_write_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B14D1_PLAN_RUN_WRITE_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B14D1_PLAN_RUN_WRITE_BOUNDARY.md

## 禁止范围

不得把 `grounded_execution_plan` 提升为写入 Owner；不得让工具结果绕过 `frozen_plan_definition + plan_run`；不得丢失被成功候选替代的 `FAILED_RETRYABLE` 步骤；不得修改 Business Service 权威、事务状态机、能力匹配规则或 State Schema 版本；不得新增跨包依赖循环。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14d1-plan-run-write-boundary.json`
- 验收 ID：`V20-17-B14D1-PLAN-RUN-WRITE-001`

工具执行 Runtime 源码不再调用 `mark_step_result` 作为 PlanRun 写入输入；步骤结果只从冻结定义、当前 PlanRun 与真实工具结果派生；成功修复候选后，之前的可修复失败步骤必须在 PlanRun 中持久化为 `SKIPPED`，重新投影后仍保持该状态。

## 基线

红基线：B14c 中三条反例失败。其一没有 PlanRun 写入派生边界；其二工具执行仍先调用 `mark_step_result`；其三兼容投影中的 `SKIPPED` 修复证据在重新从 PlanRun 投影后退回 `FAILED_RETRYABLE`。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修改 PlanRun 步骤结果写入合同及工具执行消费者；没有有效进展时停止并重新规划。
