# 当前生产架构（唯一当前架构权威）

> 本文件是工程当前有效架构的**唯一当前架构权威**。历史阶段文档只用于解释迁移背景；当历史文档与本文件冲突时，以本文件、当前代码合同和正式治理 Policy 为准。

## 当前主链

```text
HTTP / SSE / UI
→ Application Use Case / LifecycleCommandRunner
→ frozen_semantic_contract
→ goal_records + goal_blockers
→ frozen_plan_definition
→ plan_run
→ Capability Contract v2
→ MatchProof
→ ExecutionPermit
→ Tool Gateway / Transaction Gateway
→ Independent Business Service
→ RuntimeOutcome
→ Presentation
```

这条链只描述当前权威，不表示每个对象都必须由模型生成。模型提出候选语义、参数和工具调用；Runtime 校验候选；Business Service 和事务仓库裁决最终事实与写入。

## 权威对象

### 1. `frozen_semantic_contract`

当前用户回合的冻结语义权威。它保存显式 Goal、`requested_effect`、用户证据、依赖和语义版本。冻结后，下游不得通过工具名、关键词或历史模糊引用重新解释用户目标。

### 2. `goal_records` 与 `goal_blockers`

- `goal_records`：Goal 生命周期和完成证明的权威记录。
- `goal_blockers`：缺输入、歧义、权限前置条件等阻断的权威记录。

阻断解除必须产生结构化证据，不能只根据模型自然语言判断“已经补齐”。

### 3. `frozen_plan_definition`

不可变计划结构权威，描述 Goal、Step、Capability、输入来源和依赖。它不拥有业务事实、授权或执行结果。

### 4. `plan_run`

可变执行进度权威，保存 Step 状态、Attempt 和 Outcome 引用。`grounded_execution_plan` 等兼容对象只能由 `frozen_plan_definition + plan_run` 派生，不能成为新的写入 Owner。

### 5. `MatchProof` 与 `ExecutionPermit`

- `MatchProof` 证明当前 Capability 精确满足 Goal 的开放效果、参数和目标集合。
- `ExecutionPermit` 是短生命周期执行许可，只允许当前已验证调用；它不能替代业务授权、事务 Grant 或 Business Service 校验。

没有精确能力时必须明确拒绝或追问，禁止选择相似能力代替。

### 6. Transaction Authority

写操作由独立事务边界拥有：

```text
TransactionDraft
→ AuthorizationGrant
→ CommitAttempt
→ Receipt / Reconcile
```

模型、Lifecycle、Plan 和 Presentation 都不能直接声明业务提交成功。幂等、版本、权限、资格和最终状态由 Transaction Store 与 Independent Business Service 裁决。

### 7. `RuntimeOutcome` 与 Presentation

`RuntimeOutcome` 是执行结果向展示层开放的唯一运行投影；Presentation 只能根据它和已验证引用生成客户可见内容。工具自由文本、模型猜测和内部 Plan 状态不能直接变成“成功”“已退款”“已取消”等业务结论。

## 模块边界

| 责任 | 当前唯一 Owner |
|---|---|
| 会话生命周期与图编排 | `agent_core/lifecycle/` |
| 能力匹配、Permit、执行分类与 RuntimeOutcome | `agent_core/runtime/` |
| Draft、Grant、Attempt、Receipt、Reconcile | `agent_core/transaction/` |
| 展示合同、发布校验和 Renderer | `agent_core/presentation/` |
| Store 协议 | `agent_core/storage/` |
| Store 实现 | `agent_core/persistence/` |
| 具体模块安装 | `agent_core/composition/` |
| 领域能力闭环 | `agent_modules/<module>/` |
| 业务事实、资格、权限和最终写入 | Independent Business Service |

当前依赖债务由 `governance/architecture-policy.json` 的 Dependency Debt Ratchet 管理。已知依赖环只允许缩小，任何新增或扩大必须失败；`PASS_WITH_DEBT` 不等于架构已无债务。

## State Schema v2

新线程从第一轮使用 `state_schema_version=2`。当前持久权威包括：

- `frozen_semantic_contract`
- `goal_records`
- `goal_blockers`
- `frozen_plan_definition`
- `plan_run`

以下旧字段不再由新线程产生或作为当前权威：

- `turn_goal_plan`
- `workflow_plan`
- `pending_clarification`

旧 checkpoint 只能依据显式结构化证据做一次性迁移；无法无损迁移时返回 `LEGACY_STATE_REQUIRES_RESTART`，禁止 Runtime 猜测。

## 明确非目标

当前架构不宣称具备：

- 多 Agent 自由协商；
- 跨进程后台 Durable Workflow；
- 依靠 Checkpointer 自动获得外部任务调度；
- 用 RAG 内容或工具自由文本控制系统指令；
- 用测试 Stub 代替真实模型语义认证。

## 历史记录

- `TURN_GOAL_PLAN_RECORD.md`：SUPERSEDED，仅用于历史迁移解释。
- `WORKFLOW_PLAN_RECORD.md`：SUPERSEDED，仅用于历史迁移解释。
- `V20_14_PRETOOL_GROUNDED_PLANNER_SHADOW.md`：Shadow 阶段历史。
- `V20_15_FROZEN_PLAN_DEFINITION_PLAN_RUN.md`：当前计划定义/运行拆分来源。
- `V20_16_STATE_SCHEMA_V2_LEGACY_CUTOVER.md`：当前 State Schema v2 迁移来源。

## 变更规则

新增普通业务能力原则上只修改对应 AgentModule、Business Service 端口、composition 安装和测试。若必须修改本文件中的语义、计划、Permit、事务或展示权威，必须先建立独立 ADR/Change Target，并证明现有 Owner 无法承担该职责；不得新增第三套 Goal 或 Plan 权威。

## 新增抽象记录

- **新增项**：`docs/architecture/CURRENT_ARCHITECTURE.md`，作为 State Schema v2 当前生产架构的唯一入口。
- **唯一职责**：只声明当前有效的语义、Goal、Plan、Capability、事务和展示权威链；不实现运行时代码，也不成为新的业务事实 Owner。
- **替换或删除项**：替换 `TARGET_ARCHITECTURE.md` 中把 `TurnGoalPlan`、`WorkflowPlan` 误当当前权威的旧描述；`TURN_GOAL_PLAN_RECORD.md` 与 `WORKFLOW_PLAN_RECORD.md` 显式标记为 `SUPERSEDED`，仅保留迁移历史。
- **删除证据**：新线程的 State Schema v2 不再生成 `turn_goal_plan`、`workflow_plan`、`pending_clarification`；架构合同测试验证旧记录不再被任何当前文档声明为权威。
- **验证**：`test_current_architecture_truth.py` 验证唯一当前入口与旧记录退役；`test_dependency_lock_contract.py` 验证两套 `uv.lock` 当前有效并进入 clean-release 源选择；完整 Quick 回归验证运行时行为不变。
