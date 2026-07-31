# 目标

- 目标 ID：migration-v20.17-b12b-transaction-runtime-boundary
- 变更标识：portable-migration-v20.17-b12b-transaction-runtime-boundary
- 执行上下文：local-change
- 目标类型：migration

让 transaction 只消费显式 BusinessPort 与 Runtime Outcome Factory，删除 transaction 对 runtime 的反向依赖和隐藏 Provider 查询，使 transaction 退出主 SCC；事务状态机、授权、幂等、Receipt 和 RuntimeOutcome 行为保持不变。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/kernel/outcome_contract.py`, `services/agent-service/src/agent_core/kernel/decision_trace.py`, `services/agent-service/src/agent_core/runtime/node_support.py`, `services/agent-service/src/agent_core/runtime/deps.py`, `services/agent-service/src/agent_core/transaction/deps.py`, `services/agent-service/src/agent_core/transaction/availability.py`, `services/agent-service/src/agent_core/transaction/lifecycle_query.py`, `services/agent-service/src/agent_core/transaction/operation_preparation.py`, `services/agent-service/src/agent_core/transaction/gateway_runtime.py`, `services/agent-service/src/agent_core/transaction/commit_runtime.py`, `services/agent-service/src/agent_core/transaction/interaction_runtime.py`, `services/agent-service/src/agent_core/lifecycle/graph.py`, `services/agent-service/src/agent_core/lifecycle/nodes.py`, `services/agent-service/app/services/agent_service.py`, `services/agent-service/app/services/lifecycle_command_runner.py`, `services/agent-service/app/use_cases/transaction_start.py`, `services/agent-service/app/use_cases/interaction_submit.py`, `services/agent-service/src/agent_modules/ecommerce/shared/runtime_tools.py`, `services/agent-service/src/agent_modules/ecommerce/shared/prepare_actions.py`, `services/agent-service/src/agent_modules/ecommerce/shared/refund_eligibility.py`, `services/agent-service/tests/support/runtime_support.py`, `services/agent-service/tests/support/conversation_case_runner.py`, `services/agent-service/tests/architecture/test_runtime_contract.py`, `services/agent-service/tests/architecture/test_transaction_runtime_boundary_scc.py`, `services/agent-service/tests/context/test_dialogue_counterexamples.py`, `docs/architecture/V20_17_B12_TRANSACTION_RUNTIME_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B12_TRANSACTION_RUNTIME_BOUNDARY.md

## 禁止范围

不得改变 Draft/Grant/Attempt/Receipt 状态机、业务命令、幂等键、授权语义、RuntimeOutcome 公开结构、业务模块能力、Agent Loop 或 State Schema；不得新增全局 Transaction Provider、动态兼容入口或第二套 Outcome 实现；不得修改质量策略或依赖债务基线。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b12b-transaction-runtime-boundary.json`
- 验收 ID：`TRANSACTION-RUNTIME-BOUNDARY-SCC-B12B-001`

Transaction 不再导入 runtime 且不隐藏查询 BusinessPort；Lifecycle/Application 显式传入事务执行依赖；Runtime 继续拥有具体 Outcome 实现；主 SCC 从 3 降到至多 2，transaction 与 B1-B11 已移出包保持退出。

## 基线

旧基线由 transaction 导入 runtime.outcomes、runtime.node_support，并通过 Runtime 包装器查询全局 BusinessPort，主 SCC 为 3；新反例失败，B11 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复显式依赖传递、Outcome Factory 协议和 Decision Trace 纯 helper；若事务权威、业务命令或 Outcome 语义变化，停止并重新规划。
