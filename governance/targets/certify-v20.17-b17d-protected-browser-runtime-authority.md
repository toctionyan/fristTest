# 目标

- 目标 ID：certify-v20.17-b17d-protected-browser-runtime-authority
- 变更标识：certify-v20.17-b17d-protected-browser-runtime-authority
- 执行上下文：local-change
- 目标类型：certification

修复 B17c 将本地 SQLite、开发登录、未签名业务身份和本地稀疏 RAG 浏览器旅程与独立 PostgreSQL 组件结果拼接成生产浏览器认证的假绿边界，使两个受保护浏览器旅程必须在同一个控制器拥有的 preprod 运行时和同一个 PostgreSQL 实例中执行。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `scripts/verify_full_lifecycle_canary.py`, `scripts/verify_product_browser_journey.py`, `scripts/verify_production_browser_bundle.py`, `scripts/production_certification_contract.py`, `scripts/run_production_release.py`, `services/agent-service/scripts/seed_ephemeral_rag_fixture.py`, `services/agent-service/frontend/e2e/product_journey.mjs`, `services/agent-service/frontend/e2e/strong_context_journey.mjs`, `services/agent-service/frontend/e2e/strong_context_campaign_journey.mjs`, `services/agent-service/tests/runtime/test_b17a_production_certification_authority.py`, `services/agent-service/tests/runtime/test_b17c_production_release_control_closure.py`, `services/agent-service/tests/runtime/test_b17d_protected_browser_runtime_authority.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `services/agent-service/tests/architecture/test_systemic_operational_closure.py`, `skill-system/tests/test_architecture_cycle_debt.py`, `docs/architecture/V20_17_B17D_PROTECTED_BROWSER_RUNTIME_AUTHORITY.md`, `governance/claims/certify-v20.17-b17d-protected-browser-runtime-authority.json`, `governance/targets/certify-v20.17-b17d-protected-browser-runtime-authority.md`, `governance/active-change.json`, `CHANGELOG.md`, `B17C_STAGE_SUMMARY.json`, `B17D_STAGE_SUMMARY.json`, `PHASE_CANDIDATE_NOTICE.md`, `PHASE_CANDIDATE_MANIFEST.json`, `release/MANIFEST.json`, `release/VALIDATION_REPORT.md`
- 新增抽象记录：`docs/architecture/V20_17_B17D_PROTECTED_BROWSER_RUNTIME_AUTHORITY.md`

## 禁止范围

不得修改客服 Agent 的语义理解、Prompt、Capability、计划、事务状态、业务规则或产品数据库实现；不得使用 `APP_PROFILE=local`、SQLite、`dev_token`、未签名 Actor、本地 RAG 或独立数据库结果证明生产浏览器闭环；不得把外部模型、Embedding、Docker、浏览器或锁定依赖故障伪装为代码 PASS，也不得生成 production closed。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17d-protected-browser-runtime-authority.json`
- 验收 ID：`V20-17-B17D-PROTECTED-BROWSER-001`

B17c 本地浏览器运行时必须作为反例被拒绝。修复后，两个配置模型浏览器旅程必须共同证明：`preprod`、JWT、关闭开发登录、签名业务身份、严格状态合同、模型 Verifier、Agent/Checkpoint/Business/RAG/Document Job 共用同一 PostgreSQL 权威，并携带同一数据库实例指纹。外部凭证、限流、超时、Docker、浏览器和锁定依赖缺失只能产生 `BLOCKED_BY_ENVIRONMENT`。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复生产浏览器 Harness、显式迁移/临时数据命令、认证注入、运行时证据契约、环境分类、Workflow 参数和对应反例，不修改客服产品语义。

## 基线

红基线：B17c 的浏览器组件仍以 `APP_PROFILE=local`、SQLite、`dev_token`、关闭 Actor 签名和 `local_sparse` RAG 执行；PostgreSQL 仅在另一个独立组件中通过。控制器可以把两个不属于同一真实运行时的绿色结果组合成生产浏览器证明。
