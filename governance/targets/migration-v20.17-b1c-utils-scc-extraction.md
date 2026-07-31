# 目标

- 目标 ID：migration-v20.17-b1c-utils-scc-extraction
- 变更标识：portable-migration-v20.17-b1c-utils-scc-extraction
- 执行上下文：local-change
- 目标类型：migration

将错误放置在 `agent_core.utils` 的 Lifecycle 图调试包装器迁移到 `agent_core.observability`，消除 `utils -> lifecycle/storage` 反向依赖，使 `utils` 从主包依赖 SCC 中退出。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/utils/flow_debug.py`, `services/agent-service/src/agent_core/observability/flow_debug.py`, `services/agent-service/src/agent_core/lifecycle/graph.py`, `services/agent-service/tests/architecture/test_utils_scc_extraction.py`, `docs/architecture/V20_17_B1_UTILS_SCC_EXTRACTION.md`
- 新增抽象记录：`docs/architecture/V20_17_B1_UTILS_SCC_EXTRACTION.md`

## 禁止范围

不得修改 Agent 业务行为、State Schema、事务协议、能力选择、工具调用、Business Service、质量策略或依赖债务基线；不得通过删除调试能力让 SCC 下降。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b1c-utils-scc-extraction.json`
- 验收 ID：`UTILS-SCC-EXTRACTION-001`

`flow_debug` 保持同等功能并归属 observability；`utils` 不再导入 lifecycle/storage；Architecture Gate 的主 SCC 成员数从 14 降到 13，`removed_members` 包含 `utils`。

## 基线

加入同一架构反例测试后，旧基线仍存在 `utils/flow_debug.py`、缺少 `observability/flow_debug.py`，且 Architecture Gate 的主 SCC 仍包含 14 个成员，因此 Quick 必须为红。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：仅根据结构化 Repair Plan 修正 flow-debug 包归属；不得扩大到其他 SCC 边界。
