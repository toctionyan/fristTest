# V20.17 B38：强上下文引用证明与多任务全局覆盖

## 状态

本记录定义 B38 迁移边界。它扩展现有 Context、FrozenSemanticContract、CapabilityGate、Pretool Planner 和 Workflow Runtime，不创建第二套持久语义、业务事实、计划执行或事务权威。

## 新增项

- `agent_core.context.reference_resolution`：有限 `ReferenceExpression` 与确定性 `ReferentResolutionProof`，把“上一次、上上次、第 N 个”等语言关系转为可验证历史 ResultRef/成员引用。
- `agent_core.lifecycle.goal_granularity`：在 Capability 发现前审查 Goal 是否为用户可独立验收的业务结果，区分 `exact / under_split / over_split / mixed / clarify / indeterminate`。
- `agent_core.lifecycle.condition_expression`：有限、无脚本的 ConditionExpression AST，统一 Goal 输出、目标事实、输入和字面值条件。
- `agent_core.lifecycle.goal_capability_coverage`：对当前全部 Goal 生成只读的精确 Capability Coverage，识别一个 Tool 覆盖多个 Goal、一个 Goal 多步骤和共享前置能力。
- Workflow Runtime 的逐 Goal 完成证明：一个 Step 可绑定多个 Goal，但每个 Goal 单独记录 effect role、cardinality eligibility 和 completion eligibility。

## 唯一职责

- 语义模型只提出 `ReferenceExpression`、Goal、依赖和条件候选。
- Context Runtime 只在用户已见且仍有效的 ResultRef 上生成解析证明；它不自动选择、切换或替代目标。
- FrozenSemanticContract 继续是当前轮唯一正式语义权威，并冻结 `resolved_reference`、granularity proof 和规范化条件。
- Capability Contract/Registry 继续定义能力输入、输出、前置条件、授权和完成证明。
- Global Coverage 仅回答“哪些精确能力组合能够覆盖哪些 Goal”，不签发 ExecutionPermit、不 Dispatch、不写业务事实。
- CapabilityGate 继续是 Tool 调用前的唯一执行许可边界，并强制 Tool target 与冻结引用证明一致。
- Business Service 继续裁决业务事实和业务规则；Draft/Grant/Attempt/Receipt 继续裁决写操作。

## 数据流

```text
ContextBundle.visible_result_refs
→ Model ReferenceExpression
→ Runtime ReferentResolutionProof
→ UNIQUE resolved_reference
→ FrozenSemanticContract
→ Goal completeness + granularity proof
→ exact Capability Surface
→ global Goal-Capability Coverage
→ PretoolExecutionPolicy frontier
→ MatchProof + ExecutionPermit
→ Tool/Business Service
→ per-Goal GoalOutputRef / completion proof
```

非 `UNIQUE` 引用只能形成 Blocker 或失败结果，不能回退到最近同类集合。条件本阶段完成结构化、依赖验证和冻结；它不替代未来的 durable workflow/外部事件条件执行引擎。

## 替换或删除项

- 替换“模型直接选择一个合法 ResultRef 即可执行”的隐含路径：模型只提出关系，Runtime 生成唯一解析证明。
- 删除 `context_binding` 可以覆盖或改变语义目标的权威含义；该字段只保留为兼容/审计注解。
- 替换开放 `condition: {}` 的正式语义合同，统一为有限 ConditionExpression AST；旧简单条件仅在规范化入口一次性迁移。
- 删除 Goal 与 Tool 一一对应的执行假设；Workflow Step 使用 `goal_ids[]`，每个 Goal 必须独立证明。
- 替换“逐 Goal 路径等于全局执行方案”的假设：逐 Goal 候选仍用于局部拓扑，Global Coverage 是唯一跨 Goal 覆盖投影。
- 不新增关键词路由、相似能力回退、万能查询 DSL或第二个 Planner 执行权威。

## 删除证据

