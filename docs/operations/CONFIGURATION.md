# 配置与环境变量

V20.1 起，工程包必须同时交付**不含密钥的配置模板**。真实 `.env` 只存在于部署机器，永远不进入源码、测试夹具或发布压缩包。

## 第一次本地启动

在工作区根目录执行：

```bash
cp services/agent-service/.env.example services/agent-service/.env
cp services/business-service/.env.example services/business-service/.env
cp services/agent-service/frontend/.env.example services/agent-service/frontend/.env
```

然后至少填写 Agent 的：

```env
APP_PROFILE=local
AGENT_SERVICE_RELOAD=true
OPENAI_API_KEY=你的模型服务密钥
OPENAI_MODEL=你的模型名
OPENAI_API_BASE=你的 OpenAI 兼容接口地址  # 官方 OpenAI 时可留空
```

### Python 锁文件与可复现安装

两个 Python 服务必须随源码和 clean release 同时交付各自的 `uv.lock`：

- `services/agent-service/uv.lock`
- `services/business-service/uv.lock`

`pyproject.toml` 表达允许的依赖范围，`uv.lock` 固定实际解析结果；CI、发布和本地标准启动统一使用 `uv sync --locked --all-groups`。禁止在锁文件缺失或过期时退化为非锁定安装。更新 Python 依赖时，必须在对应服务目录重新生成锁文件，并执行 `uv lock --check` 和标准测试后一起提交。

先在两个服务目录各运行一次 `uv sync --locked --all-groups`，然后在两个终端分别启动 Business Service 与 Agent Service：

```bash
(cd services/business-service && uv sync --locked --all-groups && .venv/bin/python scripts/run_business_api.py)
(cd services/agent-service && uv sync --locked --all-groups && .venv/bin/python scripts/run_api.py)
```

`scripts/run_api.py` 在 `APP_PROFILE=local` 时会幂等写入当前已安装模块的内置知识，确保空工作区首次启动后政策咨询可用；`preprod` / `production` 不会自动写入知识库，必须由部署迁移/数据流程显式完成。

前端开发服务使用 `services/agent-service/frontend/.env` 中的 `VITE_AGENT_DEV_TARGET`；Vite 配置会显式读取该文件，不能再只依赖终端临时导出变量。

## 模型配置

| 变量 | 用途 | 本地默认 |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI 兼容 API 密钥 | 必填，无默认值 |
| `OPENAI_MODEL` | 主对话、RAG、语义核验共享模型名 | `gpt-4o-mini` |
| `OPENAI_API_BASE` | OpenAI 兼容 Base URL | 空，使用 SDK 默认值 |
| `MODEL_TEMPERATURE` | 模型温度，范围 `0`–`2` | `0` |
| `MODEL_TIMEOUT_SECONDS` | 单次模型请求超时，范围 `1`–`600` | `60` |
| `MODEL_MAX_RETRIES` | SDK 级模型请求重试，范围 `0`–`10` | `2` |
| `MODEL_CALL_MAX_PER_TURN` | 一次用户回合内所有模型调用的硬总预算，必须等于三个职责池之和 | `18` |
| `MODEL_CALL_MAX_PLANNER_PER_TURN` | Planner/Agent Loop 调用预算 | `8` |
| `MODEL_CALL_MAX_VERIFIER_PER_TURN` | 目标、能力和回答安全校验的保留预算 | `8` |
| `MODEL_CALL_MAX_SUPPORT_PER_TURN` | RAG 改写/回答等辅助调用预算 | `2` |

模型温度、超时和重试不再硬编码。模型调用预算按 Planner、Verifier、Support 隔离，但仍共享同一条审计 Trace 和总硬上限；Planner 耗尽时不能挤占最终安全校验额度。它们会被记录到非密钥的 `model_profile`/`model_call_budget` 中，用于审计与问题复现。

## 本地、预发布和生产

Agent 使用 `APP_PROFILE`，仅允许：`local`、`preprod`、`production`。

- `local`：可以使用 SQLite、`local_sparse`、本地开发登录。
- `preprod` / `production`：必须启用认证、Postgres Agent/checkpoint/Business
  持久化、严格状态合同、共享文档队列/对象存储、显式 CORS、强 Business
  Token 与 actor 签名；运行时会拒绝任何 SQLite/local 降级。

Business Service 与 Agent 使用同一个 `APP_PROFILE`，旧 `APP_ENV` 不再参与信任模式判断。`preprod` / `production` 必须配置强 `BUSINESS_SERVICE_TOKEN`、`BUSINESS_REQUIRE_ACTOR_SIGNATURE=true` 和 `BUSINESS_ACTOR_SIGNING_SECRET`，并禁止三个语义/目标/回答 verifier 使用 `disabled` 或 `candidate`。两个服务的 `BUSINESS_SERVICE_TOKEN` 与 actor signing secret 必须一致。

## 数据库与 RAG

本地模板默认使用：

```text
Agent persistence: runtime/sqlite/app.db
Checkpoint:        runtime/sqlite/checkpoints.db
Business data:     runtime/business-service/business.db
RAG:               local_sparse
```

这些运行态文件由程序创建，不能打包。切换到 protected profile 时，同时填写
`AGENT_DATABASE_URL`、`CHECKPOINT_DATABASE_URL`、`RAG_DATABASE_URL` 与
`BUSINESS_DATABASE_URL`，将 Agent、checkpoint、Business backend 改为 `postgres`，
RAG 改为 `pgvector`，并以 `deployment/.env.example` 为基础启动本地 pgvector：

```bash
cp deployment/.env.example deployment/.env
cd deployment && docker compose --env-file .env -f docker-compose.pgvector.yml up -d
```

## 配置完整性门禁

`architecture-skill/scripts/verify_convergence.py` 会检查：

1. Agent、Business、Frontend、Deployment 四份模板都存在；
2. 运行时代码读取的环境变量都有模板条目；
3. 模型温度、超时、重试不会重新硬编码到模型创建逻辑；
4. `.env`、运行数据库、日志和前端构建产物不进入发布包。

模板是每个变量的唯一默认值清单；本文件解释配置语义，不复制整份变量表。
