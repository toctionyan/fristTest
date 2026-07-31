# V20.17 B15b1：真实模型语义认证身份边界

## 目标

把 B15a 的官方 Provider 身份合同扩展到真实模型语义原型认证。语义脚本不能因为模型返回结构正确的 `declare_turn_goals` 就宣称真实模型通过；调用前必须验证官方 HTTPS Provider，调用后每个响应必须验证 Provider 报告模型、token usage 与 finish reason。

## 红基线

旧版 `verify_preprod_conversation_smoke.py` 没有调用真实模型身份合同。localhost OpenAI-compatible 模型桩只要返回 12 个结构正确的目标声明，就可以输出 PASS。正式红基线位于 `.quality/product-code/certify-v20.17-b15b1-real-model-semantic-identity-boundary/red-baseline-v3`，声明 `V20-17-B15B1-SEMANTIC-IDENTITY-001` 在旧实现上为 FAILED。

## 新增项

新增并复用 `agent_core.model_calls.real_model_identity` 中的真实 Provider 身份与响应元数据认证边界。`verify_preprod_conversation_smoke.py` 在读取语义案例、建立模型客户端和发起任何请求之前完成身份预检；每个语义响应在进入业务 Oracle 之前生成非敏感 Provider attestation。

## 唯一职责

该边界只回答两个问题：本次请求是否发送到允许认证的官方 Provider，以及返回值是否携带与配置一致的模型标识、正数 token usage 和 finish reason。它不判断业务语义是否正确，不替代 Goal/Plan Oracle，也不拥有对话状态、业务事实或事务状态。

## 替换或删除项

替换 `verify_preprod_conversation_smoke.py` 中“只要 OpenAI-compatible 响应结构正确即可参与认证”的旧入口。删除语义认证脚本对 localhost、私网、HTTP、测试凭据、测试模型以及无 Provider 元数据响应的隐式信任。B15a 的身份解析仍是唯一 Provider 身份实现，不新增第二套 URL、Key 或模型判定器。

## 删除证据

- localhost 确定性语义桩在旧实现上可以完成 12 次调用并返回 PASS；修复后同一反例在首次模型调用前失败，桩调用次数为 0。
- 缺少真实 API key 时脚本返回 `BLOCKED_BY_ENVIRONMENT / api_key_missing`，不会回退到模型桩。
- 缺失或不匹配的 provider model、usage 或 finish reason 会返回认证失败，不进入语义 PASS 汇总。
- 生产脚本不输出 API key、完整 Prompt 或完整模型响应。

## 验证

1. 定向测试覆盖 localhost 身份旁路、缺少 key、官方身份的 12 个语义案例、usage 缺失和凭据脱敏。
2. 对抗桥纳入标准运行时反例，旧实现正式 Baseline 必须 FAILED，修复后同一声明必须 VERIFIED。
3. 缺少真实 Provider 凭据的当前环境只允许记录 `BLOCKED_BY_ENVIRONMENT`，不得声明完成真实语义认证。
4. 完整 Quick 继续执行架构、系统反例、Agent、Business、前端和生命周期回归；浏览器或真实模型环境阻断与本声明的代码结论分开记录。

## 范围

本阶段只关闭单轮语义原型认证的身份和响应元数据边界。多轮浏览器 Campaign 与完整真实模型生命周期留给 B15b2/B15c。
