# 目标

- 目标 ID：migration-v20.12-goal-change-evidence
- 变更标识：portable-migration-v20.12-goal-change-evidence
- 执行上下文：local-change
- 目标类型：migration

为历史 Goal 生命周期、Goal Patch、Goal 替换和 UI Focus 变更建立强类型、原文证据、revision 并发校验；让冻结语义合同在每次消费前重新校验内容摘要，阻止内容修改后继续执行。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/**, services/agent-service/tests/runtime/**, docs/architecture/**`
- 新增抽象记录：`docs/architecture/V20_12_GOAL_CHANGE_EVIDENCE.md`

## 禁止范围

不得修改 Skill、Quality Policy、Judge、既有 Evidence、Business Service、事务主链、Capability 身份、Planner 或公开 API；不得由程序根据关键词决定暂停、取消或修改哪个 Goal。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.12-goal-change-evidence.json`
- 验收 ID：`GOAL-CHANGE-EVIDENCE-001`, `FOCUS-CHANGE-EVIDENCE-001`, `FROZEN-SEMANTIC-INTEGRITY-001`

缺失原文证据、revision 冲突、from 状态不一致、非法 requested_effect Patch、未知 Focus、冻结合同内容篡改都必须被拒绝；合法暂停与 Focus 变更必须通过并增加 revision。

## 基线

在 V20.11.2 浏览器阶段候选源码上仅加入本目标反例，记录真实红基线。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修改语义状态变更合同、Goal 生命周期应用、Focus 应用和摘要完整性 Owner；不得扩大到 Planner 或 Capability Contract。
