# 目标

- 目标 ID：repair-stage4-2-dependency-obligation-evidence-pipeline
- 变更标识：portable-repair-stage4-2-dependency-obligation-evidence-pipeline
- 执行上下文：local-change
- 目标类型：repair

Close the Stage 4.2 dependency-obligation evidence pipeline: pair relation evidence is observation only, target compatibility and current-turn result-removal counterfactual require separately validated premise-bound evidence, and the existing deterministic dependency reducer remains the sole authority seal.

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/goal_graph/dependency_alignment.py`, `services/agent-service/src/agent_core/lifecycle/goal_planning.py`, `services/agent-service/tests/runtime/test_dependency_alignment_authority.py`, `services/agent-service/tests/runtime/test_dependency_obligation_evidence_pipeline.py`
- 新增抽象记录：无
- 复用现有边界：GoalAlignment semantic verifier boundary、ProofObservation 与 deterministic dependency reducer；不新增并列 authority owner。

## 禁止范围

Do not modify dependency_proof.py, CapabilityGate, GoalOutputRef, transaction Draft/Grant/Attempt/Receipt authority, business tools/services, production dependency-authority activation/defaults, Skill/Judge/Quality policy, or create a peer dependency authority owner. Do not change this Target, Claim, focused acceptance policy, baseline, Judge, or evidence to make a candidate pass.

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/repair-cases/repair-stage4-2-dependency-obligation-evidence-pipeline/quality-input/claim.json`
- 验收 ID：`STAGE4_2.DEPENDENCY_OBLIGATION_PIPELINE`

A structurally valid pair decision, complete/matching diagnostic, adversarial phase name, or call count alone must leave target_compatibility and counterfactual unresolved. A separate semantic-verifier-produced, frozen-premise-bound obligation evidence envelope may satisfy those obligations; malformed, missing, spoofed, relation-mismatched or premise-mismatched evidence fails closed. dependency_proof.py remains the only maturity/authority reducer.

## 基线

The exact pre-repair feature source reproduces false authority: dependency_alignment.py hard-codes target_compatibility=PASS and counterfactual=PASS from a normalized pair decision, while goal_planning.py emits no distinct obligation evidence contract. The pre-change Quality Loop baseline is recorded after the repair governance inputs and ChangePermit are frozen and before any allowed product source is changed; the focused counterexample must be RED. The structured `新增抽象记录` value is exactly `无`; the explanatory reuse note is deliberately separate so the Quality controller reads the declared value without prose contamination.

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
