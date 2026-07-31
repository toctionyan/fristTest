# 目标

- 目标 ID：certify-v20.17-b17i-production-execution-handoff
- 变更标识：certify-v20.17-b17i-production-execution-handoff
- 执行上下文：local-change
- 目标类型：certification

关闭 B17h 在 `release-admission` 失败时只有 Job 日志、没有独立可下载 JSON 证据，以及最终仓库/Environment/密钥/输入/Artifact 验收缺少唯一执行手册的交接边界。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `deployment/ci/release-toolchain-lock.json`, `scripts/release_admission_contract.py`, `scripts/release_toolchain_contract.py`, `services/agent-service/tests/runtime/test_b17i_production_execution_handoff.py`, `services/agent-service/tests/runtime/test_b17g_production_execution_readiness.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17I_PRODUCTION_EXECUTION_HANDOFF.md`, `docs/operations/B17I_FINAL_PRODUCTION_EXECUTION_RUNBOOK.md`, `governance/claims/certify-v20.17-b17i-production-execution-handoff.json`, `governance/targets/certify-v20.17-b17i-production-execution-handoff.md`, `governance/active-change.json`, `README.md`, `CHANGELOG.md`, `B17I_STAGE_SUMMARY.json`, `PHASE_CANDIDATE_NOTICE.md`, `PHASE_CANDIDATE_MANIFEST.json`, `release/MANIFEST.json`, `release/VALIDATION_REPORT.md`
- 新增抽象记录：`docs/architecture/V20_17_B17I_PRODUCTION_EXECUTION_HANDOFF.md`

## 禁止范围

不得修改客服 Agent 语义、Prompt、Capability、事务协议、业务规则、数据库实现或 RAG 行为；不得向准入 Job 注入任何生产密钥；不得把 Runbook、模拟日志或本地 JSON 冒充真实 GitHub 生产认证；不得生成 `production_closed`。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17i-production-execution-handoff.json`
- 验收 ID：`V20-17-B17I-PRODUCTION-EXECUTION-HANDOFF-001`

`release-admission` 必须为 PASS、FAIL 和 BLOCKED_BY_ENVIRONMENT 原子写入同一个脱敏 JSON，并在无密钥 Job 中通过 `if: always()` 以 Run ID/Attempt 唯一命名上传。静态供应链合同必须锁定结果路径、Artifact 名称、无密钥边界和缺文件即失败。最终 Runbook 必须覆盖仓库根目录、protected main、Environment、三个密钥、输入、三类 Artifact 和 production_closed 验收。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复准入证据持久化、Workflow 上传、供应链合同和执行手册，不修改客服产品运行时。

## 基线

红基线：B17h 的 `release-admission` 在失败时只输出日志，`protected-release` 因 needs 失败不会启动，因此没有结构化 Artifact；同时没有唯一最终执行手册，操作者容易把外层目录上传错位置、漏建 Environment 或误判 Artifact。
