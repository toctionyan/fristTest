# 目标

- 目标 ID：certify-v20.17-b15c3-real-model-release-bundle-authority
- 变更标识：certify-v20.17-b15c3-real-model-release-bundle-authority
- 执行上下文：local-change
- 目标类型：certification

将 Release Quality Loop 的真实模型认证从三份独立执行结果，切换为唯一同会话 bundle 权威。

## 允许范围

- 允许变更路径：`governance/quality-loop-policy.json`, `scripts/quality_loop.py`, `scripts/create_ci_quality_target.py`, `governance/claims/v20.6-protected-certification.json`, `governance/claims/v20.6.2-project-release-certification.json`, `services/agent-service/tests/runtime/test_b15c_real_model_certification_dimension.py`, `services/agent-service/tests/runtime/test_b15c3_real_model_release_bundle_authority.py`, `services/agent-service/tests/architecture/test_quality_loop_controller.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/NEW_ABSTRACTION_PRODUCT_RUNTIME_GATES.md`, `docs/architecture/V20_17_B15C3_REAL_MODEL_RELEASE_BUNDLE_AUTHORITY.md`, `governance/targets/certify-v20.17-b15c3-real-model-release-bundle-authority.md`, `governance/claims/certify-v20.17-b15c3-real-model-release-bundle-authority.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B15C3_REAL_MODEL_RELEASE_BUNDLE_AUTHORITY.md`

## 禁止范围

不得允许三份历史或独立认证结果拼接为 Release PASS；不得删除 bundle 内部组件脚本；不得降低浏览器 Gate、Provider 身份、session、工作区或调用证明要求；不得把缺 Key、跳过或环境阻断写成 PASS。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b15c3-real-model-release-bundle-authority.json`
- 验收 ID：`V20-17-B15C3-RELEASE-BUNDLE-AUTHORITY-001`

旧树在三个独立 Gate 全绿时错误形成认证。修复后 Release policy 只有一个 bundle 认证入口；聚合器仅接受有效 bundle 与两个配置模型浏览器 Gate，旧独立结果必须失败关闭。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复 Release 编排和认证维度，不修改模型调用组件、业务语义或事务状态。

## 基线

红基线：B15c2 已建立同会话 bundle，但 Release Quality Loop 尚未把它设为唯一认证权威。
