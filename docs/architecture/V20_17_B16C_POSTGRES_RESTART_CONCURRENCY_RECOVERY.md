# V20.17 B16c PostgreSQL Restart and Concurrency Recovery

## 目标

让 Managed Integration 不只证明数据库组件可连接，还必须通过公开 HTTP 边界证明 PostgreSQL 上的会话、Draft、授权、Commit 与 Receipt 能跨服务重启恢复，并在两个 Agent 实例并发提交同一授权时保持单一稳定结果。

## 新增项

- `ProductRuntimeHarness.restart_product_services()`：只允许 PostgreSQL 模式，保留 owned 数据库与模型进程，重启公开 Agent/Business。
- `ProductRuntimeHarness.start_secondary_agent()`：使用不同端口启动第二个 Agent，但共享同一 PostgreSQL、Business 与模型配置。
- `verify_managed_postgres_recovery.py`：通过公开事务 API 创建 Draft，第一次重启后恢复待授权状态；两个 Agent 并发提交同一授权；重放后 Receipt 不变；第二次重启后恢复 COMMITTED 与 SUCCESS Receipt。
- Managed Integration 在 Quality Controller 前生成独立、机器可读的恢复证据。

## 唯一职责

Harness 只拥有进程生命周期；恢复脚本只拥有公开产品旅程及断言；Managed Integration 只编排 owned PostgreSQL、恢复旅程与原 Quality Controller。事务仓库、业务状态机和 Quality Judge 的权威边界不迁移。

## 替换或删除项

- 替换“一次启动直到测试结束”的公开服务 Harness，加入受控、按名称停止和重启能力。
- 删除“组件级 PostgreSQL 测试即可代表产品恢复”的隐含结论。
- 不删除 SQLite Quick Harness；SQLite 明确拒绝承担生产恢复认证。

## 删除证据

- 旧树没有 `restart_product_services`、第二 Agent 或恢复旅程，四条 B16c 反例失败。
- 修复后恢复旅程必须包含两次重启、两个 Agent、两个并发授权尝试和一次重复重放。
- Managed Integration 必须把恢复证据写入当前运行的证据目录，禁止复用历史 JSON。
- 没有 Docker/PostgreSQL 时只能返回 `BLOCKED_BY_ENVIRONMENT`，不得声明真实恢复 PASS。

## 验证

- 代码级红基线：B16b Harness 缺少重启、第二实例和恢复旅程。
- 修复后相关 Harness、生命周期、产品化和系统闭包回归通过。
- Quick 证明代码边界和非 PostgreSQL 回归；B16c3 必须在真实 PostgreSQL 环境执行 Managed Integration，才能认证恢复旅程。
