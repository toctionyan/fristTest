# V20.17 B5：Kernel Runtime Registry 显式注入边界

## 状态

本记录定义 B5b 迁移目标。前一尝试证明仅修改实现模块不足：`agent_core.kernel` 公共包装器仍是零参数并导致完整生命周期启动失败。最终边界必须让实现函数与公共 API 同时显式接收 RuntimeRegistry。

## 新增项

- `agent_core.kernel.integrity.validate_runtime_architecture(registry)` 显式参数；
- `agent_core.kernel.validate_runtime_architecture(registry)` 同签名公共入口。

## 唯一职责

`kernel` 只拥有 RuntimeRegistry 与稳定 Kernel 合同，并对调用方显式传入的 Registry 执行结构完整性校验。Composition Root 创建 Registry，`app.main` 在启动时传入。

## 替换或删除项

- 删除 `kernel.integrity` 对 `agent_core.modules.current_runtime_registry` 的导入和隐藏查询；
- 删除公共入口的零参数包装行为，但保留公共符号；
- 不移动 RuntimeRegistry 权威，不新增全局 Provider，不修改完整性规则。

## 删除证据

- `agent_core.kernel` 不得导入 `agent_core.modules`；
- 实现函数和公共入口的 `registry` 参数均不得有默认值；
- `app.main` 必须调用 `validate_runtime_architecture(get_runtime_registry())`；
- 主 SCC 从 10 降为至多 9，removed_members 包含 kernel/resources/ledger/rag/utils；
- 不允许修改依赖债务基线制造缩减。

## 验证

- 实现与公共 API 显式注入反例；
- B1-B4 累计依赖债务回归；
- 应用启动、完整 HTTP 生命周期和真实 Chromium；
- Agent/Business 全量、前端 Vitest 与生产构建。

## 明确不处理

- 不重构 ModuleRegistry、Composition Root 或 RuntimeRegistry 内部；
- 不修改 Agent Loop、State、Transaction、Presentation 或 Business Service；
- 剩余 9 包 SCC 由后续独立 Target 处理。
