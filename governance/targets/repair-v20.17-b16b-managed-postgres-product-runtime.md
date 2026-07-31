# 目标

- 目标 ID：repair-v20.17-b16b-managed-postgres-product-runtime
- 变更标识：repair-v20.17-b16b-managed-postgres-product-runtime
- 执行上下文：local-change
- 目标类型：repair

让 Managed Integration 的 owned PostgreSQL 同时支撑公开 Agent、Checkpoint、Business 服务和独立 Integration 测试，禁止公开产品链在 Integration 中静默继续使用 SQLite。

## 允许范围

- 允许变更路径：`scripts/verify_full_lifecycle_canary.py`, `scripts/run_managed_quality_integration.py`, `services/agent-service/tests/architecture/test_b16b_managed_postgres_product_runtime.py`, `services/agent-service/tests/architecture/test_systemic_operational_closure.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B16B_MANAGED_POSTGRES_PRODUCT_RUNTIME.md`, `governance/targets/repair-v20.17-b16b-managed-postgres-product-runtime.md`, `governance/claims/repair-v20.17-b16b-managed-postgres-product-runtime.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B16B_MANAGED_POSTGRES_PRODUCT_RUNTIME.md`

## 禁止范围

不得修改数据库 Schema、迁移、事务协议或 Quality Judge；不得把静态环境断言写成真实 PostgreSQL PASS；不得让普通 Quick 强制依赖 PostgreSQL。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b16b-managed-postgres-product-runtime.json`
- 验收 ID：`V20-17-B16B-MANAGED-POSTGRES-RUNTIME-001`

旧实现必须在“公开服务未绑定 owned PostgreSQL”的反例上失败；修复后同一反例转绿，并保持普通 SQLite 生命周期不回归。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复 Managed Integration 持久化绑定，不修改业务逻辑。

## 基线

红基线：B16a 候选树启动 Managed PostgreSQL，但 `ProductRuntimeHarness()` 仍固定设置 Agent、Checkpoint 和 Business 为 SQLite。
