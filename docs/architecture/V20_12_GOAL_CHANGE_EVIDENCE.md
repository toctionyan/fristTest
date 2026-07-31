# V20.12 Goal Change Evidence 与冻结语义完整性

## 优化目标

本阶段只闭合两个 P0 边界，不扩大到 Capability Planner：

1. 历史 Goal 生命周期、Goal Patch、Goal 替换和 UI Focus 的状态变化必须能由当前用户原话、当前对象引用和当前 revision 证明；
2. 冻结语义合同在每次被 Goal、Plan 或 dispatch 消费前必须重新计算 canonical digest，不能只比较两个存储字段。

模型仍是开放语言语义 Owner。程序不根据“先别管”“继续”“不是这个”等词决定修改哪个 Goal，只验证模型候选是否有证据、引用和并发一致性。

## Goal Change Contract

新轮 `goal_changes` 只接受三种操作：

- `SET_GOAL_LIFECYCLE`
- `PATCH_GOAL`
- `SUPERSEDE_GOAL`

共同必需字段：

- `goal_id`
- `expected_revision`
- `evidence_span`

`evidence_span` 必须是当前用户消息中的连续字面片段。`expected_revision` 必须等于当前 GoalRecord revision；应用时再次比较，防止验证后到写入前发生并发变化。

### 生命周期操作

模型可请求的目标状态仅为：

- `ACTIVE`
- `PAUSED`
- `CANCELLED`

`COMPLETED`、`BLOCKED` 和事务成功不能由模型直接设置，仍由执行、Blocker 或权威结果决定。

### Patch allowlist

`PATCH_GOAL` 只允许：

- `target_candidate`
- `input_candidates`
- `condition`
- `depends_on`

禁止 Patch `requested_effect` 或任意业务事实。用户改变业务目标时必须声明一个新 Goal，并用 `SUPERSEDE_GOAL` 显式替换旧 Goal。

## Focus Change Contract

`focus_change` 不再是可任意合并的字典，只接受：

- `SET_GOAL_FOCUS`
- `SET_INTERACTION_FOCUS`
- `CLEAR_FOCUS`

Focus 变更需要当前 focus revision 和原文证据。Goal Focus 只能引用存在且未终结的 Goal；Interaction Focus 只能引用当前有效的结构化 Interaction。Focus 只是上下文投影，不能授权事务或覆盖业务事实。

## Revision 兼容策略

- V20.12 新 GoalRecord 从 revision 1 开始；
- 旧 checkpoint 中没有 revision 的 GoalRecord 按 revision 1 读取；
- 旧 Focus 没有 revision 时按 revision 0 读取；
- 每次合法状态变化增加 revision；
- 验证和应用都进行 revision 比较。

这只是兼容读取，不是永久迁移方案。旧状态退出由后续 State Schema v2 阶段负责。

## 冻结语义合同完整性

`semantic_digest` 根据去除以下派生字段后的 canonical JSON 计算：

- `semantic_digest`
- `semantic_contract_id`

每次读取正式 Goal、验证 Grounded Plan 或应用 Goal 状态前重新计算摘要，并同时验证 `semantic_contract_id`。修改合同内容但保留旧 digest 会返回 `SEMANTIC_CONTRACT_DIGEST_INVALID`。

## 明确未做

本阶段没有：

- 扩展 Capability `requires / produces / preconditions`；
- 把 Planner 移到 Tool Call 之前；
- 分离 FrozenPlanDefinition 与 PlanRun；
- 修改 Business Service 或事务权威；
- 退出 `turn_goal_plan/workflow_plan` 兼容链；
- 提升 Architecture Baseline。

这些保持为后续独立迁移，避免一次变更同时修改语义、规划和事务三条权威链。

# 新增抽象记录

- 记录 ID：`ABSTRACTION-20260728-semantic-state-changes`
- Change ID：`migration-v20.12-goal-change-evidence`
- 新抽象：`agent_core.lifecycle.semantic_state_changes`
- 规则等级：`STRONG_DEFAULT`

## 唯一职责

该模块只拥有模型提出的历史 Goal/Focus 状态变更候选的确定性验证与原子应用合同：有限操作类型、当前用户原文字面证据、对象存在性、允许字段、乐观 revision 和应用时再次比较。它不解释用户语言、不选择应修改的 Goal、不决定业务事实、不执行 Capability 或事务。

## 净收益

新增一个窄模块和一组有限 Schema，替换原来分别散落在 `goal_planning.py`、`goal_lifecycle.py` 与 `tool_execution_runtime.py` 中的开放字典接受、任意字段合并和仅状态机合法性检查。新增概念成本小于继续让三个运行时 Owner 各自维护不一致验证规则的风险，并防止这些 God File 继续吸收状态变更协议。

## 替换、删除或不适用说明

- 替换/删除项：替换 `goal_changes.items.additionalProperties=true`、`focus_change.additionalProperties=true`、Goal 变更无 evidence/revision 的应用路径，以及 Focus 任意字典合并路径。
- 若没有旧项可替换，说明为何属于真正新增能力：不适用；本抽象有明确旧责任替换对象。
- 兼容层截止时间与删除条件：V20.12 只读兼容旧 GoalRecord 缺失 revision（按 1）和旧 Focus 缺失 revision（按 0）。该兼容必须在后续 State Schema v2 迁移完成、活动 checkpoint 全部升级并连续 14 天 legacy revision fallback 计数为 0 后删除；不得扩展为永久双写协议。

## 不能并入现有 Owner 的原因

若继续并入 `goal_planning.py`，会把模型协议、语义候选验证和状态并发应用混在同一文件；若并入 `goal_lifecycle.py`，会让状态仓库重新解释模型开放对象；若并入 `tool_execution_runtime.py`，会让 Tool 执行器拥有 Goal/Focus 语义状态合同。独立窄 Owner 可保持依赖方向为：协议/规划调用验证器，生命周期与 Focus 应用器消费已验证操作，执行器不得自行补写语义。

## 验证与回滚

- 必须证据：签名红基线中三个 P0 adversarial bridge 均真实失败；候选实现中同一 Gate 全部通过；11 项直接合同反例、强上下文 107 场景、Chromium 兼容旅程和架构 Gate 通过。
- 复杂度指标：只允许三种 Goal 操作、三种 Focus 操作和四个 Goal Patch 字段；不得新增关键词路由、领域 effect 枚举或业务状态判断。
- 回滚方案：回滚本阶段 lifecycle 源码和测试到 V20.11.2，并保留红基线与失败证据；不得通过恢复开放 `additionalProperties` 或删除反例完成回滚。若正式模型无法稳定输出新合同，应返回协议修正或结构化澄清，不得静默接受旧开放对象。

## 机器校验字段

- 新增项：`agent_core.lifecycle.semantic_state_changes` 与 Goal/Focus 强类型协议。
- 唯一职责：验证并原子应用已有 Goal/Focus 的有限状态变化，不解释开放语言。
- 替换或删除项：开放 `goal_changes`、开放 `focus_change`、无 revision/evidence 的历史状态修改，以及 Focus 任意字典合并。
- 删除证据：旧代码在签名红基线的三个 adversarial bridge 中真实失败；候选代码必须使同一测试节点通过，且源码中不再存在开放对象 Schema 和任意 Focus merge。
- 验证：`adversarial-runtime-counterexamples`、`test_goal_change_evidence_contract.py`、强上下文 107 场景、Chromium 兼容旅程和架构 Gate。
