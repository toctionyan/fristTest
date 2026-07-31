# 目标

- 目标 ID：migration-v20.13-capability-contract-v2
- 变更标识：portable-migration-v20.13-capability-contract-v2
- 执行上下文：local-change
- 目标类型：migration

为 Capability 建立版本化、领域无关的规划合同，明确 target、requires、produces、preconditions、authorization、completion proof、freshness、idempotency 与 resource conflict。先完整覆盖物流查询、退款资格/申请、发票查询/申请三条垂直业务；本阶段不切换正式 Planner，不让通用 Runtime 硬编码电商步骤。

## 允许范围

- 新增抽象记录：docs/architecture/V20_13_CAPABILITY_CONTRACT_V2.md
- 允许变更路径：services/agent-service/src/agent_core/kernel/capability.py, services/agent-service/src/agent_core/kernel/capability_registry.py, services/agent-service/src/agent_modules/ecommerce/capabilities/spec.py, services/agent-service/src/agent_modules/ecommerce/capabilities/get_order_logistics.py, services/agent-service/src/agent_modules/ecommerce/capabilities/evaluate_refund_eligibility.py, services/agent-service/src/agent_modules/ecommerce/capabilities/prepare_refund.py, services/agent-service/src/agent_modules/ecommerce/capabilities/prepare_refund_from_eligibility.py, services/agent-service/src/agent_modules/ecommerce/capabilities/list_invoices.py, services/agent-service/src/agent_modules/ecommerce/capabilities/prepare_invoice.py, services/agent-service/tests/runtime/test_capability_contract_v2.py, services/agent-service/tests/runtime/test_goal_binding_counterexamples.py, docs/architecture/**
- `services/agent-service/src/agent_core/kernel/capability.py`
- `services/agent-service/src/agent_core/kernel/capability_registry.py`
- `services/agent-service/src/agent_modules/ecommerce/capabilities/spec.py`
- `services/agent-service/src/agent_modules/ecommerce/capabilities/get_order_logistics.py`
- `services/agent-service/src/agent_modules/ecommerce/capabilities/evaluate_refund_eligibility.py`
- `services/agent-service/src/agent_modules/ecommerce/capabilities/prepare_refund.py`
- `services/agent-service/src/agent_modules/ecommerce/capabilities/prepare_refund_from_eligibility.py`
- `services/agent-service/src/agent_modules/ecommerce/capabilities/list_invoices.py`
- `services/agent-service/src/agent_modules/ecommerce/capabilities/prepare_invoice.py`
- `services/agent-service/tests/runtime/test_capability_contract_v2.py`
- `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`
- `docs/architecture/**`

## 禁止范围

不得修改 Skill、Quality Policy、Judge、Business Service、事务状态机、正式 Tool 执行顺序、Grounded Planner 或 Presentation；不得在通用 Runtime 中写死退款、发票、物流业务步骤；不得把 Draft 标记为最终业务完成证据。

## 验收条件

- 最低质量模式：quick
- 声明清单：governance/claims/migration-v20.13-capability-contract-v2.json
- 验收 ID：CAPABILITY-CONTRACT-V2-001, VERTICAL-CAPABILITY-CLOSURE-001, CAPABILITY-COMPLETION-PROOF-001
- V2 合同缺少 planning contract、target、requires/produces、completion proof 时必须 fail closed。
- requires 与 produces 名称必须唯一，freshness、authorization、idempotency、resource conflict 必须具有确定性结构。
- 物流与发票查询由 tool output 作为完成证明。
- 退款与发票申请 Tool 只产生 Draft；最终业务完成证明必须来自 transaction authority 的 Receipt。
- 三条垂直链的合同由 ecommerce module 声明，Kernel 只验证结构。
- 原 V20.12 Goal Evidence、强上下文、多意图、事务和浏览器代表链不得回归。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后只修改 Capability Contract v2、模块合同声明及对应测试；不得提前切换 V20.14 Planner。

## 基线

在 V20.12.0 Goal Change Evidence 候选源码上仅加入本阶段目标、Decision、Claims 和反例，记录 Capability Contract v2 缺失的真实红基线。
