# Mutation 与失败 Replay 合同

## Mutation Adequacy

每条高风险 invariant 至少声明一个等价错误实现和一个 kill test。Mutation catalog 记录：mutation id、invariant id、operator、kill selector。

必须保留的系统 Mutation 包括：原始消息尾切片、孤立 ToolMessage、禁用历史 Evidence、折叠 thread topology、恢复 wildcard private import、重置订单选择、非累积 release profile 和未脱敏 failure replay。

Mutation Gate 的成功条件是错误实现被拒绝；只证明当前实现通过不算 kill proof。删除、改名或放宽 kill selector 必须使 Gate 失败。

## Runtime Failure Replay

Runtime 异常出口必须生成确定性 replay envelope，包含错误类别、阶段、turn/message/workflow/tool-trace shape 和 fingerprint，但不能包含原始用户文本、token、API key、password、actor identity 或原 thread id。

Replay 的处理闭环是：

```text
runtime failure
→ redacted replay envelope
→ stable invariant/failure class
→ failing regression + mutation
→ unique Owner repair
→ targeted dependency closure
→ full profile certification
```

Replay 只提供复现输入形状，不自动获得业务权限，也不能重放真实写操作。
