# 目标

- 目标 ID：migration-v20.17-b2b-readiness-scc-ratchet
- 变更标识：portable-migration-v20.17-b2b-readiness-scc-ratchet
- 执行上下文：local-change
- 目标类型：migration

将 readiness 聚合器迁移到应用层并修正 B1 架构回归为单调债务棘轮，使 rag 退出主 SCC，同时保留 utils 的既有退出成果。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/runtime/readiness.py`, `services/agent-service/app/services/readiness_service.py`, `services/agent-service/app/api/health_api.py`, `services/agent-service/tests/architecture/test_readiness_boundary_scc_extraction.py`, `services/agent-service/tests/architecture/test_utils_scc_extraction.py`, `docs/architecture/V20_17_B2_READINESS_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B2_READINESS_BOUNDARY.md

## 禁止范围

不得修改 Agent Loop、State Schema、事务、能力选择、RAG 实现、Business Service、质量策略或依赖债务基线；不得放宽 utils/rag 必须退出 SCC 的断言。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b2b-readiness-scc-ratchet.json`
- 验收 ID：`READINESS-SCC-RATCHET-001`

Readiness 行为等价；主 SCC 从 13 降到 12；rag 与 utils 均不在当前循环；B1 回归使用单调不增长断言而不是锁死中间数值。

## 基线

旧基线保留 runtime/readiness.py，主 SCC 为 13，新的 readiness 反例失败；原 B1 测试在 13 时仍通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
