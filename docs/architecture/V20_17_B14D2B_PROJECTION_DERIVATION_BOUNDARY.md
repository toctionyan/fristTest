# V20.17 B14d2b 投影派生统一边界

## 问题

正式 Definition/Run 投影在 Kernel 中派生 Goal、Task 和 Workflow 状态；同回合临时计划和旧兼容 carry-forward 在 Lifecycle 中复制了相同算法。B14d2a 调整 Kernel 状态优先级后，重复实现立即出现行为分裂。

## 唯一职责

Kernel 的 `derive_plan_runtime_view` 唯一负责根据 Goals、Tasks 和 Steps 生成：

- Goal coverage；
- Goal coverage complete；
- Task status；
- Workflow status。

正式投影和临时计划只消费该结果，不再各自解释状态。

## 替换或删除项

删除 Lifecycle 中 `_refresh_goal_coverage`、`_aggregate_workflow_status`、`_sync_task_statuses` 和仅为这些函数服务的状态转换包装。

## 删除证据

结构反例扫描 `workflow_runtime.py`，要求三个重复函数均不存在，并确认两个临时计划更新入口调用 Kernel 派生视图。

## 验证

红基线固定同一 Goal 的授权暂停分裂：Kernel 为 `AWAITING_AUTHORIZATION`，Lifecycle 为 `RUNNING`。修复后正式投影、临时计划和 carry-forward 使用完全相同的派生结果。
