# 目标

- 目标 ID：repair-v20.16-control-plane-self-test-closure
- 变更标识：repair-v20.16-control-plane-self-test-closure
- 执行上下文：local-change
- 目标类型：repair

修复阻断 V20.16 Quick Gate 的两个控制平面自测，使错误合同断言与当前正式文案一致，并让 Repair Orchestrator 临时工作区携带完整最小控制器依赖。

## 允许范围

- 允许变更路径：`services/agent-service/tests/architecture/test_quality_loop_governance.py`
- 新增抽象记录：无

## 禁止范围

不得修改 V20.16 产品运行时代码、Quality Judge、中央 Quality Policy、Skill、Business Service 或事务/展示权威边界。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.16-control-plane-self-test-closure.json`
- 验收 ID：`CONTROL-PLANE-SELF-TEST-CLOSURE-001`

两个原始失败必须在未修复源码上真实失败，修复后由同一独立 Gate 全部通过。

## 基线

以用户上传的 V20.16 phase candidate 原包建立红基线；不得用修改后的测试重新生成红基线。

## 修复轮次

- 最大轮次：2
- 当前轮次：1
- 失败后：仅修复测试合同或临时控制器夹具，不修改被测试的正式 Judge 行为。
