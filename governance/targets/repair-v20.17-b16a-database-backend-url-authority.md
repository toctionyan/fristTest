# 目标

- 目标 ID：repair-v20.17-b16a-database-backend-url-authority
- 变更标识：repair-v20.17-b16a-database-backend-url-authority
- 执行上下文：local-change
- 目标类型：repair

建立 Agent 数据库 backend/URL 单一权威。任何声明为 PostgreSQL、SQLite 或 MySQL 的后端都不得接受另一数据库家族的 URL；protected profile 不得在缺少 PostgreSQL URL 时静默回退 SQLite。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/persistence/database_settings.py`, `services/agent-service/src/agent_core/persistence/store_provider.py`, `services/agent-service/tests/architecture/test_b16a_database_backend_url_authority.py`, `services/agent-service/tests/architecture/test_persistence_profile_boundary_scc.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B16A_DATABASE_BACKEND_URL_AUTHORITY.md`, `governance/targets/repair-v20.17-b16a-database-backend-url-authority.md`, `governance/claims/repair-v20.17-b16a-database-backend-url-authority.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B16A_DATABASE_BACKEND_URL_AUTHORITY.md`

## 禁止范围

不得修改数据库 Schema、迁移脚本、事务协议、Checkpoint、RAG、Business Service 或质量 Judge；不得用 SQLite 测试替代真实 PostgreSQL 集成认证；不得把环境排除写成 PostgreSQL PASS。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b16a-database-backend-url-authority.json`
- 验收 ID：`V20-17-B16A-DATABASE-AUTHORITY-001`

旧实现必须在 protected profile 缺 URL和三类 backend/URL 错配反例上失败；修复后同一反例必须 fail closed，同时显式 generic SQLAlchemy + SQLite 的本地路径继续可用。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复 backend/URL 权威绑定，不进入真实 PostgreSQL 部署范围。

## 基线

红基线：B15c3 候选树中的 `DatabaseSettings` 独立解析 backend 与 URL，并在 URL 缺失时无条件合成 SQLite URL。
