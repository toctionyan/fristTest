# 目标

- 目标 ID：migration-v20.17-b2-readiness-scc-extraction
- 变更标识：portable-migration-v20.17-b2-readiness-scc-extraction
- 执行上下文：local-change
- 目标类型：migration

将跨配置、存储、前端、RAG、迁移和 Business Service 的 readiness 聚合器从 agent_core.runtime 迁移到 app.services，消除 runtime -> rag 反向依赖，使 rag 退出主依赖 SCC。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/runtime/readiness.py`, `services/agent-service/app/services/readiness_service.py`, `services/agent-service/app/api/health_api.py`, `services/agent-service/tests/architecture/test_readiness_boundary_scc_extraction.py`, `docs/architecture/V20_17_B2_READINESS_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B2_READINESS_BOUNDARY.md

## 禁止范围

不得修改 Agent Loop、State Schema、事务、能力选择、RAG 实现、Business Service、质量策略或依赖债务基线；不得通过删除 readiness 检查项制造 SCC 下降。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b2-readiness-scc-extraction.json`
- 验收 ID：`READINESS-SCC-EXTRACTION-001`

readiness_report 行为保持等价并由 app.services 唯一拥有；agent_core.runtime 不再导入 RAG；Architecture Gate 主 SCC 从 13 降到 12，removed_members 包含 rag。

## 基线

加入同一边界反例后，旧基线仍存在 agent_core/runtime/readiness.py、缺少 app/services/readiness_service.py，且主 SCC 仍为 13 个成员，因此 Quick 必须为红。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
