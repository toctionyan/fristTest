# 目标

- 目标 ID：migration-v20.17-b6-module-presentation-boundary
- 变更标识：portable-migration-v20.17-b6-module-presentation-boundary
- 执行上下文：local-change
- 目标类型：migration

把模块贡献合同与展示 Registry 装配职责分开：modules 只声明中立 PresentationAdapter 协议并暴露已安装适配器集合，Composition Root 创建 PresentationRegistry，删除 modules 对 presentation 的反向依赖，使 modules 退出主 SCC，同时保留 B1-B5 的累计依赖债务成果。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/modules/contracts.py`, `services/agent-service/src/agent_core/modules/registry.py`, `services/agent-service/src/agent_core/presentation/adapters.py`, `services/agent-service/src/agent_core/presentation/registry.py`, `services/agent-service/src/agent_core/composition/registry.py`, `services/agent-service/tests/architecture/test_module_installation.py`, `services/agent-service/tests/architecture/test_module_presentation_boundary_scc.py`, `docs/architecture/V20_17_B6_MODULE_PRESENTATION_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B6_MODULE_PRESENTATION_BOUNDARY.md

## 禁止范围

不得移动或复制 PresentationRegistry、ModuleRegistry、RuntimeRegistry 或 Presentation Contract 权威；不得修改展示选择优先级、结构化 Release Gate、模块清单、能力/资源/操作合同、Agent Loop、State Schema、事务状态机、Business Service、质量策略或依赖债务基线；不得新增展示 Service Locator 或第二套 PresentationAdapter 协议。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b6-module-presentation-boundary.json`
- 验收 ID：`MODULE-PRESENTATION-BOUNDARY-SCC-001`

modules 不再导入 presentation；PresentationAdapter 的唯一中立协议由模块贡献合同声明，旧 presentation.adapters 仅保留兼容导出；ModuleRegistry 只暴露适配器集合而不创建 PresentationRegistry；Composition Root 创建同一个 PresentationRegistry；主 SCC 从 9 降到至多 8；modules、kernel、resources、ledger、rag、utils 均保持退出。

## 基线

旧基线由 modules.contracts 导入 presentation.adapters，modules.registry 导入并创建 PresentationRegistry，形成 modules → presentation 反向依赖，主 SCC 为 9；新的边界反例失败，B1-B5 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复模块贡献协议、适配器集合暴露、Presentation Registry 装配与批准测试；没有可度量依赖改善时停止并重新规划。
