# V20.17 B14a：State Schema v2 旧字段隔离

## 问题

State Schema v2 已将 `turn_goal_plan`、`workflow_plan` 和 `pending_clarification` 定义为退休字段。迁移层会把旧 checkpoint 一次性转换为 `frozen_semantic_contract`、`goal_blockers`、`frozen_plan_definition` 与 `plan_run`，新线程不得再读取旧字段作为当前权威。

B13 之后审计发现，`suspend_for_clarification` 虽然优先读取正式语义和 `grounded_execution_plan`，但在这些对象缺失时仍会无条件回退到 `turn_goal_plan` 与 `workflow_plan`。因此，一个标记为 Schema v2 的异常或被污染状态仍可能把伪造旧 Goal 写入 `suspended_goals`。

## 修复

- `pending_clarification` 继续只由 `active_pending_clarification` 读取；该投影在 Schema v2 下始终返回空。
- `turn_goal_plan` 与 `workflow_plan` 仅在 `legacy_fallback_allowed(state)` 为真时进入 Clarification Runtime。
- Schema v2 只接受 `frozen_semantic_contract` 和 `grounded_execution_plan` 作为当前回合语义与计划投影。
- 不改变一次性 legacy checkpoint 迁移、不改变 State Schema 版本，也不新增第二套 Goal/Plan Owner。

## 反例

`test_state_v2_ignores_forged_retired_goal_and_workflow_fields` 构造 Schema v2 状态并注入伪造的旧 Goal/Workflow。修复前，该 Goal 会进入 `suspended_goals`；修复后必须为空。

同时保留以下正向验证：

- 正式 `frozen_semantic_contract + grounded_execution_plan` 仍能生成正确澄清挂起目标；
- Schema v2 中残留的 `pending_clarification` 不会恢复；
- 现有 Schema v1 checkpoint 兼容测试保持通过。

## 边界

本阶段只关闭 Clarification Runtime 的退休字段渗透，不删除 legacy checkpoint 迁移代码，也不宣称完成全部 State/Loop 瘦身。后续阶段可继续审计剩余兼容投影和同回合派生字段。

## 验证结果

2026-07-29 在 B13 最终树上执行 B14a 回归：

- 定向 Clarification / State v2 回归：`9 passed`；
- 对抗运行时反例：`107 passed`；
- Agent 标准 Python 套件：`653 passed, 6 deselected`；
- Business 标准 Python 套件：`28 passed, 2 deselected`；
- Architecture Gate：`PASS`，架构债务 `RESOLVED`，跨包循环仍为 `0`；
- Module Vertical Closure 与 Presentation Release Boundary：`PASS`；
- B14a 声明 `V20-17-B14A-STATE-AUTHORITY-001`：`VERIFIED`。

完整 Quick 结果未标记为收敛。当前工作环境缺少前端 `node_modules` 中的 `vite`、`vitest` 和 `@adobe/css-tools` 入口，且可用 npm 镜像无法恢复锁定依赖，因此 `frontend-dependencies` 为 `FAIL`，其下游前端、覆盖率、Canary 和浏览器 Gate 被按依赖关系跳过。该阻断与本阶段 Python 代码改动无关，但在恢复同一锁文件依赖并补跑完整 Quick 前，本阶段只作为候选阶段包交付，不伪标 `closed` 或 `CONVERGED`。

真实模型认证仍为 `NOT_DECLARED`。

