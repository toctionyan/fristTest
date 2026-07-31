# V20.17 B15c3 真实模型 Release Bundle 权威

## 目标

将 Release Quality Loop 的真实模型认证从三份彼此独立的执行结果，切换为唯一同会话 bundle 权威，防止不同 session、工作区或 Provider 结果被拼接成发布认证。

## 新增项

- 新增唯一 Release Gate：`preproduction-real-model-certification-bundle`。
- 新增 `real-model-certification-dimension@2` 聚合合同。
- 聚合结果绑定 bundle session、工作区 SHA-256、官方 Provider、模型、凭据指纹、三组件覆盖和模型调用数。

## 唯一职责

`verify_real_model_certification_bundle.py` 是 Release 真实模型认证的唯一执行入口。Quality Loop 的 `real_model_certification` 维度只消费该 bundle Gate，以及配置模型浏览器会话与 Campaign 两个产品 Gate。三个内部组件脚本继续被 bundle 现场调用，但不再独立授予 Release 权威。

## 替换或删除项

- 从 Release Policy 删除 `preproduction-model-base-smoke` 的独立发布权。
- 从 Release Policy 删除 `preproduction-conversation-prototypes` 的独立发布权。
- 从 Release Policy 删除 `preproduction-full-lifecycle-model` 的独立发布权。
- `clean-release-preflight` 改为依赖 `preproduction-real-model-certification-bundle`。
- 不删除三个脚本文件，因为它们仍是 bundle 内部组件及人工诊断入口。

## 删除证据

- Release Policy 中三个旧独立 Gate 的数量为零。
- `clean-release-preflight` 不再依赖旧生命周期 Gate。
- 三个旧 Gate 即使全部返回 PASS，只要缺少 bundle Gate，认证维度必须返回 `required_real_model_bundle_gate_missing`。
- bundle contract、session、工作区、组件集合、Provider 身份或调用证明不完整时，认证维度必须返回 `real_model_bundle_evidence_invalid`。

## 验证

- 红基线使用新 Policy 和旧聚合实现，新增反例失败。
- 修复后相同 Policy、Target 和反例转绿。
- bundle + 两个配置模型浏览器 Gate 可形成 `real-model-certification-dimension@2`。
- 环境阻断、跳过、缺失 Gate、三份独立结果拼接和不完整 bundle 均 fail-closed。
- B15a～B15c、Quality Loop 控制器和全量 Python 回归不得下降。
