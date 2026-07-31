# 目标

- 目标 ID：certify-v20.17-b15c-real-model-certification-bundle-boundary
- 变更标识：certify-v20.17-b15c-real-model-certification-bundle-boundary
- 执行上下文：local-change
- 目标类型：certification

建立真实模型最终认证的唯一汇总入口，将 smoke、语义原型和完整生命周期三类证据绑定到同一次现场执行。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/model_calls/real_model_certification_bundle.py`, `services/agent-service/src/agent_core/model_calls/__init__.py`, `services/agent-service/scripts/verify_model_smoke.py`, `services/agent-service/scripts/verify_preprod_conversation_smoke.py`, `services/agent-service/scripts/verify_preprod_full_lifecycle.py`, `services/agent-service/scripts/verify_real_model_certification_bundle.py`, `services/agent-service/tests/runtime/test_b15c_real_model_certification_bundle_boundary.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B15C_REAL_MODEL_CERTIFICATION_BUNDLE_BOUNDARY.md`, `governance/targets/certify-v20.17-b15c-real-model-certification-bundle-boundary.md`, `governance/claims/certify-v20.17-b15c-real-model-certification-bundle-boundary.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B15C_REAL_MODEL_CERTIFICATION_BUNDLE_BOUNDARY.md`

## 禁止范围

不得接受历史证据路径作为认证输入；不得允许不同 Provider、模型、凭据指纹、session 或工作区结果拼接；不得输出 API key、完整 Prompt、完整模型响应或用户消息；不得把缺 Key 或环境阻断写成 PASS。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b15c-real-model-certification-bundle-boundary.json`
- 验收 ID：`V20-17-B15C-CERTIFICATION-BUNDLE-001`

旧树因不存在汇总模块而在新增反例上失败。修复后，相同 session/工作区/身份的三组件证据可通过；任一组件缺失、重放、身份或指纹不一致均失败；缺少真实 Key 时组件启动次数必须为零。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复认证证据汇总边界，不修改业务语义、Plan/State 或事务状态机。

## 基线

红基线：B15b2 候选树只有三份独立认证脚本，没有单次现场执行的最终汇总权威。
