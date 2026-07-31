# 目标

- 目标 ID：certify-v20.17-b15a-real-model-identity-boundary
- 变更标识：certify-v20.17-b15a-real-model-identity-boundary
- 执行上下文：local-change
- 目标类型：certification

关闭真实模型认证中的身份伪造边界。受保护模型 smoke 不能仅凭一个固定字符串宣告真实模型通过；它必须在调用前验证官方 HTTPS Provider 端点、非测试凭据和非桩模型标识，在调用后验证动态挑战、Provider 报告模型、token usage 与结束原因，并只输出不含密钥的审计信息。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/model_calls/real_model_identity.py`, `services/agent-service/src/agent_core/model_calls/__init__.py`, `services/agent-service/scripts/verify_model_smoke.py`, `services/agent-service/.env.example`, `services/agent-service/tests/runtime/test_b15a_real_model_identity_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B15A_REAL_MODEL_IDENTITY_BOUNDARY.md`, `governance/targets/certify-v20.17-b15a-real-model-identity-boundary.md`, `governance/claims/certify-v20.17-b15a-real-model-identity-boundary.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B15A_REAL_MODEL_IDENTITY_BOUNDARY.md`

## 禁止范围

不得把模型桩、localhost/private endpoint、HTTP endpoint、测试 key、测试模型名或固定回显当成真实模型；不得输出 API key；不得放宽现有模型调用预算；不得修改业务事实、Plan/State 权威、事务状态机或数据库 Schema；不得把缺少真实凭据写成 PASS。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b15a-real-model-identity-boundary.json`
- 验收 ID：`V20-17-B15A-REAL-MODEL-IDENTITY-001`

旧实现必须在 localhost + deterministic test model + fake key + 固定回显的反例上失败；修复后同一反例必须返回 `FAIL / real_model_identity_invalid`。官方 OpenAI 默认端点和官方 DeepSeek HTTPS 端点必须通过预检；DeepSeek 已弃用别名、测试凭据、非 HTTPS、私网/本地端点和缺失响应 usage 必须 fail closed。没有真实密钥时，真实模型执行状态必须保持 `BLOCKED_BY_ENVIRONMENT`，不能写成已认证。

## 基线

红基线：B14g 的 `verify_model_smoke.py` 只检查静态 `model-smoke-ok`，通过 monkeypatch 或 OpenAI-compatible localhost 桩即可返回 PASS，证据没有官方 Provider 端点、动态挑战或 Provider 响应元数据约束。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复真实模型身份与 smoke 证据边界；真实语义、多轮和全生命周期质量留给 B15b/B15c，不得在本阶段用固定桩代替。
