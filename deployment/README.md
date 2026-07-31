# 部署辅助

`docker-compose.pgvector.yml` 用于本地或预发布环境启动 pgvector 依赖。首次使用：

```bash
cp .env.example .env
# 修改 POSTGRES_PASSWORD 后：
docker compose --env-file .env -f docker-compose.pgvector.yml up -d
```

`.env.example` 只包含本地占位值；真实 `.env` 可能含数据库密码，不进入版本控制或发布包。服务运行态数据仍由各服务 `runtime/` 管理，不写入此目录。
