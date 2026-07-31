# V20.15 FrozenPlanDefinition 与 PlanRun 迁移

## 目标

将不可变计划结构与可变执行轨迹分离。

## 权威边界

- `FrozenPlanDefinition`：结构权威，只包含 Goal/Step/Capability/依赖与摘要。
- `PlanRun`：运行权威，保存步骤状态和当前运行。
- `StepAttempt`：一次边界执行尝试。
- `StepOutcome`：该尝试的结果引用和证明等级。
- `grounded_execution_plan/workflow_plan`：兼容投影，不是写入 Owner。
- Transaction Store：Draft/Grant/Attempt/Receipt 的最终权威，不迁入计划运行对象。

## 本阶段不做

不修改 State Schema 版本，不删除 legacy 字段，不接管事务状态机，不进行真实生产环境认证。

## 新增抽象记录

- 新增项：`agent_core.lifecycle.plan_execution`，提供 `FrozenPlanDefinition`、`PlanRun`、`StepAttempt`、`StepOutcome` 的构建、摘要验证、运行更新和兼容投影。
- 唯一职责：计划定义只拥有不可变结构；PlanRun 只拥有执行进度；Attempt/Outcome 只记录一次边界执行及其结果。事务 Receipt 仍由 Transaction Authority 拥有。
- 替换或删除项：替换 `grounded_execution_plan` 同时承担计划结构和运行状态的双重职责；V20.15 中该旧对象仅由定义与运行投影生成，不再作为正式写入 Owner。
- 删除证据：V20.16 State Schema v2 完成后，停止持久化 `grounded_execution_plan/workflow_plan`，旧 checkpoint 迁移完成且 legacy projection 连续 14 天为 0 后删除兼容投影写入。
- 验证：签名红基线四个 bridge 反例失败；候选中同一反例通过，计划版本升级只继承结构未变化步骤的已验证 Attempt/Outcome，Runtime/Context、107 强上下文和真实 Chromium 代表链通过。
