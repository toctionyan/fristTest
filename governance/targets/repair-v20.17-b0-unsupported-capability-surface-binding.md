# 目标

- 目标 ID：repair-v20.17-b0-unsupported-capability-surface-binding
- 变更标识：portable-repair-v20.17-b0-unsupported-capability-surface-binding
- 执行上下文：local-change
- 目标类型：repair

修复已被能力发现证明不存在的用户目标在规划到执行之间丢失 capability_surface 证据，确保系统成功报告不支持而不是错误进入 FAILED_FINAL。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/lifecycle/state.py`, `services/agent-service/src/agent_core/lifecycle/state_contracts.py`, `services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py`, `services/agent-service/src/agent_core/runtime/outcomes.py`, `services/agent-service/tests/runtime/test_unsupported_capability_surface_binding.py`
- 新增抽象记录：无

## 禁止范围

不得新增相似能力回退，不得修改用户 Goal，不得降低 CapabilityGate，不得改变事务与授权链。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b0-unsupported-capability-surface-binding.json`
- 验收 ID：`UNSUPPORTED-CAPABILITY-SURFACE-BINDING-001`

原始 V20.16 中可复现的 unsupported 场景由 FAILED_FINAL 转为 SUCCEEDED；相似能力拒绝、目标效果绑定、事务和全量 Python 回归保持通过。

## 基线

在原始 V20.16 最终包和当前工作树中记录真实红基线：unsupported 场景稳定失败，MatchProof 缺少执行阶段 capability_surface。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
