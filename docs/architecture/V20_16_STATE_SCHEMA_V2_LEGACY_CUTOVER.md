# V20.16 State Schema v2 与 Legacy Cutover

## 目标

V20.16 只处理持久状态权威和旧检查点退出，不改变用户语义、Capability Contract、事务权威或 Presentation：

1. 新线程从第一轮开始使用 `state_schema_version=2`；
2. `frozen_semantic_contract`、`goal_records`、`goal_blockers`、`frozen_plan_definition`、`plan_run` 是新状态权威；
3. `turn_goal_plan`、`workflow_plan`、`pending_clarification` 不再由新线程产生或持久化；
4. Schema v1 检查点只能通过显式结构化证据做一次性迁移；
5. 无法无损迁移的活动旧状态返回 `LEGACY_STATE_REQUIRES_RESTART`，禁止 Runtime 猜测；
6. 旧模糊能力发现只能存在于迁移前 Schema v1 边界，Schema v2 不读取旧 Goal 投影。

## 新线程

`prepare_agent_loop_turn_node` 在空状态上直接产生 Schema v2，并且不会输出三个退役字段。当前轮的执行兼容视图 `grounded_execution_plan` 仍可由 `FrozenPlanDefinition + PlanRun` 派生，但不是持久语义 Owner。

## 旧检查点迁移

`CheckpointHydrator` 在图执行前读取旧状态并调用 `migrate_checkpoint_state`；迁移补丁通过 `lifecycle_command_runner.py` 这一唯一 checkpoint 写入 Owner 持久化：

- 显式 `requested_effect` 的活动旧 Goal 可冻结为正式语义合同；
- 旧 `pending_clarification.suspended_goals` 迁移成独立 `GoalBlocker`；
- 活动旧 Goal 迁移成 `GoalRecord`；
- 三个退役字段以 `null` tombstone 一次性写回 checkpoint；
- 迁移报告写入 `state_migration`，兼容使用写入 `legacy_compatibility_metrics`。

旧状态缺少开放业务效果、Goal 身份或可验证挂起关系时，不生成猜测结果，必须新建会话。

## 单一写入者边界

- `CheckpointHydrator` 只负责读取、确定性迁移和生成受限迁移补丁。
- `graph.update_state` 仍只允许出现在 `app/services/lifecycle_command_runner.py`。
- 持久化补丁只包含 Schema 版本、迁移报告、正式语义/Goal/Blocker、计划定义/运行以及退役字段 tombstone，不覆盖消息、线程身份或无关业务状态。
- 新线程没有旧字段痕迹时直接识别为 Schema v2，不计入 legacy migration 指标。

## 退出边界

本阶段保留 Schema v1 读取代码，只用于一次性迁移和隔离测试。新线程不得进入旧能力模糊发现，不得恢复旧字段写入。V20.17 在正式 LangGraph/PostgreSQL 环境验证迁移、使用量和恢复后，删除 Schema v1 fallback。
## 新增抽象记录

- 新增项：`agent_core.lifecycle.state_schema`，提供 Schema 版本识别、一次性旧 checkpoint 迁移、退役字段 tombstone 和 `LEGACY_STATE_REQUIRES_RESTART` 失败边界。
- 唯一职责：只迁移已有的显式结构化状态，不重新解释用户语言；`CheckpointHydrator` 负责在图执行前持久化迁移结果，运行节点只消费 Schema v2 权威对象。
- 替换或删除项：替换 `turn_goal_plan`、`workflow_plan`、`pending_clarification` 作为跨轮持久权威的职责；新线程不再产生这些字段，旧字段仅在 Schema v1 迁移入口被读取一次。
- 删除证据：V20.17 在正式 LangGraph/PostgreSQL 环境中完成活动 checkpoint 迁移审计，连续 14 天 `legacy_fallback_allowed=false` 且旧字段非空计数为 0 后，删除 Schema v1 fallback、旧模糊能力发现和三个 tombstone 兼容写入。
- 验证：签名红基线四个 V20.16 bridge 反例均失败；候选中同一反例通过，并通过 390 项 Runtime/Context/Transaction/Presentation 回归、107 项强上下文目录与真实 Chromium 代表链。

