# 目标

- 目标 ID：repair-v20.17-b14a-state-v2-retired-field-quarantine
- 变更标识：portable-repair-v20.17-b14a-state-v2-retired-field-quarantine
- 执行上下文：local-change
- 目标类型：repair

确保 State Schema v2 运行时完全忽略已退休的 turn_goal_plan、workflow_plan 和 pending_clarification，旧字段只能在显式 legacy checkpoint 迁移边界读取。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/clarification_runtime.py`, `services/agent-service/tests/runtime/test_state_v2_retired_field_quarantine.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B14A_STATE_V2_RETIRED_FIELD_QUARANTINE.md`
- 新增抽象记录：无

## 禁止范围

不得把旧字段重新提升为 v2 权威，不得修改 State Schema 版本或放宽迁移失败策略。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14a-state-v2-retired-field-quarantine.json`
- 验收 ID：`V20-17-B14A-STATE-AUTHORITY-001`

v2 下伪造的 turn_goal_plan/workflow_plan/pending_clarification 对 suspended goals、blockers 和 clarification projection 均无影响；v1 兼容路径仍按现有合同工作。

## 基线

红基线：adversarial-runtime-counterexamples 中的 B14a 反例在修复前必须失败，证明旧字段可渗透 clarification runtime。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
