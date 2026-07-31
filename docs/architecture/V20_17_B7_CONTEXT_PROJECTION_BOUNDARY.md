# V20.17 B7：Context 只读投影边界

## 状态

本记录定义 B7 迁移目标：ContextBundle 的确定性只读状态投影由 context 拥有，lifecycle 继续独占状态写入和转换，storage 继续独占事务仓库实现。

## 新增项

- `agent_core.context.state_projection`：GoalRecord、GoalBlocker 与 legacy clarification 的只读 Context 投影；
- ContextBundle 内部最小 `TransactionContextRepository` 协议和等价 scope 值对象。

## 唯一职责

`context` 只读取已存在的状态与事务摘要并生成模型上下文，不修改状态。`lifecycle` 负责状态创建、修订、迁移和转换。`storage` 负责具体事务仓库及完整持久化合同。

## 替换或删除项

- 删除 `context.context_bundle -> lifecycle.*` 三组投影导入；
- 删除 `context.context_bundle -> storage.repositories.base` 类型导入；
- lifecycle 中原只读投影名称保留为指向 context 唯一实现的兼容导入；
- 不复制 Goal/Blocker/Clarification 写入逻辑。

## 删除证据

- `agent_core.context` 不得导入 `agent_core.lifecycle` 或 `agent_core.storage`；
- lifecycle 兼容入口必须与 context 函数对象相同；
- ContextBundle 的字段、排序、authority 标记与旧检查点兼容规则保持不变；
- 主 SCC 从 8 降为至多 7，removed_members 包含 context/modules/kernel/resources/ledger/rag/utils；
- 不允许修改依赖债务基线制造缩减。

## 验证

- Context 反向依赖与兼容函数身份反例；
- ContextBundle、Goal、Blocker、Clarification 现有测试；
- B1-B6 累计依赖债务回归；
- Agent/Business 全量、前端 Vitest 与生产构建；
- 应用启动、完整 HTTP 生命周期和真实 Chromium。

## 明确不处理

- 不改变 ContextBundle token/字符预算或长期记忆策略；
- 不改变任何状态写入、revision 或迁移规则；
- 不修改 Agent Loop、Transaction 状态机或 Business Service；
- 剩余 7 包 SCC 和 State/Loop 瘦身由后续独立 Target 处理。
