# 目标

- 目标 ID：migration-v20.11.1-unified-semantics-grounded-planning
- 变更标识：portable-migration-v20.11.1-unified-semantics-grounded-planning
- 执行上下文：local-change
- 目标类型：migration

迁移统一语义、精确能力落地与可验证局部执行计划，并保留现有业务、事务和发布权威边界。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/context/**, services/agent-service/src/agent_core/lifecycle/**, services/agent-service/src/agent_core/runtime/**, services/agent-service/src/agent_core/kernel/capability.py, services/agent-service/src/agent_core/kernel/capability_registry.py, services/agent-service/src/agent_modules/*/capabilities/**, services/agent-service/tests/context/**, services/agent-service/tests/runtime/**, docs/architecture/**`
- 新增抽象记录：docs/architecture/UNIFIED_SEMANTIC_PLANNING_MIGRATION.md

## 禁止范围

不得修改 Skill、Quality Policy、Judge、Baseline 或受保护 Evidence；不得改变 Business Service、事务与发布权威；不得使用关键词、Tool 名或相似度生成正式能力身份。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.11.1-unified-semantics-grounded-planning.json`
- 验收 ID：`UNIFIED-SEMANTIC-GROUNDED-PLANNING-001`

旧代码上的统一语义与 Grounded Plan 反例必须真实失败；修复后相关 Quick Gate 通过，旧字段仅为兼容投影。

## 基线

在干净 V20.6.1 产品源码上仅加入统一语义与规划反例，记录 Quick 红基线。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
