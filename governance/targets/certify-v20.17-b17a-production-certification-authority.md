# 目标

- 目标 ID：certify-v20.17-b17a-production-certification-authority
- 变更标识：certify-v20.17-b17a-production-certification-authority
- 执行上下文：local-change
- 目标类型：certification

让 Release 只接受一个实时生产认证 Bundle，同时绑定真实模型、owned PostgreSQL/pgvector 与真实浏览器证据。

## 允许范围

- 允许变更路径：`scripts/production_certification_contract.py`, `scripts/verify_production_real_model_bundle.py`, `scripts/verify_production_postgres_bundle.py`, `scripts/verify_production_browser_bundle.py`, `scripts/verify_production_certification_bundle.py`, `scripts/quality_loop.py`, `governance/quality-loop-policy.json`, `services/agent-service/tests/runtime/test_b17a_production_certification_authority.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17A_PRODUCTION_CERTIFICATION_AUTHORITY.md`, `governance/targets/certify-v20.17-b17a-production-certification-authority.md`, `governance/claims/certify-v20.17-b17a-production-certification-authority.json`, `services/agent-service/tests/architecture/test_quality_loop_controller.py`, `services/agent-service/tests/architecture/test_systemic_operational_closure.py`, `services/agent-service/tests/runtime/test_b15c3_real_model_release_bundle_authority.py`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B17A_PRODUCTION_CERTIFICATION_AUTHORITY.md`

## 禁止范围

不得修改业务状态机、事务 Schema、工具选择、模型 Prompt 或数据库业务实现；不得读取历史组件证据形成 PASS；不得在缺少任一真实环境时写 production closed。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17a-production-certification-authority.json`
- 验收 ID：`V20-17-B17A-PRODUCTION-AUTHORITY-001`

旧 Release 独立绿灯策略必须在同一反例上失败；修复后唯一 production Bundle 权威转绿，标准回归不下降。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复生产认证控制面，不修改产品业务逻辑。

## 基线

红基线：B16c 具备三类独立认证控制器，但没有一个实时父控制器证明它们来自同一源码快照和同一认证会话。
