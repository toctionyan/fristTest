# 目标

- 目标 ID：repair-v20.17-b12c-review-evidence-recovery
- 变更标识：portable-repair-v20.17-b12c-review-evidence-recovery
- 执行上下文：local-change
- 目标类型：repair

恢复 B12b 已关闭事务边界变更的可验证审查证据：保存原关闭合同，明确记录原两份审查文件丢失及其历史哈希，生成新的独立范围审查与反向审查，并重新认证当前产品树。不得修改任何产品实现。

## 允许范围

- 允许变更路径：`services/agent-service/tests/architecture/test_b12_review_evidence_recovery.py`, `docs/architecture/evidence-recovery/migration-v20.17-b12b-transaction-runtime-boundary/prior-closed-change.json`, `docs/architecture/evidence-recovery/migration-v20.17-b12b-transaction-runtime-boundary/recovery.json`, `docs/architecture/evidence-recovery/migration-v20.17-b12b-transaction-runtime-boundary/scope-planner.md`, `docs/architecture/evidence-recovery/migration-v20.17-b12b-transaction-runtime-boundary/adversarial-reviewer.md`, `docs/architecture/V20_17_B12C_REVIEW_EVIDENCE_RECOVERY.md`
- 新增抽象记录：docs/architecture/V20_17_B12C_REVIEW_EVIDENCE_RECOVERY.md

## 禁止范围

不得修改 Agent、Runtime、Lifecycle、Transaction、Business Service、前端、质量策略、依赖债务基线、B12b Target/Claim/Decision 或 B12b 产品验证证据；不得伪造原审查文件内容或原哈希。恢复记录必须明确区分“历史缺失证据”和“新生成替代审查”。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b12c-review-evidence-recovery.json`
- 验收 ID：`B12C-REVIEW-EVIDENCE-RECOVERY-001`

原 B12b 关闭合同必须被逐字节保存；恢复记录必须保存原缺失审查哈希并绑定两份新审查文件的真实 SHA-256；产品源码相对 B12b 验证快照不得变化；B12 Transaction/Runtime 边界、完整 HTTP 生命周期和真实 Chromium 必须继续通过。

## 基线

在恢复材料尚不存在的旧工作树上记录真实红基线：`prior-closed-change.json`、`recovery.json` 与两份替代审查均缺失，新增证据完整性反例必须失败；B12 Transaction/Runtime 边界累计反例继续通过，其他产品行为不得形成红灯。

## 修复轮次

- 最大轮次：2
- 当前轮次：1
- 失败后：只修复证据恢复文件或恢复记录；任何产品源码变化立即停止并重新规划。
