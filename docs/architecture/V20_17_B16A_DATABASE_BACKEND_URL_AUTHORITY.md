# V20.17 B16a 数据库 Backend/URL 权威

## 目标

把 Agent 持久化入口的数据库 backend 与连接 URL 绑定为一个权威对，禁止 protected profile 声明 PostgreSQL 却静默连接 SQLite。本阶段不修改数据库 Schema、事务协议、Checkpoint、RAG 或 Business Service。

## 新增项

- 新增 `validate_database_settings` 绑定校验。
- `DatabaseSettings` 构造时执行校验。
- `StoreProvider` 工厂再次执行校验，防止手工构造或反序列化配置绕过环境解析。
- protected profile 默认 PostgreSQL 时，要求显式提供 PostgreSQL URL。

## 唯一职责

`DatabaseSettings` 负责声明并验证 backend/URL 权威对：`sqlite` 只能绑定 SQLite URL，`postgres/postgresql` 只能绑定 PostgreSQL URL，`mysql` 只能绑定 MySQL URL。`sqlalchemy` 是显式通用适配入口，可在本地测试中绑定受支持的 SQLite、PostgreSQL 或 MySQL URL。Provider 只消费已经验证的设置，不再推断或修复错配数据库类型。

## 替换或删除项

- 替换“URL 缺失时无条件合成 `sqlite:///...`”的旧行为。
- 删除 protected profile 中 `backend=postgres` 与实际 SQLite URL 并存的路径。
- 替换旧 profile 成功测试，使其显式提供 PostgreSQL URL。
- 不删除 generic SQLAlchemy + SQLite 本地兼容路径。

## 删除证据

- preprod/postgres 缺 URL 的反例必须在创建 Provider 前失败。
- `postgres + sqlite URL`、`sqlite + postgres URL`、`mysql + sqlite URL` 均在设置边界失败。
- `StoreProvider` 对调用方传入的 settings 再验证相同合同。
- 源码不再包含 protected profile URL 缺失时生成 SQLite URL 的分支。

## 验证

- 旧 B15c3 实现在 4 条 backend/URL 反例上为 `4 failed, 1 passed`。
- 修复后同一数据库测试与 Profile/架构测试为 `6 passed`。
- Agent 持久化、安全与事务相关回归为 `24 passed`。
- Business 配置与完整性回归为 `27 passed`。
- 真实 PostgreSQL 连接、迁移、重启和并发认证属于 B16b/B16c；本阶段不得把被排除的集成测试写成 PostgreSQL PASS。
