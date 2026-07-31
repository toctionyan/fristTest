# V20.17 B14d1 PlanRun 步骤结果写入边界

## 新增项

新增 `derive_plan_run_step_update` 与 `complete_plan_run_step_result` 两个 Lifecycle 合同，并扩展 `complete_step_attempt` 支持同一次原子写入中的相关步骤更新。它们只接收冻结计划定义、当前 PlanRun、真实工具结果和步骤尝试 ID，不接收兼容投影。

## 唯一职责

该边界唯一负责把工具结果转成 PlanRun 的运行时字段：步骤状态、失败类型、结果摘要、验证证据，以及同一 Goal 中已被成功候选替代的可修复失败步骤。计划结构仍由 `frozen_plan_definition` 负责，业务事实仍由 Business Service 和事务 Runtime 负责。

唯一写入链为：

```text
FrozenPlanDefinition + PlanRun + Tool Result
                  ↓
derive_plan_run_step_update
                  ↓
complete_plan_run_step_result
                  ↓
PlanRun 原子写入当前步骤和相关修复步骤
                  ↓
Kernel 单向生成 grounded_execution_plan
```

## 替换或删除项

替换工具执行 Runtime 中的旧链路：

```text
Tool Result → mark_step_result(grounded_execution_plan) → complete_step_attempt
```

`tool_execution_runtime.py` 不再调用 `mark_step_result`，也不再从兼容投影提取 `status`、`failure_type`、`result_summary` 或 `verification` 作为 PlanRun 写入输入。`mark_step_result` 暂时仅保留给同回合临时计划和现有兼容测试，内部复用同一个结果派生函数。

## 删除证据

- `tool_execution_runtime.py` 中 `mark_step_result(` 命中数为 0。
- 正式工具执行只调用 `complete_plan_run_step_result`。
- 新反例证明旧实现重新投影后会把 `SKIPPED` 退回 `FAILED_RETRYABLE`；修复后 PlanRun 自身保存 `candidate_repaired=true` 与 `superseded_by_effect_id`。
- `grounded_execution_plan` 不在 PlanRun 写入合同参数中。

## 验证

- B14d1 定向反例：3 passed。
- Workflow、Goal Coverage、多意图、Goal Binding、B14c 投影边界相关回归：96 passed。
- Runtime 测试：251 passed。
- 标准隔离 Python 套件：Agent 667 passed；Business 28 passed；0 skipped。
- 正式 Quick Gate 使用同工作区红基线，要求声明从 `FAILED` 转为 `VERIFIED`。

## 权威边界

- 结构权威：`frozen_plan_definition`
- 执行进度权威：`plan_run`
- 工具事实：真实工具 `result`
- 兼容视图：`grounded_execution_plan`，仅派生、只读
- 业务事实与事务最终裁决：Business Service 与 Transaction Runtime
