# 目标

- 目标 ID：migration-v20.17-b11b-presentation-dependency-boundary
- 变更标识：portable-migration-v20.17-b11b-presentation-dependency-boundary
- 执行上下文：local-change
- 目标类型：migration

让 presentation 成为只消费中立 Outcome 投影协议和显式目录数据的纯展示层，删除 presentation 对 lifecycle、runtime、transaction 的反向依赖，使 presentation 退出主 SCC；RuntimeOutcome 的生成/校验权威、事务授权和展示结果保持不变。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/outcome_contract.py`, `services/agent-service/src/agent_core/runtime/outcomes.py`, `services/agent-service/src/agent_core/presentation/outcome.py`, `services/agent-service/src/agent_core/presentation/actions.py`, `services/agent-service/app/main.py`, `services/agent-service/tests/context/test_dialogue_counterexamples.py`, `services/agent-service/tests/architecture/test_presentation_dependency_boundary_scc.py`, `docs/architecture/V20_17_B11_PRESENTATION_DEPENDENCY_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B11_PRESENTATION_DEPENDENCY_BOUNDARY.md

## 禁止范围

不得改变 RuntimeOutcome 闭合类型、fail-closed 文案、事务授权、Draft/Grant/Attempt/Receipt、展示合同、卡片优先级或业务模块；不得让 presentation 自行生成业务结果；不得修改 Agent Loop、State、质量策略或依赖债务基线。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b11b-presentation-dependency-boundary.json`
- 验收 ID：`PRESENTATION-DEPENDENCY-BOUNDARY-SCC-001`

Presentation 不再导入 lifecycle/runtime/transaction；Kernel 只拥有闭合 Outcome 词汇和只读映射协议，Runtime 继续拥有 RuntimeOutcome 构造/校验；目录完整性由 Composition Root 显式传值；主 SCC 从 4 降到至多 3，presentation 与 B1-B10 已移出包保持退出。

## 基线

旧基线代码由 presentation.outcome 导入 runtime.outcomes，presentation.actions 在内部导入 lifecycle/transaction，形成展示层反向依赖，主 SCC 为 4；新反例失败，B10 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复 Outcome 投影协议、目录校验依赖和显式装配；若展示语义、授权或 RuntimeOutcome 权威变化，停止并重新规划。
