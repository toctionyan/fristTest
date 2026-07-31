# V20.17 B9：Observability 依赖中立边界

## 状态

本记录定义 B9 迁移目标：observability 只拥有依赖中立的 Trace/Event 合同、事件构造、脱敏、Correlation、指标读取协议和计时器；Lifecycle 与 Persistence 的权威通过显式依赖注入接入。

## 新增项

- 显式 `StateUpdateValidator` 调用合同；
- 显式 `TraceRepository`/Trace Sink 使用方式；
- `agent_core.persistence.trace_store` 作为 SQLite Trace 唯一实现。

## 唯一职责

Lifecycle 继续拥有 State Contract 与校验逻辑；Persistence 继续拥有数据库实现；Observability 只构造和提交观测事件，不查询全局 StoreProvider，也不导入 Lifecycle 状态实现。

## 替换或删除项

- 删除 `flow_debug` 内部对 `lifecycle.state_contracts` 的直接导入；
- 删除 `flow_debug` 内部对 `get_store_provider()` 的懒加载；
- 将 SQLite `TraceLogger` 从 observability 迁入 persistence；
- Lifecycle graph 显式传入 validator 与已装配 trace repository。

## 删除证据

- observability 不得导入 lifecycle 或 persistence；
- observability 不得继承 SQLiteBase 或构造 StoreProvider；
- persistence 只有一份具体 Trace 实现；
- 主 SCC 从 6 降为至多 5，removed_members 包含 observability/storage/context/modules/kernel/resources/ledger/rag/utils；
- 不允许修改依赖债务基线制造缩减。

## 验证

- Observability 边界、显式依赖和 SCC 反例；
- State Contract strict/audit 回归；
- Trace 持久化、查询、指标与脱敏回归；
- B1-B8 累计依赖债务回归；
- Agent/Business 全量、前端、完整生命周期和真实 Chromium。

## 明确不处理

- 不改变 State Schema、State Owner 或 Agent Loop；
- 不改变 Trace 表结构、事件字段或审计语义；
- 剩余 5 包 SCC、State/Loop 瘦身与真实模型认证由后续独立 Target 处理。
