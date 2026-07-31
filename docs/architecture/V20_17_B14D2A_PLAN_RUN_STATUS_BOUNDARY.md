# V20.17 B14d2a PlanRun 状态派生边界

## 问题

B14d1 已统一步骤结果写入，但 Lifecycle 仍通过 `_run_status` 和终端分支独立计算 `plan_run.status`。Kernel 同时从 Definition/Run 重新计算兼容投影状态，导致同一权威对出现两个 Workflow 状态。

## 唯一职责

Kernel 的 Plan 投影合同唯一负责从 `frozen_plan_definition + plan_run` 派生 Workflow 状态。PlanRun 的所有写入路径只调用该合同并保存结果，不自行解释 Goal、Step 或终端工具。

## 替换或删除项

- 删除 Lifecycle 私有 `_run_status` 聚合器；
- 删除澄清和最终回答分支直接写入 `NEEDS_INPUT`、`SUCCEEDED`；
- 创建、修订、步骤开始、步骤完成和终端 Goal 记录统一调用 Kernel 派生函数。

## 删除证据

结构反例扫描 `plan_execution.py`，要求不存在 `_run_status` 定义和终端状态直接写入。

## 验证

红基线固定创建、澄清和最终回答三种状态分裂；修复后 PlanRun 与兼容投影必须对同一权威对给出相同状态，同时保持现有投影公开语义不变。
