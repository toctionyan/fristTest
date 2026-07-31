# 目标

- 目标 ID：repair-v20.17-b14b-grounded-projection-quarantine
- 变更标识：portable-repair-v20.17-b14b-grounded-projection-quarantine
- 执行上下文：local-change
- 目标类型：repair

确保 State Schema v2 中的 `grounded_execution_plan` 只能由 `frozen_plan_definition + plan_run` 确定性派生，持久化兼容投影不得成为第二套 Plan 权威。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/state_schema.py`, `services/agent-service/src/agent_core/lifecycle/plan_execution.py`, `services/agent-service/tests/runtime/test_state_v2_grounded_projection_quarantine.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B14B_GROUNDED_PROJECTION_QUARANTINE.md`
- 新增抽象记录：无

## 禁止范围

不得修改 State Schema 版本、不得恢复 `workflow_plan`、不得把兼容投影提升为写入 Owner、不得放宽 Frozen Definition 或 Plan Run 完整性校验。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14b-grounded-projection-quarantine.json`
- 验收 ID：`V20-17-B14B-PLAN-AUTHORITY-001`

v2 checkpoint 中伪造或过期的 grounded projection 必须被正式定义/运行对覆盖；没有权威对的孤立投影必须清空；现有计划执行行为保持不变。

## 基线

红基线：B14b 对抗反例在修复前失败，证明 Schema v2 原样信任持久化 grounded projection。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修改计划投影唯一 Owner 与 State Schema 规范化边界；没有有效进展时停止并重新规划。
