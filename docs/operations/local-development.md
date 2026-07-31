# 本地开发与运行目录

## 配置

先从各服务 `.env.example` 创建本机 `.env`。变量默认值、模型设置和生产约束见 [CONFIGURATION.md](CONFIGURATION.md)。

## 运行时数据

两个服务默认将本机状态写入各自的 `runtime/`：

- Agent：`runtime/sqlite/`、`runtime/vector-store/`、`runtime/uploads/`、`runtime/logs/`
- Business Service：`runtime/business-service/`

这些目录由程序按需创建，不能提交、不能作为测试 fixture、不能进入发布包。需要保留演示数据时，使用独立 fixture 或 seed，而不是复制数据库文件。

## 数据库迁移

Agent 的 Alembic 配置位于 `services/agent-service/migrations/alembic.ini`，迁移脚本位于 `services/agent-service/migrations/agent_db/`。迁移属于源码；数据库文件属于运行时状态。

## 当前治理与验证

只使用 `governance/architecture-policy.json` 和统一验收命令：

```bash
PYTHON_BIN="$(services/agent-service/scripts/resolve_python.py)"
"$PYTHON_BIN" -B scripts/verify_architecture.py
```

不要复制历史规则，也不要为了一个局部问题新增平行 Guard、Facade 或兼容路径。
