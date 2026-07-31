# 目标

- 目标 ID：certify-v20.17-b17e-release-supply-chain-authority
- 变更标识：certify-v20.17-b17e-release-supply-chain-authority
- 执行上下文：local-change
- 目标类型：certification

修复 B17d 正式发布工作流仍使用可变 GitHub Action 大版本标签、运行时无版本安装 uv、可变 Node 主版本、可变 pgvector 镜像标签、隐藏声明文件默认漏传，且生产证据未绑定真实 CI 工具链的供应链假绿边界。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `deployment/ci/release-toolchain-lock.json`, `deployment/ci/uv-requirements-linux-x86_64.txt`, `scripts/release_toolchain_contract.py`, `scripts/production_certification_contract.py`, `scripts/verify_production_certification_bundle.py`, `scripts/run_managed_quality_integration.py`, `scripts/verify_production_postgres_bundle.py`, `scripts/verify_production_browser_bundle.py`, `scripts/run_production_release.py`, `scripts/quality_loop.py`, `services/agent-service/frontend/package.json`, `services/agent-service/tests/runtime/test_b17a_production_certification_authority.py`, `services/agent-service/tests/runtime/test_b17c_production_release_control_closure.py`, `services/agent-service/tests/runtime/test_b17e_release_supply_chain_authority.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17E_RELEASE_SUPPLY_CHAIN_AUTHORITY.md`, `governance/claims/certify-v20.17-b17e-release-supply-chain-authority.json`, `governance/targets/certify-v20.17-b17e-release-supply-chain-authority.md`, `governance/active-change.json`, `CHANGELOG.md`, `B17D_STAGE_SUMMARY.json`, `B17E_STAGE_SUMMARY.json`, `PHASE_CANDIDATE_NOTICE.md`, `PHASE_CANDIDATE_MANIFEST.json`, `release/MANIFEST.json`, `release/VALIDATION_REPORT.md`
- 新增抽象记录：`docs/architecture/V20_17_B17E_RELEASE_SUPPLY_CHAIN_AUTHORITY.md`

## 禁止范围

不得修改客服 Agent 的语义理解、Prompt、Capability、事务协议、业务规则或数据库实现；不得使用可变 Action 标签、未哈希安装器、可变运行时主版本或跨工具链拼接证据；不得因当前环境缺少锁定 venv、Node 依赖、Docker、浏览器或密钥而伪造生产 PASS，也不得生成 production closed。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17e-release-supply-chain-authority.json`
- 验收 ID：`V20-17-B17E-RELEASE-SUPPLY-CHAIN-001`

B17d 的可变 Action 标签、无版本 uv 安装和跨工具链证据组合必须作为反例被拒绝。修复后，Workflow、依赖锁、已安装环境、Docker 客户端/服务端、不可变 pgvector 镜像以及所有生产认证组件必须绑定同一 `toolchain_fingerprint_sha256`；PostgreSQL 与浏览器必须报告同一镜像引用和容器镜像 ID；任一缺失、篡改或不一致都必须失败关闭。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复发布供应链锁、工具链证据、会话绑定、质量维度、Workflow 和对应反例，不修改客服产品语义。

## 基线

红基线：同一个源码提交可在不同时间解析到不同 GitHub Action、uv、Node 或 pgvector 镜像；`.quality` 隐藏声明文件还可能被 artifact 上传默认规则漏掉；现有生产 Bundle 和最终关单摘要不携带工具链指纹，因而无法证明所有绿色结果来自同一受保护执行栈。
