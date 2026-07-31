# V20.17 B14e Compatibility Exit Boundary

## 问题

B14a-d 已建立正式 State/Plan 权威与单向投影，但仍有四个兼容出口可能让旧路径重新进入生产判断：

1. `workflow_runtime.mark_step_result` 允许直接修改兼容投影；
2. preprod 图诊断直接读取退休 `workflow_plan`；
3. 终端澄清在更新 PlanRun 后创建 blocker 时没有携带新的 Definition/Run；
4. `legacy_pending_clarification_projection` 无调用但仍留在生产模块，容易被未来代码重新启用。

## 修复

- 删除生产 `mark_step_result`；历史单测的同回合计划模拟迁入 `tests/support/legacy_workflow_projection.py`。
- preprod 诊断调用 `read_plan_projection`，伪造旧字段不能覆盖正式 Definition/Run。
- 终端澄清的 blocker 输入合并 `state + common + current projection`，确保使用刚更新的 PlanRun。
- 删除未使用的 singleton clarification 兼容投影。

## 权威边界

正式 Plan 权威仍是 `frozen_plan_definition + plan_run`。`grounded_execution_plan` 仅为 Kernel 派生读视图；schema-v1 兼容只存在于一次性迁移与明确的 legacy fallback 中。测试辅助模块不属于生产导入图。
