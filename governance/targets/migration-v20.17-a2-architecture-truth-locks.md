# 目标

- 目标 ID：migration-v20.17-a2-architecture-truth-locks
- 变更标识：portable-migration-v20.17-a2-architecture-truth-locks
- 执行上下文：local-change
- 目标类型：migration

统一 State Schema v2 当前架构文档权威，并恢复与当前 pyproject 完全匹配且可进入 clean release 的 Python 锁文件。

## 允许范围

- 允许变更路径：`docs/architecture/CURRENT_ARCHITECTURE.md`, `docs/architecture/TARGET_ARCHITECTURE.md`, `docs/architecture/TURN_GOAL_PLAN_RECORD.md`, `docs/architecture/WORKFLOW_PLAN_RECORD.md`, `docs/architecture/overview.md`, `docs/operations/CONFIGURATION.md`, `services/agent-service/uv.lock`, `services/business-service/uv.lock`, `services/agent-service/tests/architecture/test_current_architecture_truth.py`, `services/agent-service/tests/architecture/test_dependency_lock_contract.py`
- 新增抽象记录：docs/architecture/CURRENT_ARCHITECTURE.md

## 禁止范围

不得恢复 TurnGoalPlan、WorkflowPlan 或 pending_clarification 为 Schema v2 当前权威；不得修改 Agent Runtime、Business Service、Skill、Judge 或 Quality Policy。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-a2-architecture-truth-locks.json`
- 验收 ID：`ARCHITECTURE-CURRENT-OWNER-001`, `PYTHON-LOCK-REPRODUCIBILITY-001`

当前架构入口唯一；旧记录显式 SUPERSEDED；两个 Python 锁文件通过 uv lock --check 并被 clean release 复制。

## 基线

本红基线加入同一文档与锁文件反例测试后，CURRENT_ARCHITECTURE 缺失、旧记录未退役且两个 uv.lock 缺失，Quick 必须为红。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
