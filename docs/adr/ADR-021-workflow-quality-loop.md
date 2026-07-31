# ADR-021：WorkflowPlan 与发布级质量 Loop

- 状态：Accepted
- 决策：`WorkflowPlan` 只替换多意图请求中原本隐式的连续工具编排；它留在 `lifecycle/`，不创建 Planner 服务、Facade 或新的业务状态库。`current_turn_plan` 仍是模型候选调用的审计证据，`workflow_plan` 是当前回合的运行时编排状态。
- 编排元数据：Effect 可保留 `target_cardinality_hint`，仅用于在终态模型调用覆盖原始 `tool_calls` 后继续区分单目标与多目标编排；它不是资源事实、目标解析或授权。真实集合范围仍只由 MatchProof、VisibleResultRef 与 CapabilityGate 决定。
- 审计：跨回合历史只进入已有、上限为 80 的 `conversation_event_log`；删除 `workflow_history`，避免重复且无界的 checkpoint 状态。
- 事务未知：`SUBMISSION_UNKNOWN` 必须标记为只读对账状态，复用原幂等键；不被归类为环境故障或普通重试。
- 验证：84 条 schema-v2 确定性生命周期回归（逐 user turn）、集成 HTTP/SSE smoke 和预发布 12 原型只读模型 smoke 是该边界的发布证据。
- Loop 范围：local baseline 保存排除运行态后的源码快照；后续只允许 target 的 `允许变更路径` 变化。新增生产 Python 文件需要一个完整的新抽象替换记录，否则停止并重建 target/baseline，而不是扩张同一轮修复范围。
