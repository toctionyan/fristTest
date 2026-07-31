# V20.14 Pre-Tool Grounded Planner Shadow

## 目标

在模型选择具体业务 Tool 之前，将冻结 Goal 与 Capability Contract v2 编译成只读局部计划。该计划只用于验证架构和收集偏差，不拥有执行权。

## 正向链路

```text
FrozenSemanticContract
  → Exact Capability Surface
  → Module-owned Capability Contract v2
  → Candidate capability paths
  → Type-based input/output closure
  → Preferred shadow path
  → Model Tool Call
  → Shadow comparison（只读）
  → 原 MatchProof / Permit / Executor
```

## 不变量

- Planner 不解释用户语言。
- Planner 不按 Tool 名称猜业务步骤。
- `capability_output` 输入只能由相同 `type_name` 的声明输出满足。
- 外部输入只记录合同允许的来源，Shadow 不伪造具体值。
- Shadow 不进入模型提示，不影响 Tool surface。
- Shadow 不创建 Permit、不调用 Tool、不修改事务。
- Goal 间依赖只复制冻结语义 `depends_on`。

## 当前阶段边界

V20.14 允许并行保留多个候选路径。例如 `refund.create` 同时可能有：

1. `prepare_refund` 直接路径；
2. `evaluate_refund_eligibility → prepare_refund_from_eligibility` 合同输出闭合路径。

Preferred path 只按“路径闭合、步骤数量、Tool 名称”做确定性选择，不表示正式执行裁决。V20.15 前不得用该选择替代模型 Tool Call 与正式 Permit。

## 新增抽象记录

- 新增项：`agent_core.lifecycle.pretool_planner`，提供 `build_pretool_shadow_plan` 与 `compare_shadow_plan_to_model_calls`。
- 唯一职责：在模型业务 Tool Call 之前，把冻结 Goal 与模块 Capability Contract v2 编译为只读候选路径和偏差证据；不拥有执行、权限、事务或语义修改职责。
- 替换或删除项：本阶段不替换正式执行链；它替代的是“只能在 Tool Call 后才能观察计划缺口”的诊断盲区。V20.15/V20.16 必须决定正式接管或删除 Shadow 接入，禁止永久双 Planner。
- 删除证据：回滚时删除 `pretool_planner.py`、State 中两个 Shadow 字段及 `dialogue_runtime` 接入，现有 MatchProof/Permit/Executor 测试仍应通过；正式执行链没有依赖 Shadow 输出。
- 验证：红基线三个 bridge 反例失败；候选中对应反例通过，Runtime/Context 回归、107 强上下文、架构 Gate 和真实 Chromium 兼容旅程通过。
