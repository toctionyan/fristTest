---
name: architecture-options
description: 架构设计、重构或现有规则可能阻碍更优方案时使用。至少比较保守、演进与重构方案，区分硬不变量、强默认和参考模式。
---

# Architecture Options

- 先从用户真实目标推导最简单正确方案，不先套现有对象。
- 比较保守、演进、重构方案的正确性、复杂度、迁移、回滚和验证成本。
- 明确删除内容和不采用更简单方案的原因。
- 需要偏离强默认时生成 Architecture Variance Record。
- 不把类名、目录名、节点数和示例当作硬约束。
