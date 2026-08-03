# 目标

- 目标 ID：repair-v20.17-b22-transaction-identity-closure
- 变更标识：repair-v20.17-b22-transaction-identity-closure
- 执行上下文：local-change
- 目标类型：repair

完成 Stage 4 的两个产品工作包：WP-06 多 Draft、交互焦点和事务恢复；WP-07 Actor、Subject、Resource 与 Business Service 独立授权边界。不得把缺少锁定 LangGraph/PostgreSQL/前端环境的集成测试伪装为通过。

## 允许范围

- 允许变更路径：`services/agent-service/app/api/product_api.py`, `services/agent-service/app/schemas/chat_schema.py`, `services/agent-service/app/security.py`, `services/agent-service/app/services/agent_service.py`, `services/agent-service/app/services/response_projector.py`, `services/agent-service/app/services/stale_interaction.py`, `services/agent-service/app/use_cases/conversation_turn.py`, `services/agent-service/app/use_cases/interaction_submit.py`, `services/agent-service/app/use_cases/transaction_start.py`, `services/agent-service/src/agent_core/business/__init__.py`, `services/agent-service/src/agent_core/business/contracts.py`, `services/agent-service/src/agent_core/config.py`, `services/agent-service/src/agent_core/ledger/ledger.py`, `services/agent-service/src/agent_core/lifecycle/state.py`, `services/agent-service/src/agent_core/lifecycle/state_contracts.py`, `services/agent-service/src/agent_core/lifecycle/state_schema.py`, `services/agent-service/src/agent_core/observability/flow_debug.py`, `services/agent-service/src/agent_core/operations/base.py`, `services/agent-service/src/agent_core/security/auth_provider.py`, `services/agent-service/src/agent_core/transaction/active_draft.py`, `services/agent-service/src/agent_core/transaction/commit_runtime.py`, `services/agent-service/src/agent_core/transaction/coordinator.py`, `services/agent-service/src/agent_core/transaction/failure.py`, `services/agent-service/src/agent_core/transaction/focus.py`, `services/agent-service/src/agent_core/transaction/gateway_runtime.py`, `services/agent-service/src/agent_core/transaction/lifecycle_query.py`, `services/agent-service/src/agent_core/transaction/reconciliation.py`, `services/agent-service/src/agent_modules/ecommerce/shared/context.py`, `services/agent-service/tests/architecture/test_runtime_foundations.py`, `services/agent-service/tests/transactions/test_stage4_transaction_focus.py`, `services/agent-service/tests/transactions/test_transaction_protocol.py`, `services/business-service/business_service/api_models.py`, `services/business-service/business_service/application/core_service.py`, `services/business-service/business_service/application/operation_commands.py`, `services/business-service/tests/test_stage4_identity_security.py`
- 新增抽象记录：`focused_draft_id`, `SubjectContext`, `ResourceScopeAssertion`, pure transaction failure/stale-interaction projection

## 禁止范围

不得修改 Skill 控制器、治理规则、发布状态、前端、数据库部署配置或生产认证结论；不得删除、跳过或弱化测试；不得以 `active_draft_id` 重新建立第二事务权威；不得信任客户端自报的 actor/subject/resource 身份。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b22-transaction-identity-closure.json`
- 验收 ID：V20-17-B22-STAGE4-001

必须满足：

1. 多个持久 Draft 可同时存在，`focused_draft_id` 是唯一交互焦点权威，`active_draft_id` 仅为兼容投影；
2. 一个 Draft 终态不得覆盖其他仍开放 Draft；焦点只有在唯一候选时自动转移，歧义时必须要求选择；
3. 过期 Interaction 返回最新焦点、当前 Interaction 与 Pending Transactions；
4. Actor、Subject 与 Resource Scope 在认证、Agent 状态、命令传输和 Business Service 验证中保持分离；
5. 客户端伪造 Subject 被认证边界覆盖；租户、角色、资源类型、资源 ID、主体和版本不一致均被拒绝；
6. Business Service 继续以领域所有权和资源状态为最终权威；
7. Agent 非环境事务/架构回归 63 passed、1 个 PostgreSQL integration 用例明确 deselected；Business Service 38 passed；
8. 独立 DiffReview 证明修改仅限本目标列出的文件；
9. 缺少 Python 3.12.13、langgraph/langchain_core、真实 PostgreSQL、前端与真实模型的验证继续归属 WP-08，`production_closed=false`。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复被证据证明的公共事务或身份合同根因，不增加业务关键词分支、静默 fallback 或第二权威。

## 基线

红基线由 B21 产品快照加上只读安全 Oracle `test_stage4_identity_security.py` 构成。该 Oracle 在旧实现上真实失败。现有可重复证据证明：缺少 canonical `focused_draft_id`，旧 `active_draft_id` 同时承担持久事务与交互焦点；Actor 身份被默认等同于 Subject，Business Service 未完整保留和核验 Subject/Resource Scope。基线验证应失败于这些声明，而不是失败于无关环境依赖。
