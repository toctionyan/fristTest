# 目标

- 目标 ID：repair-v20.17-b14e-compatibility-exit-boundary
- 变更标识：portable-repair-v20.17-b14e-compatibility-exit-boundary
- 执行上下文：local-change
- 目标类型：repair

关闭 Plan/Clarification 迁移完成后仍暴露在生产与认证路径中的兼容出口。生产 Runtime 不再提供直接修改 `grounded_execution_plan` 的 API；preprod 诊断只通过 Kernel 读取正式 Plan 权威；终端澄清创建 Goal Blocker 时必须携带本次刚更新的 Definition/Run；未被使用的 singleton clarification 兼容投影从生产代码删除。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/workflow_runtime.py`, `services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py`, `services/agent-service/src/agent_core/lifecycle/goal_blockers.py`, `services/agent-service/scripts/verify_preprod_full_lifecycle.py`, `services/agent-service/tests/support/legacy_workflow_projection.py`, `services/agent-service/tests/runtime/test_b14e1_compatibility_projection_exit.py`, `services/agent-service/tests/runtime/test_workflow_runtime.py`, `services/agent-service/tests/runtime/test_goal_coverage_runtime.py`, `services/agent-service/tests/runtime/test_multi_intent_runtime.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `services/agent-service/tests/runtime/test_b14d2b_projection_derivation_boundary.py`, `docs/architecture/V20_17_B14E_COMPATIBILITY_EXIT_BOUNDARY.md`
- 新增抽象记录：无

## 禁止范围

不得改变 `frozen_plan_definition + plan_run` 的唯一正式 Plan 权威；不得删除 schema-v1 checkpoint 的一次性迁移能力；不得允许测试兼容辅助模块进入生产导入图；不得修改业务事实、事务状态机、能力匹配规则或 State Schema 版本；不得新增跨包依赖循环。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14e-compatibility-exit-boundary.json`
- 验收 ID：`V20-17-B14E-COMPATIBILITY-EXIT-001`

生产 `workflow_runtime.py` 不再定义或导出投影写入 API；历史单测需要的兼容变换只能存在于 `tests/support`；preprod 图诊断在同时存在正式 Definition/Run 与伪造 `workflow_plan` 时必须展示正式投影；终端澄清构造 blocker 状态时必须包含当前 `common` 中刚更新的 Plan 权威；未使用的 singleton clarification 兼容投影必须从生产源码移除。

## 基线

红基线：B14d2b 后生产模块仍定义 `mark_step_result`，preprod 诊断仍读取退休 `workflow_plan`，终端澄清 blocker 状态未合并本轮更新后的 `common`，且 `goal_blockers.py` 保留零调用的 singleton clarification 兼容函数。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：仅修改上述兼容出口及其回归测试；如发现仍有生产调用依赖旧 API，先迁移到 PlanRun 写入边界，不得保留双写。
