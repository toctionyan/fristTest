# V20.17 B3：Ledger 与 TransactionDraft 合同边界

## 状态

本记录定义 B3 迁移目标。实现前，`agent_core.transaction.model` 同时承担纯 Draft 数据合同和事务包兼容入口；实现后，纯合同只有一个中立 Owner。

## 新增项

- `agent_core.operations.draft`：纯 TransactionDraft 数据状态、规范化、命令摘要和展示投影合同。

## 唯一职责

`operations.draft` 只拥有无 I/O、无仓库、无授权和无业务服务调用的 Draft 数据代数：

- Draft 状态常量；
- effect-bearing command payload 规范化；
- command digest；
- Draft carrier 规范化与纯状态迁移；
- 非权威展示投影。

`transaction` 继续唯一拥有授权、Attempt、持久化、提交、对账和恢复；`ledger` 只保存并投影已验证 carrier，不能反向依赖事务执行包。

## 替换或删除项

- `agent_core.transaction.model` 不再保存第二份实现，只保留对 `agent_core.operations.draft` 的显式兼容导出；
- `agent_core.ledger.ledger` 改为直接依赖中立 Draft 合同；
- 不删除 `agent_core.transaction` 对外公开符号，不迁移事务执行权威。

## 删除证据

- `ledger` 包内不得导入 `agent_core.transaction`；
- `transaction.model` 不得重新定义 Draft 状态或算法；
- Architecture Gate 的主 SCC 从 12 个成员降为 11 个成员，`removed_members` 包含 `ledger`、`rag`、`utils`；
- 不允许修改依赖债务基线制造缩减。

## 验证

- Ledger/Draft 合同架构反例；
- 既有 Transaction Protocol 和 Ledger 测试；
- Architecture Convergence Gate；
- Agent 与 Business Python 全量；
- 前端 Vitest、生产构建、HTTP 生命周期和真实 Chromium。

## 明确不处理

- 不修改 TransactionDraft 状态机、摘要算法或 Schema；
- 不重构事务仓库、授权或提交节点；
- 不修改 Agent Loop、Context、Presentation 或 Business Service；
- 剩余 11 包 SCC 由后续独立 Target 处理。
