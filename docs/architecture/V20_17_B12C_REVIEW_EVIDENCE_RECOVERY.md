# V20.17 B12c：B12b 审查证据恢复

## 新增项

- `docs/architecture/evidence-recovery/migration-v20.17-b12b-transaction-runtime-boundary/prior-closed-change.json`：保存 B12b 原始关闭合同的逐字节副本。
- `recovery.json`：披露原审查文件丢失事实、历史哈希和替代审查的新哈希。
- `scope-planner.md` 与 `adversarial-reviewer.md`：重新生成的独立替代审查，不冒用历史摘要值。

## 唯一职责

- B12c 只负责治理证据恢复和来源披露。
- B12b Transaction/Runtime 产品实现、中央 Quick 结果和控制面验证保持不变。
- 新替代审查只证明当前树重新接受了范围与反向检查，不能被描述为原文件的字节级恢复。

## 替换或删除项

- 不替换 B12b 关闭合同中记录的历史审查路径或 SHA-256。
- 不删除任何产品实现、测试、事务状态机或历史质量证据。
- 新恢复清单显式替代“假定原审查文件仍存在”的错误状态。

## 事件与恢复规则

B12b 达到 `closed / CONVERGED` 后，原 scope-planner 与 adversarial-reviewer Markdown 文件被删除。关闭合同仍保留其历史路径与 SHA-256，但摘要不能反推原始字节。恢复过程必须保存原关闭合同，明确记录丢失，并为全新审查生成独立哈希。

## 删除证据

- 当前交付树不得继续引用不存在的审查文件作为唯一可读证据。
- `recovery.json` 必须保留历史缺失哈希，并将 `historical_hashes_reused_for_replacement` 固定为 `false`。
- 恢复测试必须验证原关闭合同仍为 `closed / CONVERGED`，且替代审查的实际哈希与恢复清单一致。

## 验证

- `services/agent-service/tests/architecture/test_b12_review_evidence_recovery.py`
- `services/agent-service/tests/architecture/test_transaction_runtime_boundary_scc.py`
- 完整 Quick 的 18 个 required Gates。
- HTTP 生命周期和真实 Chromium Journey。

## 未解决债务

本阶段不关闭剩余 `lifecycle / runtime` 两包 SCC，不执行 State/Loop 瘦身，也不声明真实模型认证或 Prompt Injection 认证完成。
