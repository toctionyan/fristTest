# V20.17 B14c Plan Projection Read Boundary

## 问题

B14b 已经在 State Schema v2 checkpoint 入口把 `grounded_execution_plan` 重新派生为
`frozen_plan_definition + plan_run` 的兼容视图，但 Runtime、Lifecycle 和 Observability
仍存在多个直接读取点。只要某条同回合内部路径未经过 checkpoint 迁移，调用参数中的
伪造、过期或未同步投影仍可能：

- 把未完成 Workflow 伪装成 `SUCCEEDED`，绕过最终回答终止校验；
- 把澄清挂到不存在的 Goal；
- 在 Plan Run 已变化后继续读取旧 Step/Goal 状态；
- 让不同消费者各自实现兼容 fallback，重新形成分散权威。

## 唯一读取边界

B14c 在 Kernel 新增 `plan_projection_contract.py`，统一区分三种合法读取对象：

1. **Definition/Run 绑定投影**：正式路径。缓存必须绑定 Definition digest、Plan Run ID、
   Plan Run digest 和自身 projection digest；缓存过期时由正式权威对重新派生。
2. **同回合临时计划**：仅允许 planner 产生、带结构摘要的
   `validated_execution_plan_not_semantic_or_business_fact`。`REJECTED` 临时计划只可用于
   repair 读取，不得通过最终回答校验。
3. **旧 checkpoint 计划**：只有 `legacy_fallback_allowed` 为真时可进入旧兼容路径。

除此之外的 `grounded_execution_plan` 一律不作为读取结果。

## 结构调整

- Kernel 成为投影算法与读取合同的唯一实现；
- Lifecycle `project_grounded_execution_plan` 仅保留兼容导出；
- `project_plan_runtime` 不再二次计算 Goal、Task 和 Workflow 状态；
- Lifecycle、Runtime、Observability 消费者统一调用 `read_plan_projection` 或
  `resolve_plan_projection`；
- 生产源码中只有 Kernel 读取合同和 State Schema 规范化边界可以直接访问
  `grounded_execution_plan`。

## 运行时保证

- 伪造完成态不能让未完成 Plan 通过 final-answer gate；
- 澄清只能绑定正式语义 Goal；
- Plan Run 更新后，旧缓存通过 `plan_run_digest` 自动失效；
- `REJECTED` 临时计划仍可为 repair 暴露 pending Goal，但不能终止；
- 不恢复 `workflow_plan` 为 Schema v2 权威；
- 不新增 Lifecycle ↔ Runtime 反向依赖。

## 新增项

- `agent_core/kernel/plan_projection_contract.py`：Plan 投影生成、缓存绑定校验和读取解析的唯一 Kernel 合同。

## 唯一职责

该合同只负责把 `frozen_plan_definition + plan_run` 投影为只读兼容视图，并区分正式权威对、同回合临时计划和旧 checkpoint fallback。它不拥有 Goal、Plan Definition、Plan Run 或业务状态的写入权威。

## 替换或删除项

- 替换 Lifecycle、Runtime、Observability 中分散的 `grounded_execution_plan` 直接读取；
- 删除 `project_plan_runtime` 对 Goal、Task、Workflow 状态的二次计算；
- 保留 Lifecycle 的兼容导出，但其实现委托 Kernel 唯一投影器；
- 不删除 State Schema v2 的规范化入口，因为它仍承担 checkpoint 迁移边界职责。

## 删除证据

- 生产源码静态反例要求：除 Kernel 读取合同与 State Schema 规范化边界外，不得直接调用 `state.get("grounded_execution_plan")`；
- `test_runtime_source_has_single_grounded_projection_read_boundary` 验证分散读取点已清除；
- `test_projection_cache_is_bound_to_current_plan_run_and_rederived_when_stale` 验证旧缓存不能成为第二权威。

## 验证

- B14c 红基线证明伪造完成态可绕过最终回答校验；
- 修复后同一对抗桥接测试必须通过，并使声明 `V20-17-B14C-PLAN-READ-001` 从 `FAILED` 转为 `VERIFIED`；
- 相关 Plan、Workflow、Clarification、Answer Release 与 State Schema 回归必须通过；
- Architecture Gate 必须保持 `PASS`、依赖债务 `RESOLVED`、跨包循环为 `0`。
