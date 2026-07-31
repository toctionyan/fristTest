# V20.17 B13：Lifecycle / Runtime 中立合同边界

## 新增项

- `agent_core.kernel.loop_contract`：只保存 Agent Loop 的闭合默认值。
- `agent_core.kernel.semantic_contract`：只保存冻结语义合同版本、摘要完整性和只读 Goal 投影。
- `agent_core.kernel.state_schema_contract`：只保存 Schema 版本常量与只读 legacy fallback 判断。

## 唯一职责

- Kernel 仅拥有无业务写入能力的中立合同、摘要校验和只读投影。
- Lifecycle 继续唯一拥有语义合同创建/冻结、Goal/Blocker 写入、State Schema 迁移和 Graph 路由。
- Runtime 继续唯一拥有模型/工具执行、能力证明、Permit 和 RuntimeOutcome。

## 替换或删除项

- 删除 Runtime 对 `agent_core.lifecycle.*` 的全部导入。
- Lifecycle 原公共名称改为从 Kernel 兼容导入，不保留第二套实现。
- 累计架构测试从“必须保持 REDUCED”改为单调棘轮：允许最终 RESOLVED，禁止任何已移出成员重新进入循环。

## 删除证据

- AST 反例扫描整个 Runtime，发现任何 Lifecycle 导入即失败。
- Kernel 与 Lifecycle 的函数/常量对象或输出必须等价。
- 摘要被篡改的 FrozenSemanticContract 仍必须返回空 Goal 投影。
- Architecture Gate 必须报告 `current_cycles=[]`、`current_member_count=0` 和 `RESOLVED`，不得修改依赖债务基线。

## 验证

- B13 架构反例与 B1-B12 累计架构回归。
- 语义合同、State Schema、Capability Match、Agent Loop 和事务测试。
- 完整 Quick 18 Gates、HTTP 生命周期与真实 Chromium。

## 未解决债务

本阶段不进行 State/Loop 瘦身，不声明 DeepSeek 等真实模型认证，也不完成 RAG/工具结果 Prompt Injection 与长期记忆认证。
