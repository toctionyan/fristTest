# SUPERSEDED：历史抽象记录

> 本记录已被 [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md) 中的 State Schema v2 权威链替代。 `TurnGoalPlan` 不再是新线程的当前持久权威；本文仅保留迁移背景，不得作为恢复旧主链的依据。

- 新增项：`agent_core/lifecycle/goal_planning.py`、`TurnGoalPlan`、`GoalAlignmentVerifier`、Goal Coverage 合同。
- 唯一职责：在每个 user turn 的领域工具开放前，保存用户显式目标、字面证据、类型、依赖和候选工具，并独立验证是否遗漏目标；不选择工具、不解析资源、不裁决业务事实、不执行写入。
- 替换或删除项：替换“模型直接选择业务工具，Runtime 只能检查已生成 Step、无法发现漏意图”的隐式目标规划方式；删除 `workflow_runtime.py` 中通过中文动作/集合关键词判断 L2 的语义补丁。
- 删除证据：`workflow_runtime.py` 不再存在 `_ACTION_HINTS`、`_MULTI_HINTS` 或 `multi_target_action_language` 关键词分类；领域工具在 `turn_goal_plan` 未通过前不会开放。
- 为什么不能并入现有 Owner：`TurnPlan` 是候选工具审计，`WorkflowPlan` 是 Effect 执行状态，CapabilityGate 是单能力精确性校验；三者都不能表达“用户提出但模型未生成工具的目标”。目标覆盖属于 lifecycle 的本轮编排前置合同。
- 验证：`test_goal_coverage_runtime.py`、独立 Semantic Goal Oracle Suite、Runtime Contract Suite、preproduction real-model goal declaration smoke、版本/Skill/架构 Gate。
