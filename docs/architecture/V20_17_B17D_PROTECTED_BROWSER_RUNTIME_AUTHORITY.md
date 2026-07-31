# V20.17 B17d — Protected Browser Runtime Authority

## 1. 问题不是“浏览器没跑”，而是浏览器跑在错误的权威里

B17c 已经能把模型、PostgreSQL 和浏览器三个组件绑定到一个发布控制器，但浏览器组件内部仍沿用本地开发 Harness：

- `APP_PROFILE=local`
- Agent、Checkpoint、Business 使用 SQLite
- `AGENT_AUTH_PROVIDER=dev_token`
- `WEB_CONSOLE_DEV_LOGIN=true`
- `BUSINESS_REQUIRE_ACTOR_SIGNATURE=false`
- `RAG_BACKEND=local_sparse`
- 状态合同和语义 Verifier 未按生产模式运行

与此同时，PostgreSQL 在另一个组件中独立通过。两个结果都绿，不等于真实浏览器请求曾经穿过“生产身份 + PostgreSQL + pgvector + 严格状态合同”的同一运行时。该组合属于跨权威拼接假绿。

## 2. 唯一浏览器运行时合同

B17d 新增 `protected-browser-runtime-authority@1`。每个受保护浏览器旅程必须证明下列事实，并由生产浏览器控制器逐字段验证：

- 运行 Profile 为 `preprod`；
- 认证为 `jwt_hs256`，关闭开发登录；
- Business Service 强制验证签名 Actor；
- Agent、Checkpoint、Business、RAG、Document Job 使用同一个 PostgreSQL URL；
- RAG 使用 `pgvector`，文档任务使用 SQLAlchemy，共享文件对象存储；
- 严格持久化、严格状态合同开启；
- Capability、Goal、Answer Release 三个 Verifier 均使用模型模式；
- 两个浏览器旅程携带相同的数据库实例指纹。

生产 Bundle 不能根据文件名、环境变量声明或独立组件 PASS 推断这些事实，只接受浏览器旅程在结束时输出的结构化证明。

## 3. 同一控制器拥有数据库生命周期

`verify_production_browser_bundle.py` 只创建一次 `ManagedPostgres`，随后顺序执行：

1. 配置模型强上下文旅程；
2. 配置模型强上下文 Campaign；
3. 对两次结果验证同一个数据库实例指纹和同一个运行时合同。

Agent、Checkpoint、Business、RAG 和 Document Job 的数据库 URL 均从该受控实例注入。任何旅程试图换库、退回 SQLite 或省略数据库指纹，都会在 Bundle 关单前失败。

## 4. 生产身份不再依赖开发登录

受保护 Harness 为一次性运行生成高熵 JWT Secret，并签发只覆盖认证窗口的客户 JWT。前端 E2E 在页面代码执行前把令牌注入 `agent.product.token`，不再通过开发登录按钮取得 `dev_token`。

令牌、JWT Secret、业务服务令牌和 Actor 签名 Secret 都只存在于临时进程环境中，不进入运行时证据、日志摘要或交付包。

## 5. 数据准备必须显式发生

生产 Profile 禁止服务启动时隐式创建 Schema 或播种演示数据。Harness 在启动公开服务前依次执行：

1. Agent Alembic migration；
2. Business 临时认证数据命令；
3. RAG 临时 pgvector 数据命令。

这些命令都要求显式的一次性开关，不能在普通生产启动中自动触发。RAG 数据使用真实受保护 Embedding 配置；模型和 Embedding 身份分别预检，不能把聊天模型密钥默认当作 Embedding 证明。

## 6. 环境阻断与代码失败分离

下列情况只能返回退出码 78 和 `BLOCKED_BY_ENVIRONMENT`：

- 锁定 Agent/Business Python 环境缺失；
- Docker 或受控 pgvector 无法启动；
- Chromium/Playwright 运行时缺失或被设备策略阻断；
- 官方模型或 Embedding 凭证缺失；
- 外部模型/Embedding 返回认证、配额、限流、超时或连接故障。

Schema、迁移、运行时合同、身份绑定、同库证明或 E2E 断言错误仍返回代码 `FAIL`。环境分类不会输出原始凭证或完整外部响应，只记录阶段、稳定原因和摘要指纹。

## 7. 边界

B17d 不修改客服语义、Prompt、Capability、事务协议、业务规则或数据库领域实现。它只让已有生产认证真正穿过同一个受保护产品运行时。

当前交付仍是阶段候选。只有在受保护 GitHub Environment 中注入官方模型/Embedding 凭证、证据签名密钥，并具备锁定 Python、Node、Playwright 和 Docker/pgvector 后，才能运行真实 Bundle 并生成 `production_closed`。
