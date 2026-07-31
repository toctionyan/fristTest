# 目标

- 目标 ID：certify-v20.17-b17f-ci-run-identity-replay-authority
- 变更标识：certify-v20.17-b17f-ci-run-identity-replay-authority
- 执行上下文：local-change
- 目标类型：certification

修复 B17e 证据只绑定源码与工具链、却未绑定当前受保护 GitHub Actions 仓库、Workflow、分支、提交、Run ID 和 Run Attempt 的跨运行重放假绿边界，并把干净 checkout、HEAD 一致、origin 一致和不持久化凭证升级为可执行发布合同。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `deployment/ci/release-toolchain-lock.json`, `scripts/release_run_identity.py`, `scripts/release_toolchain_contract.py`, `scripts/run_production_release.py`, `scripts/quality_loop.py`, `scripts/release_artifact.py`, `services/agent-service/tests/architecture/test_clean_release_integrity.py`, `services/agent-service/tests/runtime/test_b17c_production_release_control_closure.py`, `services/agent-service/tests/runtime/test_b17e_release_supply_chain_authority.py`, `services/agent-service/tests/runtime/test_b17f_ci_run_identity_replay_authority.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17F_CI_RUN_IDENTITY_REPLAY_AUTHORITY.md`, `governance/claims/certify-v20.17-b17f-ci-run-identity-replay-authority.json`, `governance/targets/certify-v20.17-b17f-ci-run-identity-replay-authority.md`, `governance/active-change.json`, `CHANGELOG.md`, `B17F_STAGE_SUMMARY.json`, `PHASE_CANDIDATE_NOTICE.md`, `PHASE_CANDIDATE_MANIFEST.json`, `release/MANIFEST.json`, `release/VALIDATION_REPORT.md`
- 新增抽象记录：`docs/architecture/V20_17_B17F_CI_RUN_IDENTITY_REPLAY_AUTHORITY.md`

## 禁止范围

不得修改客服 Agent 的语义理解、Prompt、Capability、事务协议、业务规则、数据库实现或模型路由；不得把仓库、Workflow、Ref、Commit、Run ID 或 Run Attempt 之一缺失的证据用于正式关单；不得接受未保护分支、脏工作树、HEAD 漂移、错误 origin、持久化 checkout 凭证或上一轮 Run/Attempt 证据；不得因当前环境缺少锁定 venv、Docker、浏览器或密钥而伪造生产 PASS，也不得生成 production closed。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17f-ci-run-identity-replay-authority.json`
- 验收 ID：`V20-17-B17F-CI-RUN-IDENTITY-001`

B17e 中可被跨 Run、跨 Attempt 或跨 checkout 重放的证据必须作为红基线被拒绝。修复后，工具链证据、生产认证会话、Quality Loop Summary、Clean Release Manifest 和最终关单台账必须绑定同一个 `run_identity_fingerprint_sha256`；当前环境与证据的 repository、workflow_ref、commit、protected ref、run_id、run_attempt 必须逐项一致；任一缺失、篡改或不一致都必须失败关闭。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复受保护 CI 运行身份、证据防重放、Workflow checkout 合同、发布台账和对应反例，不修改客服产品语义。

## 基线

红基线：B17e 的工具链指纹不包含本次 GitHub Run 身份，Quality Loop 签名摘要和发布包也不要求当前 Run/Attempt 指纹；同一提交的上一轮证据或不同 checkout 生成的证据可能被复制到当前关单流程中，而 Workflow 没有在代码合同中验证受保护主分支、HEAD 等于 GITHUB_SHA、干净工作树、正确 origin 与不持久化凭证。
