# 目标

- 目标 ID：certify-v20.17-b13-final-tree
- 变更标识：certify-v20.17-b13-final-tree
- 执行上下文：local-change
- 目标类型：certification

对已经正式关闭的 B13b Lifecycle / Runtime Contract Boundary 实际交付树执行只读完整 Quick 认证。交付树包含关闭状态、正式双审查、Release Judge 记录、Kernel 中立只读合同、Lifecycle 权威兼容导出以及全部累计架构棘轮回归；认证期间不得修改产品或治理源码。

## 允许范围

- 允许变更路径：`governance/targets/certify-v20.17-b13-final-tree.md`, `governance/claims/certify-v20.17-b13-final-tree.json`
- 新增抽象记录：ci-not-applicable

## 禁止范围

只读认证；不得修改产品源码、迁移 Target、Claim、Decision、审查记录、质量策略、依赖债务基线或关闭合同。不得用历史 PASS 证据替代当前交付树证据。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b13-final-tree.json`
- 验收 ID：`V20-17-B13-FINAL-TREE-001`

当前交付树必须执行全部 18 个 required Quick Gates；Runtime 不得导入 Lifecycle；Lifecycle 继续拥有语义冻结、迁移和 Graph 权威；Architecture Gate 必须报告 `PASS / RESOLVED`、跨包 SCC 为 0；HTTP 生命周期和真实 Chromium 必须 PASS。

## 基线

只读认证当前已关闭交付树，不重新制造迁移红基线。B13b 的 expected-red 和修复验证继续保留在原证据目录。

## 修复轮次

- 最大轮次：1
- 当前轮次：1
- 失败后：停止认证并创建新的独立 repair/migration target；不得在本认证目标内修改源码。
