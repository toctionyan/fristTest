# 目标

- 目标 ID：repair-v20.17-b16c-postgres-restart-concurrency-recovery
- 变更标识：repair-v20.17-b16c-postgres-restart-concurrency-recovery
- 执行上下文：local-change
- 目标类型：repair

让 Managed Integration 通过公开 HTTP 边界证明 owned PostgreSQL 上的 Draft、Transcript、Commit 和 Receipt 能跨两次服务重启恢复，并在两个 Agent 并发提交同一授权时保持幂等。

## 允许范围

- 允许变更路径：`scripts/verify_full_lifecycle_canary.py`, `scripts/run_managed_quality_integration.py`, `scripts/verify_managed_postgres_recovery.py`, `services/agent-service/tests/architecture/test_b16c_postgres_restart_recovery_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B16C_POSTGRES_RESTART_CONCURRENCY_RECOVERY.md`, `governance/targets/repair-v20.17-b16c-postgres-restart-concurrency-recovery.md`, `governance/claims/repair-v20.17-b16c-postgres-restart-concurrency-recovery.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B16C_POSTGRES_RESTART_CONCURRENCY_RECOVERY.md`

## 禁止范围

不得修改事务 Schema、业务状态机或 Quality Judge；不得把静态源码检查写成真实 PostgreSQL PASS；不得让普通 SQLite Quick 依赖 PostgreSQL。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b16c-postgres-restart-concurrency-recovery.json`
- 验收 ID：`V20-17-B16C-POSTGRES-RECOVERY-001`

旧实现必须在“无重启/无第二 Agent/无恢复旅程”的同一反例上失败；修复后反例转绿且普通生命周期不回归。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复 Managed PostgreSQL 恢复认证边界，不修改业务逻辑。

## 基线

红基线：B16b 公开服务只启动一次；Integration 没有跨重启、双 Agent 并发和 Receipt 稳定性旅程。
