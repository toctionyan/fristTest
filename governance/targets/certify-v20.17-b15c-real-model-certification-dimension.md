# 目标

- 目标 ID：certify-v20.17-b15c-real-model-certification-dimension
- 变更标识：certify-v20.17-b15c-real-model-certification-dimension
- 执行上下文：local-change
- 目标类型：certification

建立真实模型认证最终维度。只有同一 release run 中五个真实模型 Gate 全 PASS，且 base smoke、语义原型和完整生命周期的 Provider 身份完全一致，Quality Loop 才能声明 `real_model_certification=PASS`。

## 允许范围

- 允许变更路径：`scripts/quality_loop.py`, `services/agent-service/tests/runtime/test_b15c_real_model_certification_dimension.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B15C_REAL_MODEL_CERTIFICATION_DIMENSION.md`, `governance/targets/certify-v20.17-b15c-real-model-certification-dimension.md`, `governance/claims/certify-v20.17-b15c-real-model-certification-dimension.json`, `governance/active-change.json`
- 新增抽象记录：`docs/architecture/V20_17_B15C_REAL_MODEL_CERTIFICATION_DIMENSION.md`

## 禁止范围

不得复用历史 Gate；不得把 quick/integration 结果提升为真实模型认证；不得忽略 configured-model 浏览器会话或 Campaign；不得输出 API key；不得修改各模型认证脚本的 Oracle。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b15c-real-model-certification-dimension.json`
- 验收 ID：`V20-17-B15C-REAL-MODEL-DIMENSION-001`

旧实现必须在“五个 release Gate 全 PASS 且身份一致”的反例上失败，因为它仍返回 NOT_DECLARED；修复后该反例返回 PASS。环境阻断、SKIPPED、身份缺失或不一致必须 fail closed。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复真实模型认证维度聚合，不改 Gate 实现和业务代码。

## 基线

红基线：B15b2 候选树中的 `_quality_dimensions` 永久返回 `real_model_certification=NOT_DECLARED`，没有 release 证据聚合规则。
