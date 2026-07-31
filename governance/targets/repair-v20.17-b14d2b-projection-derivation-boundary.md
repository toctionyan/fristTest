# 目标

- 目标 ID：repair-v20.17-b14d2b-projection-derivation-boundary
- 变更标识：portable-repair-v20.17-b14d2b-projection-derivation-boundary
- 执行上下文：local-change
- 目标类型：repair

统一正式 Plan 投影与同回合临时计划中的 Goal 覆盖、Task 状态、Workflow 状态和完成度派生。`workflow_runtime.py` 不再保留与 Kernel 重复的三套私有派生函数。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/plan_projection_contract.py`, `services/agent-service/src/agent_core/lifecycle/workflow_runtime.py`, `services/agent-service/tests/runtime/test_b14d2b_projection_derivation_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B14D2B_PROJECTION_DERIVATION_BOUNDARY.md`
- 新增抽象记录：无

## 禁止范围

不得把临时计划变成正式持久化权威；不得改变 `frozen_plan_definition + plan_run` 的唯一正式 Plan 权威；不得移除同回合计划修复能力；不得把 Goal、Task 或 Workflow 状态写入 Business Service；不得修改事务状态机、能力匹配规则或 State Schema 版本；不得新增跨包依赖循环。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14d2b-projection-derivation-boundary.json`
- 验收 ID：`V20-17-B14D2B-PROJECTION-DERIVATION-001`

Kernel 提供唯一的 Goal/Task/Workflow 派生视图；正式投影和临时 `mark_step_result` / carry-forward 路径都调用它；同一 Goal 的授权暂停不得在正式投影与临时计划之间分别得到 `AWAITING_AUTHORIZATION` 和 `RUNNING`；Lifecycle 源码不再定义三个重复派生函数。

## 基线

红基线：B14d2a 后 Kernel 已将同一 Goal 的授权暂停派生为 `AWAITING_AUTHORIZATION`，但 `workflow_runtime.py` 的重复聚合器仍因同 Goal 的另一步骤为 `PLANNED` 返回 `RUNNING`，同时源码仍存在三套私有派生函数。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修改 Kernel 投影派生视图及 Lifecycle 临时计划消费者；没有有效进展时停止并重新规划。
