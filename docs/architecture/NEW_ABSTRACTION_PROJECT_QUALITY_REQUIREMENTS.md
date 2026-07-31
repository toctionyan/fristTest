# 新抽象替换记录：项目级质量需求目录与注册表 Provider

## 项目级质量需求目录

- 新增项：`governance/requirements/project-quality-requirements.json` 与项目级 requirement profile 校验。
- 唯一职责：在 target 和 claim manifest 之外保存独立、可审查的项目验收需求集合，并让 certification 必须完整覆盖所选 profile。
- 替换或删除项：替换“target 验收 ID 与 claim ID 相等即可代表需求完整”的隐含假设；CI 不再把 repair transition claim 直接包装为 current certification。
- 为什么不能并入现有 Owner：target 拥有单次变更范围，claim manifest 拥有证明映射；项目长期有效的需求集合必须有独立 Owner，避免二者同时遗漏同一要求。
- 迁移顺序：先加入失败反例和 repair baseline，再实现目录校验、项目级 manifests、CI profile 选择，最后以完整 Quick/Integration certification 验证。
- 删除证据：删除 CI 对 `v20.6.1-closed-repair-loop.json` 的直接 certification 复用，并移除浅层 capability execution selector。
- 验证：遗漏 profile requirement、复制未绑定 transition claim、缺失 Business integration suite、重复 goal ID、未锁环境和 Core 反向依赖均必须被自动 Gate 拒绝。

## 既有 modules 注册表的 Provider 扩展

- 新增项：`agent_core.modules.registry` 中的 Composition-owned runtime/module provider 配置点。
- 唯一职责：让 Core 依赖既有 modules 合同取得已装配注册表，而不反向导入 `agent_core.composition`。
- 替换或删除项：删除 `kernel`、`transaction`、`resources`、`presentation`、`rag` 对 Composition Root 的反向 import；不新增平行 Registry 文件。
- 为什么不能并入现有 Owner：该能力已并入现有 `ModuleRegistry` Owner；Composition 仍唯一拥有具体模块安装和 factory 配置。
- 迁移顺序：先安装 provider，再替换 Core consumer import，最后启用 AST 依赖图 Gate。
- 删除证据：`agent_core` 的 composition 目录之外不再出现 `from agent_core.composition`。
- 验证：架构 Gate 的 `reverse_composition_imports` 与 Composition 保护环 `package_dependency_cycles` 必须为空，并保留全 Core SCC 诊断。
