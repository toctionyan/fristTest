# 共享业务 API 与领域完整性合同

## 目标

以下接口是**正常业务接口**，不区分网页、App、运营后台或 Agent。调用渠道不同，业务规则与资源安全边界相同。

```text
调用方 → 认证中间件 → ActorContext → Query / Command Handler
      → ResourceScope → Policy / Transition → Transaction → Audit Event
```

## 可信身份与业务主体

- `ActorContext`：实际发起操作的人，来自 JWT、Session 或受签名的服务间 delegation；包含 `actor_id`、`tenant_id`、角色、权限、request id。
- `SubjectContext`：本次业务为谁办理。普通客户默认是自己；客服代办必须显式传 `subject_user_id`，并具备 `*:create_on_behalf` 权限。
- 业务记录必须保存 `tenant_id`、`subject_user_id`、`created_by_actor_id`，不能把 Actor 与 Subject 混为一个 `user_id`。

## 查询接口

### 订单

```text
GET /orders
GET /orders/{order_id}
POST /orders/query
GET /orders/{order_id}/available-actions
```

`/orders/query` 的过滤必须由服务端执行。支持产品关键字、状态、已付款、金额范围、绝对时间范围；未知过滤字段应失败或被显式拒绝，不能静默忽略。

`available-actions` 用于网页按钮与 Agent 对话提示，可返回诸如 `APPLY_REFUND`、`APPLY_AFTER_SALES`、`CANCEL_ORDER` 及原因。它不是 Agent 专用预检接口。

### 统一业务操作预览

```text
POST /operations/preview
```

请求方传入真实资源、希望执行的领域操作及已收集输入。服务端读取最新状态但不写数据，返回：

```text
ALLOWED / NEEDS_INPUT / NEEDS_REVIEW / BLOCKED
+ snapshot（含 version）
+ blockers
+ required_inputs
+ alternatives
```

该接口是网页、App、人工客服后台和 Agent 共用的正常领域能力，不是 Agent 专用接口。预览只改善澄清与确认；真实命令仍必须在同一事务中重新检查状态、策略、`expected_version` 和幂等键。

### 其他资源

```text
GET /refunds
GET /after-sales/tickets
GET /invoices
GET /complaints
GET /human-handoffs
```

服务端根据 Actor 的 tenant、角色和资源业务主体限制可见范围。operator 只有具备 `business:read_any` 才能在本租户内读取队列；跨 tenant 默认拒绝。

## 申请接口

```text
POST /refunds
POST /after-sales/tickets
POST /invoices
POST /complaints
POST /human-handoffs
```

创建退款/售后等写操作流程：

```text
认证 Actor
→ Resolve Subject
→ 订单/资源归属与 tenant 校验
→ 业务政策判断
→ 同一事务内检查重复申请
→ Idempotency Ledger
→ 创建真实业务单
→ 写 append-only audit event
```

退款、售后政策的最终真相在 Business Service。资格结果可用于对话和页面提示，但正式申请必须再次校验，避免预检与提交之间的状态变化。

## 资源命令接口

```text
POST /refunds/{refund_id}/commands
POST /after-sales/tickets/{ticket_id}/commands
POST /invoices/{invoice_id}/commands
POST /complaints/{complaint_id}/commands
POST /human-handoffs/{handoff_id}/commands
```

统一请求体：

```json
{
  "command": "approve",
  "expected_version": 1,
  "note": "审核说明"
}
```

服务端生成审核人、审核时间、下一个状态和新版本。调用方不可传 `status`、`reviewed_by`、`reviewed_at`、`tenant_id`、`owner_user_id`。

### 退款状态机示例

```text
待审核 --approve--> 已通过 --start_processing--> 处理中 --complete--> 已完成
   |                    |                              └--fail--> 已失败
   ├--reject--> 已拒绝   └--cancel--> 已取消
   └--cancel--> 已取消
```

售后、发票、投诉、人工转接各自有独立 TransitionSpec；它们共享执行机制，不共享业务状态字典。

更新必须同时条件化：

```text
resource_id + tenant_id + expected status + expected_version
```

不匹配时返回冲突或拒绝，不能覆盖他人的更新，也不能让终态倒流。

## 幂等与审计

写操作必须提供 `Idempotency-Key`。唯一范围：

```text
tenant_id + actor_id + command_name + idempotency_key
```

同范围、同请求体重放返回同一结果；同 key、不同请求体返回冲突；不同 tenant 的同 key 完全隔离。

每个变更写入 append-only 审计事件，至少包括：Actor、Subject、tenant、资源类型/ID、命令、前后状态、请求 ID、幂等键、服务端时间。
