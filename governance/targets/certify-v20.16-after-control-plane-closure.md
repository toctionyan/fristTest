# 目标

- 目标 ID：certify-v20.16-after-control-plane-closure
- 变更标识：certify-v20.16-after-control-plane-closure
- 执行上下文：local-change
- 目标类型：certification

在独立控制平面修复完成后，对当前 V20.16 State Schema v2 + Legacy Cutover 候选执行完整中央 Quick Gate，识别剩余真实产品失败与环境阻断。

## 允许范围

- 允许变更路径：`services/agent-service/app/services/checkpoint_hydrator.py, services/agent-service/app/services/lifecycle_command_runner.py, services/agent-service/app/use_cases/conversation_turn.py, services/agent-service/src/agent_core/context/**, services/agent-service/src/agent_core/lifecycle/**, services/agent-service/src/agent_core/observability/failure_replay.py, services/agent-service/src/agent_core/runtime/answer_release_alignment.py, services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py, services/agent-service/src/agent_modules/ecommerce/shared/context.py, services/agent-service/tests/runtime/**, services/agent-service/tests/context/**, services/agent-service/tests/support/**, services/agent-service/tests/architecture/test_quality_loop_governance.py, docs/architecture/**`
- 新增抽象记录：无

## 禁止范围

本目标只认证当前候选，不修改产品源码、中央 Quality Policy、Skill、Business Service、事务或展示权威。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.16-after-control-plane-closure.json`
- 验收 ID：`V20-16-STATE-SCHEMA-CERT-001, V20-16-CONTROL-PLANE-CERT-001, V20-16-FRONTEND-QUICK-CERT-001`

必须执行中央 Quick 的全部 required Gates；定向 PASS 不得替代完整结论。

## 基线

只读认证当前候选；不重新制造 V20.16 红基线。

## 修复轮次

- 最大轮次：1
- 当前轮次：1
- 失败后：记录真实根失败并创建新的独立 repair/migration target，不在认证目标内修改代码。
