# 目标

- 目标 ID：migration-v20.17-b7-context-projection-boundary
- 变更标识：portable-migration-v20.17-b7-context-projection-boundary
- 执行上下文：local-change
- 目标类型：migration

把 ContextBundle 使用的只读 Goal/Blocker/Clarification 投影与最小事务读取端口收回 context，删除 context 对 lifecycle 与 storage 的反向依赖，使 context 退出主 SCC，同时保持 lifecycle 对这些状态的唯一写入权威和 B1-B6 累计依赖债务成果。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/context/context_bundle.py`, `services/agent-service/src/agent_core/context/state_projection.py`, `services/agent-service/src/agent_core/lifecycle/goal_blockers.py`, `services/agent-service/src/agent_core/lifecycle/goal_lifecycle.py`, `services/agent-service/src/agent_core/lifecycle/clarification_runtime.py`, `services/agent-service/tests/architecture/test_context_projection_boundary_scc.py`, `docs/architecture/V20_17_B7_CONTEXT_PROJECTION_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B7_CONTEXT_PROJECTION_BOUNDARY.md

## 禁止范围

不得修改 Goal/Blocker/Clarification 的写入、迁移、状态转换或 Schema；不得移动 TransactionLifecycleRepository 或 TransactionScope 权威；不得修改 ContextBundle 字段、摘要、排序、权限、目标解析、Agent Loop、事务状态机、Business Service、质量策略或依赖债务基线；不得创建第二套持久状态或复制写入逻辑。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b7-context-projection-boundary.json`
- 验收 ID：`CONTEXT-PROJECTION-BOUNDARY-SCC-001`

context 不再导入 lifecycle 或 storage；只读投影有一个 context 实现，lifecycle 旧公共入口仅兼容导出同一实现；ContextBundle 使用结构化最小事务读取协议与等价 scope 值对象；主 SCC 从 8 降到至多 7；context、modules、kernel、resources、ledger、rag、utils 均保持退出。

## 基线

旧基线由 context.context_bundle 直接导入 lifecycle 的三组投影函数和 storage 的 Repository/Scope 类型，形成 context → lifecycle/storage 反向依赖，主 SCC 为 8；新的边界反例失败，B1-B6 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复只读投影所有权、兼容导出和 ContextBundle 最小读取协议；若出现状态写入语义变化或没有可度量依赖改善，停止并重新规划。
