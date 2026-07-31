# V20.17 B15b2：真实模型完整生命周期认证边界

## 目标

把 B15a/B15b1 的官方 Provider 身份合同扩展到经过公开 HTTP API 的两轮完整 Lifecycle Graph 认证。仅有最终回答正确不再足以证明真实模型参与了规划、校验与回答发布；启动前必须验证官方 Provider，每一轮运行轨迹中的成功模型调用都必须具有匹配的 Provider 模型、正数 token usage 和 finish reason。

## 红基线

旧版 `verify_preprod_full_lifecycle.py` 只以 `ProductRuntimeHarness(deterministic_model=False)` 表示真实模型，却没有验证 Harness 环境。把 `OPENAI_API_BASE` 指向 localhost 模型桩并返回两轮安全答案时，脚本仍可输出 PASS。模型调用轨迹只记录配置模型与 token 用量，缺少 Provider 报告模型和 finish reason，无法证明响应来自声明的 Provider。

## 新增项

新增真实模型生命周期预检、模型调用记录元数据和逐轮轨迹认证。生命周期脚本在启动任何服务前复用 `resolve_real_model_identity`；ModelCall Gateway 只记录非敏感的 Provider 模型、finish reason 和 usage；每个公开对话回合结束后，从正式 graph snapshot 读取本回合模型调用记录并完成认证。

## 唯一职责

该边界只证明完整生命周期中使用的模型身份和响应元数据真实可核验。它不替代业务结果 Oracle、Plan/State 权威、工具结果验证、事务安全或公开回答检查。

## 替换或删除项

替换“`deterministic_model=False` 即等于真实 Provider”的隐式判断。删除 localhost、私网、HTTP、测试凭据、测试模型或缺少 Provider 元数据的调用进入生命周期认证的可能。ModelCall Gateway 仍是唯一模型调用记录入口，不新增平行调用器。

## 删除证据

- localhost 生命周期桩在旧实现上可以进入 Harness 并返回 PASS；修复后在 Harness 启动前失败，服务启动次数和生命周期调用次数均为 0。
- 缺少真实 key 时返回 `BLOCKED_BY_ENVIRONMENT / api_key_missing`，不会启动服务。
- 缺少 Provider 模型、finish reason 或正数 usage 的任何成功模型调用都会使本回合认证失败。
- 证据只保留非敏感元数据，不包含 API key、完整 Prompt、完整响应或用户消息。

## 验证

1. 定向测试覆盖 localhost 旁路、缺少 key、Gateway 元数据记录、逐轮轨迹认证和缺失 usage 的失败路径。
2. 对抗桥纳入标准运行时反例，旧实现正式 Baseline 必须 FAILED，修复后同一声明必须 VERIFIED。
3. 当前无真实 Provider key 的环境必须保持 `BLOCKED_BY_ENVIRONMENT`，不能用确定性桩完成真实生命周期认证。
4. 完整 Quick 回归架构、Agent、Business、前端、覆盖率和确定性完整生命周期；继承的浏览器环境问题单独记录。
