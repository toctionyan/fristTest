# V20.17 B16b Managed PostgreSQL Product Runtime

## 目标

保证受控 Integration 启动的 owned PostgreSQL 不只服务独立 pytest，而是同时作为公开 Agent、Checkpoint 和 Business HTTP 服务的真实持久化后端，消除“数据库测试跑 PostgreSQL、产品链仍跑 SQLite”的双环境假绿。

## 新增项

- `ProductRuntimeHarness` 新增显式 `persistence_url` 参数。
- 参数只接受 PostgreSQL URL，其他数据库家族 fail closed。
- Managed Integration 把 `postgres.url` 同时传给公开双服务和 Integration Gate。
- PostgreSQL 模式设置 Agent Store、LangGraph Checkpoint 和 Business Database 为同一 URL。

## 唯一职责

`ManagedPostgres` 拥有一次可丢弃 PostgreSQL 实例；`ProductRuntimeHarness` 只负责按显式 URL 启动公开服务；Quality Controller 继续负责 Gate 编排。普通 Quick 和浏览器单测未传 `persistence_url` 时继续使用临时 SQLite，不被 B16b 扩大成本。

## 替换或删除项

- 替换 `ManagedPostgres + ProductRuntimeHarness()` 的旧组合，改为 `ProductRuntimeHarness(persistence_url=postgres.url)`。
- 删除 Managed Integration 中公开 Agent/Business 固定 SQLite 的路径。
- 不删除 SQLite Quick Harness；它仍是非 Integration 的确定性生命周期入口。

## 删除证据

- Managed Integration 源码必须显式把 `postgres.url` 交给公开 Runtime Harness。
- PostgreSQL Harness 环境中的 `AGENT_DATABASE_URL`、`CHECKPOINT_DATABASE_URL`、`BUSINESS_DATABASE_URL` 必须完全一致。
- PostgreSQL Harness 不保留 `SQLITE_DB_PATH`、`CHECKPOINT_DB_PATH` 或 `BUSINESS_DB_PATH` 权威。
- SQLite URL 伪装 managed persistence 时在服务启动前失败。

## 验证

- B16a 旧实现的 3 条反例全部失败。
- 修复后 Harness、Managed Integration 源码边界和既有生命周期/系统闭包回归共 18 项通过。
- 正式 Quick 只验证代码边界与非集成回归；B16b3 必须在真实 PostgreSQL 环境运行 managed Integration，才能声明 PostgreSQL 认证 PASS。
