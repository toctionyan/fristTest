# 目标

- 目标 ID：certify-v20.17-b17b-production-release-execution
- 变更标识：certify-v20.17-b17b-production-release-execution
- 执行上下文：local-change
- 目标类型：certification

将 B17a 单次生产认证 Bundle 接入唯一受保护发布入口，并禁止确定性诊断工作流构建发布包。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `.github/workflows/integration-diagnostic.yml`, `scripts/run_production_release.py`, `scripts/create_ci_quality_target.py`, `governance/claims/v20.6.2-project-release-certification.json`, `services/agent-service/tests/runtime/test_b17b_production_release_execution.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17B_PRODUCTION_RELEASE_EXECUTION.md`, `governance/claims/certify-v20.17-b17b-production-release-execution.json`, `governance/targets/certify-v20.17-b17b-production-release-execution.md`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B17B_PRODUCTION_RELEASE_EXECUTION.md`

## 禁止范围

不得修改 Agent 业务逻辑、Prompt、事务状态、数据库实现或 B17a 组件证据合同；不得将模型桩、历史证据或环境阻断升级为 production closed。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17b-production-release-execution.json`
- 验收 ID：`V20-17-B17B-PRODUCTION-EXECUTION-001`

旧发布工作流必须在同一反例上失败；修复后只有官方模型 Secret 驱动的一键执行器能够在生产 Bundle PASS 后构建 protected artifact。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复发布执行与 CI provenance，不修改产品运行时。

## 基线

红基线：B17a 已有统一认证控制器，但现有 release workflow 仍由 deterministic model stub 驱动，CI Claim 仍引用被淘汰的独立 real-model/browser Gate，且没有唯一的一键发布执行器。
