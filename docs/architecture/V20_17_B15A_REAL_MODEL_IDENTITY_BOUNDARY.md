# V20.17 B15a：真实模型身份认证边界

## 新增项

- `agent_core.model_calls.real_model_identity`：受保护真实模型认证专用的 Provider 身份预检和响应证明模块。
- `real-model-identity@1`：只包含 Provider、官方端点、模型和不可逆凭据短指纹的非敏感身份记录。
- `real-model-response-attestation@1`：动态挑战、Provider 报告模型、token usage 和 finish reason 的证明记录。

## 唯一职责

- 在真实模型认证调用前，验证官方 HTTPS Provider、模型与非测试凭据。
- 在调用后，验证每次运行的随机挑战以及 Provider 返回的模型和 usage 元数据。
- 仅输出可审计但不含 API key、Prompt 或完整模型响应的证据。
- 普通生产运行时仍可使用 OpenAI-compatible 配置；本模块只裁决“能否声明真实模型已认证”。

## 替换或删除项

- 替换 `verify_model_smoke.py` 仅校验固定 `model-smoke-ok` 字符串的旧认证方式。
- 不删除生产模型网关，不改变 `get_model()` 和统一 `invoke_model()` 调用边界。
- 不把 B15a 身份证明扩张成多轮语义或完整业务生命周期证明；这些由后续 B15b/B15c 负责。

## 问题

原 smoke 只要求固定文本。OpenAI-compatible localhost 桩、测试模型和伪 key 只要回显该文本，也可以得到 `PASS`；证据不能证明请求发往官方 Provider，也没有动态挑战、Provider 模型、token usage 或结束原因。

真实模型认证现在必须同时满足：

1. 端点为 `api.openai.com` 或 `api.deepseek.com` 的官方 HTTPS 地址；
2. 端点不含 userinfo、query、fragment、非 443 端口或非标准路径；
3. key 非空、长度合理且不含 test/mock/fake/stub/placeholder 等标记；
4. 模型名非测试名并与 Provider 对齐；
5. DeepSeek 认证拒绝已弃用的 `deepseek-chat`、`deepseek-reasoner` 兼容别名；
6. 每次运行生成新的随机挑战，响应必须逐字匹配；
7. Provider 响应必须报告匹配模型、正数 token usage 和 finish reason；
8. 证据只记录 key 的短 SHA-256 指纹，不输出 key。

## 删除证据

- localhost + deterministic test model + fake key + 固定回显的旧路径必须从 `PASS` 转为 `FAIL / real_model_identity_invalid`。
- `verify_model_smoke.py` 不再以静态 `model-smoke-ok` 作为运行时认证挑战。
- 输出 JSON 中不得出现 `OPENAI_API_KEY` 值。
- 缺少真实 key 时必须保持 `BLOCKED_BY_ENVIRONMENT`，不得写成认证通过。

## 验证

- `services/agent-service/tests/runtime/test_b15a_real_model_identity_boundary.py`
- `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py::test_v20_17_b15a_real_model_identity_boundary_adversarial_bridge`
- `adversarial-runtime-counterexamples`
- `python-test-suites`
- 正式真实模型执行仍要求受保护环境提供官方 Provider key；当前环境没有 key，因此真实执行状态不得超过环境阻断。

## 阶段验证结果

- 红基线：旧 `verify_model_smoke.py` 在 localhost 确定性模型桩、测试 key、测试模型名和固定回显下错误返回 PASS；正式红基线声明为 `FAILED`。
- 修复后 B15a 声明：`V20-17-B15A-REAL-MODEL-IDENTITY-001 = VERIFIED`。
- B15a 定向反例：15 passed。
- 对抗运行时反例：118 passed。
- 系统运行反例：17 passed。
- Agent 标准测试：713 passed。
- Business 标准测试：28 passed。
- Frontend Vitest：28 passed；Frontend Build：PASS。
- 覆盖率基线：Python 最低 72.78%，Frontend 54.32%，均高于既有基线。
- 完整生命周期：PASS。
- Architecture：PASS；Architecture Debt：RESOLVED；跨包依赖循环：0。
- 当前环境未提供真实 Provider API key，`verify_model_smoke.py` 实际返回 `BLOCKED_BY_ENVIRONMENT / api_key_missing`，因此真实模型认证没有被写成 PASS。
- 当前验证树缺少 `playwright` Node 包，完整 Quick 的 `product-browser-journey` 保持 FAIL；这是继承的浏览器验证环境问题，不改变 B15a 身份防伪声明已经验证的结论。

本阶段只能作为 `PHASE_CANDIDATE_REAL_MODEL_KEY_AND_BROWSER_DEPENDENCY_BLOCKED` 交付，不能标记为真实模型认证完成。

