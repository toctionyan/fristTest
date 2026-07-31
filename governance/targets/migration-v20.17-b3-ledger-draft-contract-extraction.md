# 目标

- 目标 ID：migration-v20.17-b3-ledger-draft-contract-extraction
- 变更标识：portable-migration-v20.17-b3-ledger-draft-contract-extraction
- 执行上下文：local-change
- 目标类型：migration

把纯 TransactionDraft 数据合同迁移到中立 operations 合同层，删除 ledger 对 transaction 执行包的反向依赖，使 ledger 退出主 SCC，同时保留 transaction.model 兼容导出和前两轮依赖债务成果。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/operations/draft.py`, `services/agent-service/src/agent_core/transaction/model.py`, `services/agent-service/src/agent_core/ledger/ledger.py`, `services/agent-service/tests/architecture/test_ledger_scc_extraction.py`, `services/agent-service/tests/architecture/test_readiness_boundary_scc_extraction.py`, `docs/architecture/V20_17_B3_LEDGER_DRAFT_CONTRACT.md`
- 新增抽象记录：docs/architecture/V20_17_B3_LEDGER_DRAFT_CONTRACT.md

## 禁止范围

不得修改 Draft 状态、命令摘要算法、Ledger Schema、事务授权/提交/持久化、Agent Loop、State Schema、Business Service、质量策略或依赖债务基线；不得通过复制两套 Draft 实现制造假解耦。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b3-ledger-draft-contract-extraction.json`
- 验收 ID：`LEDGER-DRAFT-CONTRACT-SCC-001`

TransactionDraft 纯合同只有一个实现 Owner；transaction.model 兼容导出行为等价；ledger 不再导入 transaction；主 SCC 从 12 降到 11；ledger、rag、utils 均保持退出。

## 基线

旧基线由 transaction.model 同时拥有纯 Draft 合同，ledger 直接导入 transaction，主 SCC 为 12，新的 ledger 边界反例失败；B1/B2 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复本目标的唯一 Draft 合同 Owner 或兼容导出；没有可度量依赖改善时停止并重新规划。
