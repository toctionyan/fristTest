# SUPERSEDED：历史抽象记录

> 本记录已被 [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md) 中的 State Schema v2 权威链替代。 `TurnGoalPlan / WorkflowPlan` 不再是新线程的当前持久权威；本文仅保留迁移背景，不得作为恢复旧主链的依据。

## TurnGoalPlan

- 文件：`agent_core/lifecycle/goal_planning.py`。
- 唯一职责：在领域工具开放前保存本 user turn 的全部显式目标、字面证据、类型、依赖和候选覆盖工具。
- 独立校验：preprod / production 的 `GoalAlignmentVerifier` 只判断声明是否遗漏目标；不能选择工具、解析资源或裁决业务事实。
- 替换项：替换“模型直接选工具，Runtime 无法知道是否漏意图”的隐式规划方式。
- 权威边界：只拥有语义编排权；不是业务事实、权限、目标解析或写入授权。

## WorkflowPlan

- 文件：`agent_core/lifecycle/workflow_contracts.py` 与 `workflow_runtime.py`。
- 唯一职责：把已声明目标与本回合候选 Effect 映射为 Goal / Task / Step，记录依赖、状态和失败分类，并在最终回答前验证 Goal Coverage。
- 替换项：替换“多意图只是连续工具调用，完成时只看最后一个工具”的执行方式。
- 当前范围：一个 user turn 内的执行编排；下一 user turn 重新声明目标。
- 非目标：不承担 Durable Workflow、后台调度、多 Worker、执行租约或跨回合恢复。
- 业务边界：不替代 Business Service、CapabilityGate、VisibleResultRef、Draft/Grant/Attempt/Receipt 或 RuntimeOutcome。

## 测试证据

- Runtime Contract Suite：给定候选经过真实 Graph/Gate/Workflow 的确定性合同测试。
- Semantic Goal Oracle Suite：独立 Oracle 检查候选目标和工具是否漏项或替代。
- Protected Real-Model Smoke：逐 turn 检查真实模型的目标声明，不执行业务读写。
