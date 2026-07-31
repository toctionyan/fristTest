# V20.17 B4：Resource Registry 显式注入边界

## 状态

本记录定义 B4 迁移目标。实现前，`agent_core.resources.TargetResolver` 在未传入 Registry 时会从 `agent_core.modules` 查询全局 RuntimeRegistry；实现后，资源层不再知道模块装配或全局服务定位器。

## 新增项

- TargetResolver 的 `ResourcePluginRegistry` 显式构造依赖。

## 唯一职责

`resources` 只拥有：

- ResourcePlugin 合同与 Registry；
- ResolvedTargetSet 值对象；
- 基于已验证成员的资源类型和集合校验。

Application Use Case 与已安装业务模块负责从其现有 Composition/Runtime Registry 上下文取得 `resources` Registry，并显式传入 TargetResolver。

## 替换或删除项

- 删除 `TargetResolver` 内部对 `agent_core.modules.current_runtime_registry` 的懒加载；
- 不新增 resources 级全局 Provider；
- 不修改 ResourcePlugin、ResolvedTargetSet、目标集合或 Capability 行为。

## 删除证据

- `agent_core.resources` 包内不得导入 `agent_core.modules`；
- TargetResolver 构造参数不得存在隐藏默认 Registry；
- Architecture Gate 的主 SCC 从 11 个成员降为 10 个成员，`removed_members` 包含 `resources`、`ledger`、`rag`、`utils`；
- 不允许修改依赖债务基线制造缩减。

## 验证

- Resource Registry 显式注入架构反例；
- TargetResolver 与 OperationPreparation 既有测试；
- B1/B2/B3 累计依赖债务回归；
- Architecture Convergence Gate；
- Agent 与 Business Python 全量；
- 前端 Vitest、生产构建、HTTP 生命周期和真实 Chromium。

## 明确不处理

- 不重构 ModuleRegistry 或 Composition Root；
- 不修改资源插件实现、目标解析语义或能力选择；
- 不修改 Agent Loop、Context、Transaction、Presentation 或 Business Service；
- 剩余 10 包 SCC 由后续独立 Target 处理。
