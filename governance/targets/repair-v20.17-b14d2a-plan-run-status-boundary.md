# 目标

- 目标 ID：repair-v20.17-b14d2a-plan-run-status-boundary
- 变更标识：portable-repair-v20.17-b14d2a-plan-run-status-boundary
- 执行上下文：local-change
- 目标类型：repair

统一 `plan_run.status` 与 `grounded_execution_plan.status` 的派生出口。PlanRun 的创建、修订、步骤开始、步骤完成和终端 Goal 记录都必须调用 Kernel 中同一个状态派生合同，不得在 Lifecycle 内保留第二套 Workflow 状态机。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/plan_projection_contract.py`, `services/agent-service/src/agent_core/lifecycle/plan_execution.py`, `services/agent-service/tests/runtime/test_b14d2a_plan_run_status_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B14D2A_PLAN_RUN_STATUS_BOUNDARY.md`
- 新增抽象记录：无

## 禁止范围

不得改变现有兼容投影对无步骤终端 Goal 的公开状态语义；不得把 `grounded_execution_plan` 变为写入权威；不得把用户澄清、最终回答或业务结果变成新的 Plan 状态 Owner；不得修改 Business Service、事务状态机、能力匹配规则或 State Schema 版本；不得新增跨包依赖循环。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14d2a-plan-run-status-boundary.json`
- 验收 ID：`V20-17-B14D2A-PLAN-RUN-STATUS-001`

新 PlanRun、澄清终端结果和最终回答终端结果的 `plan_run.status` 必须与同一 Definition/Run 权威对生成的投影状态完全一致；`plan_execution.py` 不再保留 `_run_status` 或直接写入 `NEEDS_INPUT`、`SUCCEEDED` 的第二套派生逻辑。

## 基线

红基线：无步骤计划创建后，PlanRun 为 `PLANNED` 而投影为 `RUNNING`；记录澄清后 PlanRun 为 `NEEDS_INPUT` 而投影为 `NOT_REQUIRED`；记录最终回答后 PlanRun 为 `SUCCEEDED` 而投影为 `NOT_REQUIRED`。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修改 Kernel 状态派生合同与 PlanRun 写入消费者；没有有效进展时停止并重新规划。
