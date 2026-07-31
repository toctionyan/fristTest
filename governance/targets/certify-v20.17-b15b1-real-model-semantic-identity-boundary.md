# 目标

- 目标 ID：certify-v20.17-b15b1-real-model-semantic-identity-boundary
- 变更标识：certify-v20.17-b15b1-real-model-semantic-identity-boundary
- 执行上下文：local-change
- 目标类型：certification

关闭真实模型语义原型认证中的身份旁路。`verify_preprod_conversation_smoke.py` 必须在任何模型调用前复用 B15a 官方 Provider 身份预检，并对每一个语义响应验证 Provider 报告模型、正数 token usage 与 finish reason；localhost、私网、HTTP、测试凭据或测试模型不得进入语义认证。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/model_calls/real_model_identity.py`, `services/agent-service/src/agent_core/model_calls/__init__.py`, `services/agent-service/scripts/verify_preprod_conversation_smoke.py`, `services/agent-service/tests/runtime/test_b15b1_real_model_semantic_identity_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B15B1_REAL_MODEL_SEMANTIC_IDENTITY_BOUNDARY.md`, `governance/targets/certify-v20.17-b15b1-real-model-semantic-identity-boundary.md`, `governance/claims/certify-v20.17-b15b1-real-model-semantic-identity-boundary.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B15B1_REAL_MODEL_SEMANTIC_IDENTITY_BOUNDARY.md`

## 禁止范围

不得用模型桩代替真实 Provider；不得把单轮原型扩张成已完成多轮认证；不得输出 API key、完整 Prompt 或完整模型响应；不得修改业务事实、Plan/State 权威或事务状态机。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b15b1-real-model-semantic-identity-boundary.json`
- 验收 ID：`V20-17-B15B1-SEMANTIC-IDENTITY-001`

旧实现必须在 localhost 确定性语义桩反例上失败；修复后同一反例必须在模型调用前返回 `FAIL / real_model_identity_invalid`。缺少真实 key 时返回 `BLOCKED_BY_ENVIRONMENT`。官方身份下，每个语义响应必须产生非敏感元数据证明。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复语义认证身份与响应证明，不改语义 Oracle。

## 基线

红基线：B15a 候选树中的 `verify_preprod_conversation_smoke.py` 未调用官方 Provider 身份预检。localhost 确定性模型桩只要返回结构正确的 12 个 `declare_turn_goals` 调用即可输出 PASS。
