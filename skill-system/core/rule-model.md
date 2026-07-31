# 规则模型

每条规则必须声明 `id`、`level`、`scope`、`rationale`、`verification` 和生命周期信息。

## 等级

### HARD_INVARIANT

违反即拒绝，适合 Hook、Schema、Judge 和 CI 强制。只描述不可破坏的安全、权威、证据和真实性边界，不绑定具体类名或目录。

### STRONG_DEFAULT

已验证的强默认。允许 Architecture Variance，但必须证明硬不变量、迁移、回滚和最终清理。

### REFERENCE_PATTERN

当前可复用的职责模式或实现模式。它们不是永恒对象，不能因名称相同就自动获得权威地位。

### PROJECT_BASELINE

当前项目版本的目录、文件、Owner 和尺寸快照。普通 Repair 必须遵守；Architecture Migration 可以通过批准的 Policy Delta 改变，随后提升为新 Baseline。

### WORKFLOW_DEFAULT

最大轮次、默认 Profile、默认模板等流程值。不同 Target 类型可以覆盖。

### EXAMPLE_ONLY

名称、目录和业务例子，只帮助理解，不得成为验收结构。

## 冲突优先级

用户真实目标 > 安全与硬不变量 > 业务权威边界 > Change Contract > Architecture Decision > 批准的 Migration Delta > 项目 Baseline > 强默认 > 参考模式 > 当前命名 > 示例。

## 验证原则

- 优先验证行为、Owner、数据流和禁止行为。
- 当前项目名称只能由 Baseline 或本次 Decision 约束。
- Variance 只有在 Judge 实际读取并应用时才算有效；仅有文档不构成偏离闭环。
- 临时兼容、Shadow 和 Delta 必须有截止日期和 Promotion/cleanup 证据。
