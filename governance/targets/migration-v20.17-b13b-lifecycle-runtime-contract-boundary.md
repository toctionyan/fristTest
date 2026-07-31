# 目标

- 目标 ID：migration-v20.17-b13b-lifecycle-runtime-contract-boundary
- 变更标识：portable-migration-v20.17-b13b-lifecycle-runtime-contract-boundary
- 执行上下文：local-change
- 目标类型：migration

让 Runtime 不再反向导入 Lifecycle。将 Loop 默认值、冻结语义合同的中立完整性/只读投影以及 State Schema 版本兼容判断迁入 Kernel 合同层，Lifecycle 保留创建、冻结、迁移和写入权威并兼容导出原公共名称；最终清除 lifecycle/runtime 两包 SCC。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/loop_contract.py`, `services/agent-service/src/agent_core/kernel/semantic_contract.py`, `services/agent-service/src/agent_core/kernel/state_schema_contract.py`, `services/agent-service/src/agent_core/lifecycle/protocol.py`, `services/agent-service/src/agent_core/lifecycle/semantic_contract.py`, `services/agent-service/src/agent_core/lifecycle/state_schema.py`, `services/agent-service/src/agent_core/runtime/node_support.py`, `services/agent-service/src/agent_core/runtime/capability_gate.py`, `services/agent-service/src/agent_core/runtime/answer_release_alignment.py`, `services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py`, `services/agent-service/src/agent_core/runtime/capability_effects.py`, `services/agent-service/tests/support/dependency_debt.py`, `services/agent-service/tests/architecture/test_lifecycle_runtime_dependency_boundary_scc.py`, `services/agent-service/tests/architecture/test_context_projection_boundary_scc.py`, `services/agent-service/tests/architecture/test_kernel_registry_injection_scc.py`, `services/agent-service/tests/architecture/test_module_presentation_boundary_scc.py`, `services/agent-service/tests/architecture/test_observability_boundary_scc.py`, `services/agent-service/tests/architecture/test_persistence_profile_boundary_scc.py`, `services/agent-service/tests/architecture/test_presentation_dependency_boundary_scc.py`, `services/agent-service/tests/architecture/test_resource_registry_injection_scc.py`, `services/agent-service/tests/architecture/test_storage_persistence_boundary_scc.py`, `services/agent-service/tests/architecture/test_transaction_runtime_boundary_scc.py`, `services/agent-service/tests/architecture/test_ledger_scc_extraction.py`, `services/agent-service/tests/architecture/test_readiness_boundary_scc_extraction.py`, `services/agent-service/tests/architecture/test_utils_scc_extraction.py`, `docs/architecture/V20_17_B13_LIFECYCLE_RUNTIME_CONTRACT_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B13_LIFECYCLE_RUNTIME_CONTRACT_BOUNDARY.md

## 禁止范围

不得移动 FrozenSemanticContract 的创建/冻结权威、State Schema 迁移权威、Graph 路由、Agent Loop、Capability Match、事务状态机、业务命令、RuntimeOutcome 或 Business Service；不得复制第二套摘要算法或兼容判断；不得修改质量策略或依赖债务基线。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b13b-lifecycle-runtime-contract-boundary.json`
- 验收 ID：`LIFECYCLE-RUNTIME-CONTRACT-BOUNDARY-B13B-001`

Runtime 包不得导入 Lifecycle；Lifecycle 原公共合同名称必须与 Kernel 中立合同保持对象/结果等价；语义摘要篡改继续失败关闭；旧 Schema 兼容判断保持不变；正式依赖图必须无任何 agent_core 跨包 SCC，债务状态为 RESOLVED，B1-B12 已移出包不得重新进入循环。

## 基线

真实红基线中 Runtime 仍从 Lifecycle 导入 Loop 默认值、semantic_goals 与 legacy_fallback_allowed，主 SCC 为 lifecycle/runtime 两包；新反例必然失败，B12 Transaction/Runtime 边界累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复中立合同所有权、兼容导出和累计债务测试；如语义冻结、迁移、Graph、Agent Loop 或业务行为变化，停止并重新规划。
