# 目标

- 目标 ID：migration-v20.17-b9-observability-boundary
- 变更标识：portable-migration-v20.17-b9-observability-boundary
- 执行上下文：local-change
- 目标类型：migration

把 Lifecycle State 校验与 Trace Repository 从 observability 的隐式依赖改为调用方显式注入，并把 SQLite Trace 实现迁入 persistence，使 observability 成为依赖中立的事件构造、脱敏、相关性与计时层，退出主 SCC，同时保持全部调试、审计和状态合同语义。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/observability/flow_debug.py`, `services/agent-service/src/agent_core/observability/trace_logger.py`, `services/agent-service/src/agent_core/observability/metrics.py`, `services/agent-service/src/agent_core/persistence/trace_store.py`, `services/agent-service/src/agent_core/persistence/store_provider.py`, `services/agent-service/src/agent_core/lifecycle/graph.py`, `services/agent-service/tests/architecture/test_observability_boundary_scc.py`, `docs/architecture/V20_17_B9_OBSERVABILITY_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B9_OBSERVABILITY_BOUNDARY.md

## 禁止范围

不得删除 Graph Node Trace、State Contract 校验、脱敏、Correlation ID、Trace 查询或指标能力；不得修改 State Schema、State Owner、Agent Loop、事务状态机、数据库表结构、Business Service、质量策略或依赖债务基线；不得用动态导入、全局 StoreProvider 查询或新的 Service Locator 隐藏 observability 对 lifecycle/persistence 的运行时依赖。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b9-observability-boundary.json`
- 验收 ID：`OBSERVABILITY-BOUNDARY-SCC-001`

observability 不再导入 lifecycle 或 persistence；debug wrapper 必须显式接收 StateUpdateValidator 与 TraceRepository；SQLite Trace 实现只有 persistence 一份；Lifecycle graph 显式传入现有 validator 和 trace repository；主 SCC 从 6 降到至多 5；observability、storage、context、modules、kernel、resources、ledger、rag、utils 均保持退出。

## 基线

旧基线由 observability.flow_debug 直接导入 lifecycle.state_contracts 并懒加载 persistence StoreProvider，observability.trace_logger 直接继承 persistence.SQLiteBase，主 SCC 为 6；新的边界反例失败，B1-B8 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复 observability 依赖注入、Trace 实现所有权和调用方装配；若 Trace 内容、State Contract 行为或数据库 Schema 变化，停止并重新规划。
