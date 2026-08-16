# 目标

- 目标 ID：repair-release55-dependency-output-role-overlap
- 变更标识：portable-repair-release55-dependency-output-role-overlap
- 执行上下文：local-change
- 目标类型：repair

Close Release #55 missing true dependency caused by structural conflation of a relation-only basis with a broader requested-output evidence span.

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/goal_planning.py`, `services/agent-service/tests/runtime/test_release55_dependency_output_role_overlap.py`
- 新增抽象记录：无

## 禁止范围

Do not change the semantic oracle, dependency reducer, Stage 4.2 obligation-evidence bridge, CapabilityGate, transaction authority, business tools, production defaults, Skill/Judge/Quality policy, or weaken any existing dependency tests.

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-release55-dependency-output-role-overlap.json`
- 验收 ID：`RELEASE55.DEPENDENCY_OUTPUT_ROLE_OVERLAP`

The exact protected Release #55 failure must be reproduced structurally. A true same-turn result reference with basis '它' nested inside broader output evidence '它能不能退款' must not be rejected as requested-output evidence; it must remain provisional and be subject to the existing counterfactual/adversarial closure. False Draft/support-dataflow dependencies must still close as independent.

## 基线

Canonical baseline: Protected Production Certification Release #55 failed on semantic_query_then_refund_consult with expected g2->g1 but actual empty dependencies. The current pairwise structural validator rejects basis '它' whenever requested-output evidence is the broader phrase '它能不能退款', then format-repair instructions explicitly tell the verifier to return independent when no disjoint basis exists.

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
