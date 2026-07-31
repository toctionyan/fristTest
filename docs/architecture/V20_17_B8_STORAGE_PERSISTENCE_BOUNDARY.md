# V20.17 B8：Storage / Persistence 实现边界

## 状态

本记录定义 B8 迁移目标：storage 只拥有持久化端口和值合同，persistence 唯一拥有 SQLite/SQLAlchemy 实现、数据库配置读取和 StoreProvider 构造。

## 新增项

- `agent_core.persistence.database_settings`：数据库后端与连接配置读取；
- `agent_core.persistence.store_provider`：SQLite Provider、Provider Factory 与缓存；
- `agent_core.persistence.sqlalchemy_provider`：SQLAlchemy Provider、表定义和仓库实现。

## 唯一职责

`storage` 定义 StoreProvider、Repository、TransactionScope 等稳定端口。`persistence` 实现这些端口并负责具体数据库装配。应用 Composition、transaction、observability 与 Alembic 迁移通过 persistence 的明确入口使用实现。

## 替换或删除项

- 删除 `agent_core.storage.factory`；
- 删除 `agent_core.storage.settings`；
- 删除 `agent_core.storage.sqlalchemy_provider`；
- 所有生产、迁移和测试导入切换到 persistence 唯一入口；
- 不保留 importlib、`__getattr__` 或其他动态兼容代理。

## 删除证据

- `agent_core.storage` 不得导入 `agent_core.persistence`、`agent_core.observability` 或 `agent_core.runtime`；
- storage 下不得存在具体 Provider、数据库配置或 SQLAlchemy 表实现文件；
- 仓库内不得继续引用三个旧导入路径；
- 主 SCC 从 7 降为至多 6，removed_members 包含 storage/context/modules/kernel/resources/ledger/rag/utils；
- 不允许修改依赖债务基线制造缩减。

## 验证

- Storage 端口所有权、旧路径删除和 SCC 反例；
- SQLite/SQLAlchemy transaction storage 测试；
- Alembic 环境和初始 Schema 导入测试；
- B1-B7 累计依赖债务回归；
- Agent/Business 全量、前端 Vitest 与生产构建；
- 应用启动、完整 HTTP 生命周期和真实 Chromium。

## 明确不处理

- 不改变数据库 Schema、仓库协议或事务状态机；
- 不改变 runtime profile 的严格模式规则；
- 不修改 Agent Loop、State、Capability 或 Business Service；
- 剩余 6 包 SCC 和 State/Loop 瘦身由后续独立 Target 处理。
