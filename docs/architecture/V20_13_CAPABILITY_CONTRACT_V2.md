# V20.13 Capability Contract v2

本阶段只建立可被程序验证的能力规划合同，不切换正式 Planner。

```text
Frozen Goal
  → exact capability identity
  → Capability Planning Contract v2
       target
       requires
       produces
       preconditions
       authorization
       completion proof
       idempotency
       resource conflict
  → V20.14 Shadow Planner
```

## 权威边界

- 模型声明用户 Goal 和用户语言业务输入候选。
- Target Resolver、Context、Capability Output、Transaction Authority 和 System 分别提供权威输入。
- Ecommerce module 声明具体业务合同。
- Kernel 只验证合同结构，不知道退款、发票或物流的业务规则。
- Draft 只是事务候选，Receipt 才能证明写操作完成。

## 本阶段垂直范围

- `get_order_logistics`
- `list_invoices`
- `evaluate_refund_eligibility`
- `prepare_refund`
- `prepare_refund_from_eligibility`
- `prepare_invoice`

其他能力继续作为 v1 合同，不在本阶段被强制迁移。


## 新增抽象记录

- 新增项：CapabilityTargetContract、CapabilityInputContract、CapabilityOutputContract、CapabilityPreconditionContract、CapabilityAuthorizationContract、CapabilityCompletionContract、CapabilityIdempotencyContract、CapabilityResourceConflictContract、CapabilityPlanningContract。
- 唯一职责：领域模块声明可规划业务合同；Kernel 只做不可变结构与静态一致性校验。
- 替换或删除项：替换 planner_rule 自然语言承担输入输出合同的职责；Draft 不再承担最终业务完成证明。
- 删除证据：V20.14 Planner 接入前不删除旧字段；本阶段通过 v1 默认值证明未迁移能力不受影响。
- 验证：缺失 planning contract、重复输入、错误完成证明必须失败；物流、退款、发票垂直合同快照必须通过。
