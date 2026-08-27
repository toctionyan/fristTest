# 目标

- 目标 ID：migration-v20.20-stage2b1-trusted-evidence-ingress
- 变更标识：portable-migration-v20.20-stage2b1-trusted-evidence-ingress
- 执行上下文：local-change
- 目标类型：migration

将应用组合层的可信输入证据、可信评估时间和 issuer 校验器接入 Typed Goal Capability shadow；保持 legacy 选择、Permit、dispatch、事务权威和 production_closed 不变。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/runtime/typed_goal_evidence_ingress.py`, `services/agent-service/src/agent_core/runtime/deps.py`, `services/agent-service/src/agent_core/lifecycle/graph.py`, `services/agent-service/src/agent_core/lifecycle/nodes.py`, `services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py`, `services/agent-service/src/agent_core/lifecycle/pretool_planner.py`, `services/agent-service/src/agent_core/lifecycle/goal_capability_coverage.py`, `services/agent-service/app/services/agent_service.py`, `services/agent-service/tests/runtime/test_typed_goal_evidence_ingress.py`
- 新增抽象记录：governance/decisions/migration-v20.20-stage2b1-trusted-evidence-ingress.md

## 禁止范围

不得从 checkpoint、用户输入或模型输出取得 trust root；不得新增 capability fallback；不得改变 Typed Coverage→MatchProof→ExecutionPermit 切换；不得修改 Quality Policy、Claim、Baseline、Judge、Business Service 或 production closure。

## 验收条件

- 最低质量模式：quick
- 声明清单：governance/claims/migration-v20.20-stage2b1-trusted-evidence-ingress.json
- 验收 ID：STAGE2B1-TRUSTED-EVIDENCE-INGRESS-001

真实 pretool shadow 调用通过 application-owned resolver 获得证据输入、评估时间和 issuer validators；缺失或篡改时 fail-closed 且不暴露原始证据；现有 shadow-only 不变量保持成立。

## 基线

Baseline: 在候选实现前，可信 ingress 不能从应用组合层到达真实 pretool shadow 调用；定向反例应为红，环境不可用时必须保留结构化阻塞证据。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
