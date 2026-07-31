# 系统运行闭环合同

## 完整性来源

项目级认证不能从一次变更的 target 或 claims 反推完整需求。唯一允许的顺序是：

```text
Product Capability Inventory
→ Requirement invariant / failure class / required strategies
→ cumulative certification profile
→ Claim / Gate / executable evidence
```

Inventory 中每个有 Owner 的产品能力必须恰好被至少一条 requirement 覆盖；catalog 不得引用不存在的能力。任何手工增加或删除 capability、API、module capability、lifecycle state、UI journey、provider protocol 或 release surface，都必须同步 Inventory，遗漏时 Gate 失败。

## 累积认证

认证层级严格累积：

```text
project-quick ⊂ project-integration ⊂ project-product ⊂ project-release
```

高层 profile 必须包含所有低层 requirement。高层 Gate 实际执行低层 Gate 不是 requirement coverage 的替代品；claims 仍必须覆盖累积后的全集。

## 高风险证明策略

每条 P0/P1 requirement 至少声明：

- `invariant`：不可被破坏的系统性质；
- `failure_class`：它保护的问题类别；
- `inventory_ids`：对应的产品能力；
- `required_strategies`：至少包含 counterexample 与 mutation；按风险增加 generated-sequence、integration、browser 或 real-model。

一个固定 pytest 样例不能单独证明状态空间、真实拓扑、浏览器产品旅程或模型语义。Gate 必须按 requirement 的策略组合提供证据。

## 完成状态

质量状态不得只返回笼统的 VERIFIED。交付报告必须区分：

- `CODE_CLOSED`：反例与单元/合同回归通过；
- `INTEGRATION_CLOSED`：双服务、数据库和真实拓扑通过；
- `PRODUCT_CLOSED`：真实浏览器关键旅程通过；
- `REAL_MODEL_CLOSED`：真实 provider 完整 lifecycle canary 通过；
- `RELEASE_CERTIFIED`：累积 profile、不可变来源和 clean artifact 全部通过。

低层状态不能冒充高层状态，环境缺失必须阻断对应高层认证。

`REAL_MODEL_CLOSED` 至少要求真实 provider 完成多轮依赖查询，穿过目标声明、CapabilityGate、Business Observation、同轮已许可 ResultRef 输出/成员链和 Answer Release；safe notice、目标声明原型或 deterministic model 都不能计为成功。