- `ReferentResolutionProof` 必须通过版本、状态、`auto_substitution_used=false` 与 `proof_digest` 完整性校验；冻结后的表达式、ResultRef、成员和摘要必须与证明一致。
- 带 `object_type` 的历史引用必须由权威成员类型元数据正向证明；旧结果缺少类型元数据时 fail closed，不得视为兼容。
- CapabilityGate 对存在 `resolved_reference` 的 Goal 强制检查目标 handle/集合 handle；不一致返回 `SEMANTIC_REFERENCE_BINDING_MISMATCH`。
- 多 Goal 单次调用若引用不同冻结目标，必须拒绝，不允许用一个更宽集合合并。
- `context_binding_authority` 明确投影为 `compatibility_annotation_only`。
- Granularity Review 在 Capability Surface 生成前运行；`over_split / under_split / mixed / clarify / indeterminate` 不得进入正式执行。
- Global Coverage 标记 `must_not_dispatch=true`、`creates_permit=false`、`mutates_semantics=false`。
- 当前仅把无副作用 Capability 投影为“一次调用覆盖多个 Goal”的共享候选；组合写操作在缺少显式原子业务 Strategy 合同前不得合并 Dispatch。
- 共享前置能力只能来自各 Goal 已闭合的精确首选路径，不得扫描全 Registry 后仅凭相同输出类型拼接无关生产者。
- AgentStep 的 `per_goal` 验证分别保存效果角色、目标基数和完成资格；一个 Step 成功不能自动完成全部绑定 Goal。
- Provider Tool Schema 可压缩重复判别联合，但 Registry 的规范 Schema 和 CapabilityGate 严格验证保持不变。

## 验证

- 历史上一轮、上上轮、显式轮次和原展示顺序成员解析反例。
- 类型冲突、找不到目标和禁止同类回退反例。
- Frozen resolved reference 与 Tool target 不一致拒绝测试。
- Goal over-split、条件依赖缺失和 legacy 条件规范化测试。
- 一个 Capability 覆盖两个 Goal、一个 Step 两个 Goal 且逐 Goal 证明测试。
- 既有 strong-context、semantic planning、Pretool Policy、Workflow Runtime、GoalOutputRef 与 CapabilityGate 回归。
- Provider projection 体积与 Runtime canonical schema 严格性回归。
- `compileall`、Diff scope、架构收敛和产品 Quick；环境缺失必须单独标记，不得伪造 PASS。

## 明确不处理

- 不实现跨进程、跨天持久工作流、定时任务、外部事件触发、Saga 补偿或大型并行调度。
- 不把通用 Agent Core 变成业务规则引擎；特殊业务系统逻辑仍由模块 Capability、Business Port 和 Business Service 封装。
- 不保证真实模型对所有自然语言表达零歧义；非唯一引用和非确定粒度必须阻断或追问。
- 不在本阶段删除所有 legacy ContextBinding 字段；它们已失去语义裁决权，后续在兼容调用归零后删除。
- 不把 Global Coverage 提升为执行权威；正式执行仍由 PretoolExecutionPolicy、MatchProof 和 ExecutionPermit 裁决。
- 不在现有 Capability Contract 未声明“每个 completion effect 对应哪个独立输出证明”时合并组合写能力；该合同演进必须另立受治理迁移。

## Stage 4：全局 Coverage 执行边界闭合

本阶段继续沿用同一个 `goal_capability_coverage` 只读 Owner，不新增第二个持久 Planner 或 SharedOutput Registry。

- Coverage v2 只在**当前冻结 Goal 集合**上做有界组合覆盖；它可以把多个安全共享读能力与既有逐 Goal `preferred_path` 组合成候选，但仍标记 `must_not_dispatch=true`、`creates_permit=false`。
- 多 Goal 单次 Tool 候选必须来自 Capability Contract v2，且 `completion.mode=tool_output`；当前合同只有一个权威主完成输出 `completion.output_name`，因此该同一主输出只能作为多个精确 completion effect 的共同证据。B38 **不新增**“每个 completion effect 独立输出映射”字段；若未来需要不同 effect 使用不同输出证明，必须另立受治理合同迁移。
- `PretoolExecutionPolicy.shared_frontier_bindings` 只投影当前真正同时位于执行前沿、且已被 Global Coverage 证明的 Goal 子集；Coverage digest 或 Registry 版本失效时只关闭共享多 Goal 绑定，不回退扩大为完整 Capability Surface。
- `CapabilityGate` 对 `goal_ids` 数量大于 1 的单次调用要求存在 Tool 名与 Goal 集合完全一致的 `shared_frontier_binding`，并要求每个 Goal 均有完成证明声明；否则 fail closed。冻结历史引用仍需指向同一规范目标作用域。
- 多 Goal Tool 成功后，`GoalOutputRef` 继续是唯一持久复用证据 Owner。只有 Capability Contract 指定的主完成输出可以跨多个绑定 Goal 形成 completion proof；其它 produced output 仍可进入 Ledger，但不能因同一次 Tool 调用而自动成为每个 Goal 的完成证据。
- 组合写能力继续禁止共享 Dispatch；没有显式原子业务 Strategy、授权、幂等、冲突和 Receipt 合同前，不能用“调用更少”作为合并写操作的理由。
- Coverage 候选排序只在完整、安全、证据闭合之后比较 dispatch 数；减少调用次数永远晚于 Goal 完整性、副作用、授权和完成证明。
