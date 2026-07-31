# 新抽象替换记录：系统级运行闭环

## Conversation Protocol Compiler

- 新增项：`agent_core.context.conversation_protocol`。
- 唯一职责：把 checkpoint 中的 LangChain 消息编译成 provider 可接受的完整 Exchange，并在模型调用前验证 tool-call/tool-result 原子协议。
- 替换或删除项：替换 `dialogue_runtime._loop_messages` 与 `context_bundle.recent_conversation_window` 各自按固定条数直接切原始消息的双实现。
- 为什么不能并入现有 Owner：`ContextBundle` 拥有上下文投影，`model_calls` 拥有调用和预算；跨 provider 的消息协议编译属于二者之间独立且唯一的 context Owner。
- 迁移顺序：先加入边界/Mutation 反例，再让两个 consumer 共用 compiler，最后删除所有原始尾切片。
- 删除证据：生产代码不再出现 `messages[-12:]` 或等价的未分组消息窗口。
- 验证：1、12、13、50、100 轮和多 tool-call Exchange 均通过协议验证；naive tail mutation 必须失败。

## Goal Evidence Satisfaction

- 新增项：WorkflowGoal 的显式 Evidence satisfaction proof。
- 唯一职责：证明当前 query/consult goal 是否已被 active、scope-bound、customer-visible 的历史证据满足。
- 替换或删除项：替换“当前回合没有 observation Step 就一定未覆盖”的隐含规则；不替换 Business Service、CapabilityGate 或 Answer Release verifier。
- 为什么不能并入现有 Owner：它是 Workflow goal coverage 的组成部分，直接并入既有 `workflow_runtime`，不创建平行 workflow。
- 迁移顺序：先保留错误拒绝反例，再加入 proof，最后让 coverage 聚合读取该 proof。
- 删除证据：历史 Evidence 满足 query 时不再产生 `goal_coverage_incomplete`；action goal 仍不能被历史证据关闭。
- 验证：有效、过期、跨 scope、不可见和 action 五类反例。

## Failure Replay Envelope

- 新增项：`agent_core.observability.failure_replay`。
- 唯一职责：把运行失败转成确定性、脱敏、可校验的 replay envelope 与 fingerprint。
- 替换或删除项：替换 `tool_error` 仅保存 provider 原始字符串、无法稳定回归的做法；不新增第二套 Trace Store。
- 为什么不能并入现有 Owner：复用 observability 的 redaction Owner，并把 envelope 附着到既有 `tool_error`/Trace，不建立新的持久化主链。
- 迁移顺序：先定义 envelope 和 secret mutation，再接入 Agent Loop 异常出口，最后由治理 mutation catalog 绑定 regression selector。
- 删除证据：失败响应不再只有不可重放的异常文本。
- 验证：同一归一化失败 fingerprint 稳定，API key/token/password/raw actor/thread 均不出现。

## Frontend Order State Reducer

- 新增项：订单列表、选择、详情加载的显式 reducer 状态转换。
- 唯一职责：让初始化、刷新、用户选择和详情返回有稳定状态语义，避免 callback identity 触发 App boot 重跑。
- 替换或删除项：替换 `useOrders` 中五个相互独立且会形成闭包依赖的 `useState`；不新增第二套服务器状态缓存。
- 为什么不能并入现有 Owner：实现仍位于既有 `useOrders` Owner，仅把内部更新收敛为 reducer。
- 迁移顺序：先加入完整 Hook/App 反例，再迁移 state，最后增加线程导航、移动布局与可访问性断言。
- 删除证据：选择订单不再改变 `loadOrders` identity，也不会再次执行 `keepSelection:false` boot。
- 验证：Hook 状态转换、完整 App 用户旅程、移动 CSS 合同和 accessibility DOM 断言。
