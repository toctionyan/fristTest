# 目标

- 目标 ID：repair-stage4-2-dependency-bridge-obligation-evidence
- 变更标识：portable-repair-stage4-2-dependency-bridge-obligation-evidence
- 执行上下文：local-change
- 目标类型：repair

Close the Stage 4.2 dependency-proof bridge authority leak: a validated pair decision is observation only and must not mint target-compatibility or counterfactual PASS without obligation-specific validated evidence.

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/goal_graph/dependency_alignment.py`, `services/agent-service/tests/runtime/test_dependency_alignment_authority.py`
- 新增抽象记录：无；继续使用现有 dependency ProofObservation + deterministic reducer authority owner

## 禁止范围

Do not modify CapabilityGate, GoalOutputRef, transaction Draft/Grant/Attempt/Receipt authority, business tools/services, production dependency-authority activation/defaults, Skill/Judge/Quality policy, or create a second dependency authority owner. Do not weaken existing reducer obligations or tests to obtain a pass.

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-stage4-2-dependency-bridge-obligation-evidence.json`
- 验收 ID：`STAGE4_2.DEPENDENCY_BRIDGE.OBLIGATION_EVIDENCE`

A pairwise decision, complete/matching diagnostic, or adversarial phase name alone cannot produce target_compatibility=PASS or counterfactual=PASS. Missing or malformed obligation-specific evidence must remain UNKNOWN/fail closed. Explicit validated obligation evidence may be consumed, but the deterministic dependency-proof reducer remains the only authority seal.

## 基线

Current PR #1157 head before this repair directly self-mints target_compatibility=PASS and counterfactual=PASS in dependency_alignment.py from a pair decision/phase, while the pairwise validator exposes no independent per-pair target/counterfactual proof object. The existing bridge test demonstrates that closure phase alone can therefore mature such a row to authority.

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
