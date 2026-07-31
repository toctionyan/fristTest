# 目标

- 目标 ID：migration-v20.17-b10b-persistence-profile-boundary
- 变更标识：portable-migration-v20.17-b10b-persistence-profile-boundary
- 执行上下文：local-change
- 目标类型：migration

把 APP_PROFILE 的枚举、解析和保护模式判断从 runtime 执行包迁到 domain-neutral Kernel 配置合同，persistence 直接消费该稳定合同，删除 persistence 对 runtime 的反向依赖，使 persistence 退出主 SCC，同时保留 runtime.profile 公共兼容入口和全部保护模式行为。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/profile.py`, `services/agent-service/src/agent_core/runtime/profile.py`, `services/agent-service/src/agent_core/persistence/database_settings.py`, `services/agent-service/src/agent_core/persistence/thread_store.py`, `services/agent-service/tests/architecture/test_persistence_profile_boundary_scc.py`, `docs/architecture/V20_17_B10_PERSISTENCE_PROFILE_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B10_PERSISTENCE_PROFILE_BOUNDARY.md

## 禁止范围

不得改变 APP_PROFILE 允许值、strict/fail-closed 规则、数据库默认后端、未绑定租户处理、Verifier Mode、安全配置或线程所有权；不得复制第二套 Profile 实现；不得修改 Agent Loop、State、事务状态机、Business Service、质量策略或依赖债务基线。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b10b-persistence-profile-boundary.json`
- 验收 ID：`PERSISTENCE-PROFILE-BOUNDARY-SCC-001`

Kernel 拥有唯一 RuntimeProfile 实现；runtime.profile 仅兼容导出同一对象；persistence 不再导入 runtime；主 SCC 从 5 降到至多 4；persistence、observability、storage、context、modules、kernel、resources、ledger、rag、utils 均保持退出。

## 基线

旧基线由 persistence.database_settings/thread_store 直接导入 runtime.profile，形成 transaction→persistence→runtime→transaction 环，主 SCC 为 5；新反例失败，B1-B9 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复 Profile 合同所有权、兼容导出和 persistence 导入；若 Profile 行为或数据库/租户保护规则变化，停止并重新规划。
