# 目标

- 目标 ID：certify-v20.17-b17c-production-release-control-closure
- 变更标识：certify-v20.17-b17c-production-release-control-closure
- 执行上下文：local-change
- 目标类型：certification

修复 B17b 正式 CI 无法识别真实 PASS Summary、失败无控制台账及产物完整性校验不足的问题，使受保护发布控制器在每个阶段 fail closed。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `scripts/run_production_release.py`, `services/agent-service/tests/runtime/test_b17c_production_release_control_closure.py`, `services/agent-service/tests/runtime/test_b17b_production_release_execution.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17C_PRODUCTION_RELEASE_CONTROL_CLOSURE.md`, `governance/claims/certify-v20.17-b17c-production-release-control-closure.json`, `governance/targets/certify-v20.17-b17c-production-release-control-closure.md`, `governance/active-change.json`, `CHANGELOG.md`, `B17C_STAGE_SUMMARY.json`, `PHASE_CANDIDATE_NOTICE.md`, `PHASE_CANDIDATE_MANIFEST.json`, `release/MANIFEST.json`, `release/VALIDATION_REPORT.md`
- 新增抽象记录：`docs/architecture/V20_17_B17C_PRODUCTION_RELEASE_CONTROL_CLOSURE.md`

## 禁止范围

不得修改 Agent 业务逻辑、Prompt、Capability、计划、事务状态、数据库实现、B17a 生产认证组件或浏览器旅程；不得把环境阻断、失败 Summary、身份不一致或损坏产物升级为 production closed。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17c-production-release-control-closure.json`
- 验收 ID：`V20-17-B17C-PRODUCTION-CONTROL-001`

旧 B17b 判定器必须在真实 CI PASS Summary 反例上失败；修复后必须接受 `CI_VERIFIED` 和真实生产维度合同，同时拒绝旧字段伪装、身份漂移、缺失控制结果及损坏 sidecar。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复发布控制面、workflow provenance、控制 evidence 或产物验证，不修改产品运行时。

## 基线

红基线：B17b 发布执行器错误读取生产认证字段、拒绝 CI 的 `CI_VERIFIED`，并被 Agent 全量依赖耦合；workflow 还上传错误的 claims 路径。即使外部环境完整，正式发布也无法可靠关单。
