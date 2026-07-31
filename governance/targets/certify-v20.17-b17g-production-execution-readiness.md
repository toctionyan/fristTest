# 目标

- 目标 ID：certify-v20.17-b17g-production-execution-readiness
- 变更标识：certify-v20.17-b17g-production-execution-readiness
- 执行上下文：local-change
- 目标类型：certification

关闭 B17f 正式发布 Workflow 在非法触发条件下可能因唯一 secret-bearing Job 被 `if` 静默跳过、从而让 Workflow 显示绿色但没有执行任何认证的最终准入假绿；同时修复阶段候选元数据仍描述 B17e/旧 Skill-only 变更的真实性缺口。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `deployment/ci/release-toolchain-lock.json`, `scripts/release_admission_contract.py`, `scripts/release_toolchain_contract.py`, `services/agent-service/tests/runtime/test_b17g_production_execution_readiness.py`, `services/agent-service/tests/runtime/test_b17e_release_supply_chain_authority.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17G_PRODUCTION_EXECUTION_READINESS.md`, `governance/claims/certify-v20.17-b17g-production-execution-readiness.json`, `governance/targets/certify-v20.17-b17g-production-execution-readiness.md`, `governance/active-change.json`, `README.md`, `CHANGELOG.md`, `B17G_STAGE_SUMMARY.json`, `PHASE_CANDIDATE_NOTICE.md`, `PHASE_CANDIDATE_MANIFEST.json`, `release/MANIFEST.json`, `release/VALIDATION_REPORT.md`
- 新增抽象记录：`docs/architecture/V20_17_B17G_PRODUCTION_EXECUTION_READINESS.md`

## 禁止范围

不得修改客服 Agent 的语义理解、Prompt、Capability、事务协议、业务规则、数据库实现、模型路由或 RAG 行为；不得把非法分支触发转换为 skipped-only 绿色 Workflow；不得让无密钥准入 Job访问生产 Environment 或 Secrets；不得因当前环境缺少锁定 venv、Docker、浏览器或真实密钥而伪造完整 Quick、生产 PASS 或 `production_closed`。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17g-production-execution-readiness.json`
- 验收 ID：`V20-17-B17G-PRODUCTION-EXECUTION-READINESS-001`

B17f 中“非受保护分支导致唯一发布 Job skipped”的路径必须作为红基线被拒绝。修复后，无密钥 `release-admission` Job 必须始终执行并显式校验 event、workflow、受保护 ref、provider、model 和 embedding 参数；正式 `protected-release` 必须依赖该 Job，同时保留原平台条件作为第二道防线。非法触发必须使 Workflow 失败，而不是只留下 skipped Job。阶段说明、README、Changelog、活动变更和候选 Manifest 必须一致标识 B17g，并明确生产关单仍需真实受保护环境执行。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复发布准入、元数据真相、供应链静态合同和对应反例，不修改客服产品语义。

## 基线

红基线：B17f 把 `workflow_dispatch + protected main` 条件只放在唯一 `protected-release` Job 的 `if` 上。条件不满足时，该 Job 被 GitHub Actions 直接跳过，Workflow 可能没有失败步骤；此外 `PHASE_CANDIDATE_NOTICE.md` 仍以 B17e 为标题，根 README 仍宣称本轮只修改 Skill，导致候选与未来正式产物说明不可信。
