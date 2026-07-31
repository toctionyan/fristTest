# V20.17 B6：Module / Presentation 装配边界

## 状态

本记录定义 B6 迁移目标：模块贡献层只声明和聚合中立展示适配器，展示 Registry 的构建由 Composition Root 与 presentation 自身负责。

## 新增项

- `ModuleContribution` 中的唯一结构化 `PresentationAdapter` 协议；
- `ModuleRegistry.presentation_adapters()` 只返回已安装适配器集合。

## 唯一职责

`modules` 拥有模块贡献合同和已安装贡献聚合，不构建展示子系统。`presentation` 拥有 PresentationRegistry、正式展示合同与 Release Gate。`composition` 把已安装适配器显式交给 PresentationRegistry。

## 替换或删除项

- 删除 `modules.contracts -> presentation.adapters`；
- 删除 `modules.registry -> presentation.registry`；
- 删除 `ModuleRegistry.build_presentation_registry()`；
- `agent_core.presentation.adapters.PresentationAdapter` 保留为指向唯一协议的兼容导出，不产生第二套定义。

## 删除证据

- `agent_core.modules` 目录不得导入 `agent_core.presentation`；
- `ModuleRegistry` 只暴露 `presentation_adapters()`；
- Composition Root 必须创建 `PresentationRegistry(registry.presentation_adapters())`；
- 主 SCC 从 9 降为至多 8，removed_members 包含 modules/kernel/resources/ledger/rag/utils；
- 不允许修改依赖债务基线制造缩减。

## 验证

- Module / Presentation 依赖方向反例；
- 模块安装与展示适配器注册回归；
- B1-B5 累计依赖债务回归；
- Agent/Business 全量、前端 Vitest 与生产构建；
- 应用启动、完整 HTTP 生命周期和真实 Chromium。

## 明确不处理

- 不修改展示选择、优先级、结构化 Contract 或 Renderer；
- 不修改具体业务模块的适配器实现；
- 不修改 Agent Loop、State、Transaction 或 Business Service；
- 剩余 8 包 SCC 由后续独立 Target 处理。
