# V20.6 目标架构：在当前权威链上持续收敛

> 当前已经生效的生产架构只由 [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md) 定义。本文件只描述未来收敛目标，不能创建或恢复平行运行权威。

## 当前基线

当前主链为：

```text
frozen_semantic_contract
→ goal_records / goal_blockers
→ frozen_plan_definition
→ plan_run
→ Capability Contract v2
→ MatchProof / ExecutionPermit
→ Tool or Transaction Gateway
→ Independent Business Service
→ RuntimeOutcome
→ Presentation
```

`TurnGoalPlan`、旧 `WorkflowPlan` 和 `pending_clarification` 已退出 Schema v2 当前权威；不得因兼容、测试或文档引用重新写回新线程。

## 下一阶段收敛目标

### 1. 依赖方向收敛

```text
app / api
    ↓
lifecycle / use cases
    ↓
kernel contracts / ports
    ↓
runtime policies
    ↓
storage and transaction ports

agent_modules → kernel contracts
composition → all concrete implementations
```

- `composition` 是唯一具体实现安装入口。
- Registry 只查询合同，不导入全部具体模块。
- 已知依赖环只能缩小，不能新增或扩大。

### 2. State 瘦身

将持久权威与本轮临时运行态分离：

- checkpoint 只保存需要恢复的语义、Goal、PlanRun、事务引用和结构化长期记忆；
- ContextBundle、MatchProof、Permit、模型 Trace、Shadow 比较和 Presentation Candidate 进入本轮运行态或独立 Trace Store；
- 派生视图不再重复持久化。

### 3. 主 Loop 简化

LangGraph 保留粗粒度生命周期节点，复杂决策下沉为无状态服务：

- TurnContextCompiler
- ModelInvocationRunner
- SemanticDeclarationValidator
- ToolCallValidator
- LoopDispositionEngine
- TerminalOutcomeFactory

这些服务只拆职责，不形成新的 Goal、Plan 或业务事实权威。

### 4. 真实模型与不可信内容认证

确定性 Runtime 认证与真实模型语义认证必须分开报告。真实模型认证覆盖：

- 相似能力拒绝；
- 缺输入追问；
- 多语言与噪声；
- 长上下文和用户纠正；
- 工具结果忽略与输出伪造；
- RAG、工具和上传内容中的间接 Prompt Injection。

## 不允许的“优化”

- 不拆成多个自由协商 Agent；
- 不新增 GoalPlan v3、Unified Meta Plan 或平行 Judge 权威；
- 不把业务资格、权限或最终写入迁回 Agent；
- 不用历史 PASS 或测试 Stub 冒充当前真实模型认证；
- 不为了消除依赖环而绕过事务、Permit 或 RuntimeOutcome 边界。
