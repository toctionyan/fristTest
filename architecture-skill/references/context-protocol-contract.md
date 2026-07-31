# 对话上下文协议合同

Checkpoint 消息是 Runtime 状态，不是 provider wire messages。所有模型调用必须经过唯一 Conversation Protocol Compiler。

## 原子 Exchange

一个 Exchange 从 HumanMessage 开始，到下一个 HumanMessage 之前结束。AIMessage 中每个 tool call 与其全部 ToolMessage 响应构成不可拆分的原子段。

Compiler 必须：

- 按完整 Exchange 选择窗口，不能直接执行原始消息尾切片；
- 同时执行消息数和字符/token 预算；
- 最新 Exchange 超预算时完整保留并显式报告 overflow；
- 删除或隔离 checkpoint 中已损坏的孤立段，不能合成工具结果；
- provider 调用前验证不存在 orphan、unexpected 或 incomplete tool result；
- ContextBundle 与实际模型 payload 使用同一 compiler。

## Evidence 满足

当前 query/consult goal 可以使用历史 Evidence，但必须同时满足：

- 当前 tenant/user/thread scope；
- Ledger active；
- 已经过 customer-visible release；
- terminal call 显式绑定当前 goal 和原 evidence handle；
- Answer Release Alignment 仍需独立通过。

历史 Evidence 永远不能满足 action goal，不能替代资格预检、授权、Draft 或 Business Service 写入。

## 边界验证

Generated sequence 至少覆盖 1、12、13、50、100 轮、多 tool call、窗口正好切在 AI/tool 边界、缺少一个 ToolMessage、孤立 ToolMessage 和损坏旧 checkpoint。naive raw tail mutation 必须被 Gate 拒绝。
