# V20.17 B12 Transaction / Runtime Dependency Boundary

## 新增项

`agent_core.transaction.deps.TransactionExecutionDeps` 是事务执行阶段唯一显式依赖信封，携带应用装配好的 `BusinessPort` 与 Runtime 提供的 Outcome Factory。`agent_core.kernel.decision_trace` 提供无业务权威的纯 Decision Trace 追加函数。

## 唯一职责

- Transaction：拥有 Draft、Grant、Attempt、Receipt、预检和提交状态机，只消费显式执行依赖。
- Runtime：拥有 RuntimeOutcome 的具体构造、校验和工具结果归类，并把 Outcome Factory 显式交给事务调用方。
- Lifecycle：拥有图路由并把已装配的事务执行依赖传给事务节点。
- Application Composition：选择具体 BusinessPort，不允许事务层自行查询全局 Provider。

## 替换或删除项

删除 transaction 对 `agent_core.runtime.*` 的所有导入；删除事务节点内部的 `get_business_port()` 隐式查询；删除 transaction 对 Runtime Decision helper 的反向依赖。

## 删除证据

架构反例 AST 扫描整个 transaction 包，禁止 runtime 导入与隐藏 BusinessPort 查询；正式依赖图必须显示 transaction 从主 SCC 移除，且依赖债务基线不得修改。

## 验证

事务 Outcome 内容、授权、预检、提交、对账、幂等和 Receipt 行为保持不变；完整 Quick、HTTP 生命周期和真实 Chromium 全部通过。
