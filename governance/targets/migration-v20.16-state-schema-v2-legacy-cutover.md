# 目标

- 目标 ID：migration-v20.16-state-schema-v2-legacy-cutover
- 变更标识：portable-migration-v20.16-state-schema-v2-legacy-cutover
- 执行上下文：local-change
- 目标类型：migration

建立 State Schema v2 并完成旧主链切换：新线程不再生成 `turn_goal_plan/workflow_plan/pending_clarification`；旧 checkpoint 只通过显式结构化证据做一次性迁移，歧义状态明确要求新建会话；Schema v2 不允许旧模糊语义或能力路径恢复为正式权威。

## 允许范围

- 允许变更路径：services/agent-service/app/services/checkpoint_hydrator.py, services/agent-service/app/services/lifecycle_command_runner.py, services/agent-service/app/use_cases/conversation_turn.py, services/agent-service/src/agent_core/context/**, services/agent-service/src/agent_core/lifecycle/**, services/agent-service/src/agent_core/observability/failure_replay.py, services/agent-service/src/agent_core/runtime/answer_release_alignment.py, services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py, services/agent-service/src/agent_modules/ecommerce/shared/context.py, services/agent-service/tests/runtime/**, services/agent-service/tests/context/**, services/agent-service/tests/support/**, docs/architecture/**
- 新增抽象记录：docs/architecture/V20_16_STATE_SCHEMA_V2_LEGACY_CUTOVER.md
- `services/agent-service/app/services/checkpoint_hydrator.py`
- `services/agent-service/app/services/lifecycle_command_runner.py`
- `services/agent-service/app/use_cases/conversation_turn.py`
- `services/agent-service/src/agent_core/context/**`
- `services/agent-service/src/agent_core/lifecycle/**`
- `services/agent-service/src/agent_core/observability/failure_replay.py`
- `services/agent-service/src/agent_core/runtime/answer_release_alignment.py`
- `services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py`
- `services/agent-service/src/agent_modules/ecommerce/shared/context.py`
- `services/agent-service/tests/runtime/**`
- `services/agent-service/tests/context/**`
- `services/agent-service/tests/support/**`
- `docs/architecture/**`

## 禁止范围

不得修改 Skill、Quality Policy、Judge、Business Service、事务状态机、Capability Contract v2 业务定义或 Presentation。不得把旧字段恢复为 Schema v2 的正式读写 Owner。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.16-state-schema-v2-legacy-cutover.json`
- 验收 ID：STATE-SCHEMA-V2-NEW-THREAD-001, LEGACY-CHECKPOINT-MIGRATION-001, AMBIGUOUS-LEGACY-RESTART-001, LEGACY-AUTHORITY-CUTOVER-001
- 新线程不得生成三个退役字段。
- 可迁移旧 checkpoint 必须一次性生成正式合同、GoalRecord、GoalBlocker 和 tombstone。
- 歧义活动旧状态必须返回 `LEGACY_STATE_REQUIRES_RESTART`。
- Schema v2 不读取旧 GoalPlan 作为当前语义，不进入旧模糊能力发现。
- V20.12–V20.15、强上下文、多意图、事务和浏览器代表链不得回归。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 本阶段不得提前执行真实模型、正式前端、PostgreSQL 最终认证，也不得提升 Architecture Baseline。

## 基线

在 V20.15.0 源码上只加入本阶段合同、Decision、Claims 和同一反例，记录 Schema v2/Legacy Cutover 尚不存在的真实红基线。
