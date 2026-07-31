# 目标

- 目标 ID：certify-v20.16-continuation-final
- 变更标识：certify-v20.16-continuation-final
- 执行上下文：local-change
- 目标类型：certification

对 V20.16 State Schema v2 候选及后续控制平面、Schema v2 测试夹具、完整计划反例修复执行全新的中央 Quick 认证；不复用先前失败认证结论。

## 允许范围

- 允许变更路径：`services/agent-service/app/services/checkpoint_hydrator.py, services/agent-service/app/services/lifecycle_command_runner.py, services/agent-service/app/use_cases/conversation_turn.py, services/agent-service/src/agent_core/context/**, services/agent-service/src/agent_core/lifecycle/**, services/agent-service/src/agent_core/observability/failure_replay.py, services/agent-service/src/agent_core/runtime/answer_release_alignment.py, services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py, services/agent-service/src/agent_modules/ecommerce/shared/context.py, services/agent-service/tests/runtime/**, services/agent-service/tests/context/**, services/agent-service/tests/support/**, services/agent-service/tests/architecture/test_quality_loop_governance.py, services/agent-service/tests/architecture/test_quality_loop_controller.py, services/agent-service/tests/integration/model_stub.py, services/agent-service/tests/architecture/test_protected_model_stub_contract.py, docs/architecture/**`
- 新增抽象记录：无

## 禁止范围

本目标只认证当前候选，不在 Gate 运行期间修改源码、中央 Quality Policy、Skill、Business Service、事务或展示权威。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.16-continuation-final.json`
- 验收 ID：`V20-16-FINAL-STATE-SCHEMA-001, V20-16-FINAL-CONTROL-PLANE-001, V20-16-FINAL-REGRESSION-CLOSURE-001, V20-16-FINAL-FRONTEND-001, V20-16-FINAL-LIFECYCLE-001`

必须执行中央 Quick 的全部 required Gates；只有当前运行产生的证据可以完成 Claim。

## 基线

这是修复后的全新只读认证目标；红基线与中间失败证据保留在原迁移、控制平面修复和前序认证目录中。

## 修复轮次

- 最大轮次：1
- 当前轮次：1
- 失败后：按 Gate 分类交付；环境依赖阻断不得伪装成代码 PASS。
