# 目标

- 目标 ID：migration-v20.17-b5b-kernel-registry-injection
- 变更标识：portable-migration-v20.17-b5b-kernel-registry-injection
- 执行上下文：local-change
- 目标类型：migration

把 Kernel Runtime 架构完整性检查及其公共入口改为显式接收已装配 RuntimeRegistry，删除 kernel 对 modules 的反向依赖，使 kernel 退出主 SCC，同时保留 B1-B4 的累计依赖债务成果。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/integrity.py`, `services/agent-service/src/agent_core/kernel/__init__.py`, `services/agent-service/app/main.py`, `services/agent-service/tests/architecture/test_resource_registry_injection_scc.py`, `services/agent-service/tests/architecture/test_kernel_registry_injection_scc.py`, `docs/architecture/V20_17_B5_KERNEL_REGISTRY_INJECTION.md`
- 新增抽象记录：docs/architecture/V20_17_B5_KERNEL_REGISTRY_INJECTION.md

## 禁止范围

不得移动或复制 RuntimeRegistry 权威，不得修改 Registry 完整性规则、模块安装、能力/资源/操作合同、Agent Loop、State Schema、事务状态机、Business Service、质量策略或依赖债务基线；不得新增另一个全局 Registry Provider 或 Service Locator。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b5b-kernel-registry-injection.json`
- 验收 ID：`KERNEL-REGISTRY-INJECTION-SCC-001`

实现函数和 `agent_core.kernel` 公共入口都必须显式接收 RuntimeRegistry；kernel 不再导入 modules；应用 Composition Root 传入同一个已装配 RuntimeRegistry；主 SCC 从 10 降到 9；kernel、resources、ledger、rag、utils 均保持退出。

## 基线

旧基线由 kernel.integrity 内部调用 current_runtime_registry，且 `agent_core.kernel` 公共包装器固定为零参数，形成 kernel → modules 反向依赖，主 SCC 为 10；新的显式注入和公共 API 反例失败，B1-B4 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复 RuntimeRegistry 显式注入、公共入口和批准调用方；没有可度量依赖改善时停止并重新规划。
