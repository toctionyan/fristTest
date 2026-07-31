# 目标

- 目标 ID：certify-v20.17-b15b2-real-model-lifecycle-attestation-boundary
- 变更标识：certify-v20.17-b15b2-real-model-lifecycle-attestation-boundary
- 执行上下文：local-change
- 目标类型：certification

关闭真实模型完整 Lifecycle Graph 认证中的身份与响应轨迹旁路。`verify_preprod_full_lifecycle.py` 必须在启动服务前验证官方 Provider 身份，并对每个公开回合的成功模型调用验证 Provider 报告模型、正数 token usage 和 finish reason。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/model_calls/gateway.py`, `services/agent-service/src/agent_core/model_calls/real_model_identity.py`, `services/agent-service/src/agent_core/model_calls/__init__.py`, `services/agent-service/scripts/verify_preprod_full_lifecycle.py`, `services/agent-service/tests/runtime/test_b15b2_real_model_lifecycle_attestation_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B15B2_REAL_MODEL_LIFECYCLE_ATTESTATION_BOUNDARY.md`, `governance/targets/certify-v20.17-b15b2-real-model-lifecycle-attestation-boundary.md`, `governance/claims/certify-v20.17-b15b2-real-model-lifecycle-attestation-boundary.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B15B2_REAL_MODEL_LIFECYCLE_ATTESTATION_BOUNDARY.md`

## 禁止范围

不得用模型桩代替真实 Provider；不得把确定性 lifecycle canary 当作真实模型认证；不得输出 API key、完整 Prompt、完整模型响应或用户消息；不得修改业务事实、Plan/State 权威或事务状态机。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b15b2-real-model-lifecycle-attestation-boundary.json`
- 验收 ID：`V20-17-B15B2-LIFECYCLE-ATTESTATION-001`

旧实现必须在 localhost 生命周期桩反例上失败；修复后同一反例必须在 Harness 启动前返回 `FAIL / real_model_identity_invalid`。缺少真实 key 时返回 `BLOCKED_BY_ENVIRONMENT`。官方身份下，每个回合至少存在一项通过 Provider 元数据认证的成功模型调用。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复真实模型生命周期身份和调用轨迹证明，不改业务语义 Oracle。

## 基线

红基线：B15b1 候选树中的 `verify_preprod_full_lifecycle.py` 仅依赖 `deterministic_model=False`，没有官方 Provider 预检，也没有逐轮 Provider 响应元数据证明。
