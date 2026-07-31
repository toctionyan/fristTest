# V20.17 B2 Readiness 应用边界

## 状态

本记录定义 B2 迁移的目标边界。实现完成前，现有 `agent_core.runtime.readiness` 仍是待迁移旧 Owner；实现完成后，本记录成为当前唯一职责说明。

## 新增项

- `app.services.readiness_service`：应用级部署就绪聚合器。

## 唯一职责

Readiness 聚合器负责读取运行配置，并以只读方式汇总：

- Agent Store 与 Checkpointer；
- 前端构建产物；
- RAG 可用性；
- 数据库迁移状态；
- Business Service 健康状态。

它属于应用装配和部署边界，不属于 Agent Core Runtime。Core Runtime 不应为了健康检查反向依赖 RAG、存储 Provider、业务适配器或前端文件系统。

## 替换或删除项

- 删除 `agent_core.runtime.readiness`；
- `app.api.health_api` 改为依赖 `app.services.readiness_service`；
- 不保留 Core Runtime 兼容 Shim，避免形成新的 `agent_core -> app` 反向依赖。

## 删除证据

- `services/agent-service/src/agent_core/runtime/readiness.py` 不再存在；
- `agent_core.runtime` 不再导入 `agent_core.rag`；
- Architecture Gate 的主 SCC 从 13 个成员降为 12 个成员，`removed_members` 包含 `rag`。

## 验证

- Readiness 边界架构反例测试；
- Architecture Convergence Gate；
- Agent 与 Business Python 全量测试；
- 前端 Vitest 与生产构建；
- 双服务 HTTP 生命周期；
- 真实 Chromium 产品旅程。

## 明确不处理

- 不重构 RAG Provider、索引任务或对象存储；
- 不重构 Runtime Profile 或 Migration Verifier；
- 不修改 Agent Loop、State Schema、事务协议、能力选择或展示合同；
- 剩余 12 包 SCC 继续作为后续独立债务处理。
