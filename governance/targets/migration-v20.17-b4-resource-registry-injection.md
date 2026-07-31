# 目标

- 目标 ID：migration-v20.17-b4-resource-registry-injection
- 变更标识：portable-migration-v20.17-b4-resource-registry-injection
- 执行上下文：local-change
- 目标类型：migration

把 ResourcePluginRegistry 从隐藏的全局 RuntimeRegistry 查询改为 TargetResolver 的显式依赖，删除 resources 对 modules 的反向依赖，使 resources 退出主 SCC，同时保留 B1/B2/B3 的累计依赖债务成果。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/resources/targets.py`, `services/agent-service/app/use_cases/transaction_start.py`, `services/agent-service/src/agent_modules/ecommerce/shared/prepare_actions.py`, `services/agent-service/src/agent_modules/ecommerce/shared/refund_eligibility.py`, `services/agent-service/tests/architecture/test_runtime_contract.py`, `services/agent-service/tests/architecture/test_ledger_scc_extraction.py`, `services/agent-service/tests/architecture/test_resource_registry_injection_scc.py`, `docs/architecture/V20_17_B4_RESOURCE_REGISTRY_INJECTION.md`
- 新增抽象记录：docs/architecture/V20_17_B4_RESOURCE_REGISTRY_INJECTION.md

## 禁止范围

不得修改 ResourcePlugin/Registry 的业务语义、目标集合解析规则、Capability Match、OperationPreparation、Agent Loop、State Schema、事务状态机、Business Service、质量策略或依赖债务基线；不得新增另一个全局 Registry Provider 或在 resources 内换一种 service locator。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b4-resource-registry-injection.json`
- 验收 ID：`RESOURCE-REGISTRY-INJECTION-SCC-001`

TargetResolver 必须显式接收 ResourcePluginRegistry；resources 不再导入 modules；所有生产调用方由其已有 Composition/Module 上下文传入同一个 Registry；主 SCC 从 11 降到 10；resources、ledger、rag、utils 均保持退出。

## 基线

旧基线由 TargetResolver 在 resources 内部懒加载 current_runtime_registry，形成 resources → modules 反向依赖，主 SCC 为 11，新的显式注入反例失败；B1/B2/B3 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复 ResourcePluginRegistry 的显式注入和批准调用方；没有可度量依赖改善时停止并重新规划。
