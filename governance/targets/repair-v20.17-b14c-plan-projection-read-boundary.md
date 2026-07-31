# 目标

- 目标 ID：repair-v20.17-b14c-plan-projection-read-boundary
- 变更标识：portable-repair-v20.17-b14c-plan-projection-read-boundary
- 执行上下文：local-change
- 目标类型：repair

统一 `grounded_execution_plan` 的所有运行时读取入口，使任何消费者都不能绕过 `frozen_plan_definition + plan_run` 权威对而信任伪造或过期的兼容投影。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/plan_projection_contract.py`, `services/agent-service/src/agent_core/lifecycle/plan_execution.py`, `services/agent-service/src/agent_core/lifecycle/workflow_runtime.py`, `services/agent-service/src/agent_core/lifecycle/clarification_runtime.py`, `services/agent-service/src/agent_core/lifecycle/budget.py`, `services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py`, `services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py`, `services/agent-service/src/agent_core/lifecycle/graph_routes.py`, `services/agent-service/src/agent_core/runtime/answer_release_alignment.py`, `services/agent-service/src/agent_core/observability/failure_replay.py`, `services/agent-service/tests/runtime/test_b14c_plan_projection_read_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B14C_PLAN_PROJECTION_READ_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B14C_PLAN_PROJECTION_READ_BOUNDARY.md

## 禁止范围

不得把兼容投影提升为 Plan 写入 Owner；不得允许 Schema v2 使用无 Definition/Run 绑定的兼容投影；不得把 `REJECTED` 临时计划用于最终回答放行；不得恢复 `workflow_plan` 为新线程权威；不得新增跨包依赖循环。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14c-plan-projection-read-boundary.json`
- 验收 ID：`V20-17-B14C-PLAN-READ-001`

伪造完成态必须被正式权威对覆盖；澄清必须绑定正式 Goal；Plan Run 变化必须使旧缓存失效并重新派生；源码只能保留一个投影读取合同；现有临时计划 repair 行为和旧 checkpoint 兼容行为保持不变。

## 基线

红基线：B14c 两条运行时反例在修复前失败，分别证明伪造投影可绕过最终回答校验，以及澄清流程会读取伪造 Goal。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修改 Plan 投影读取合同、投影生成唯一实现及其消费者；没有有效进展时停止并重新规划。
