# 目标

- 目标 ID：repair-v20.16-lifecycle-canary-schema-v2-fixture
- 变更标识：repair-v20.16-lifecycle-canary-schema-v2-fixture
- 执行上下文：local-change
- 目标类型：repair

修复受保护确定性模型夹具仍输出旧 Goal Schema，导致 State Schema v2 完整 HTTP 生命周期反复拒绝目标声明并耗尽 Loop Budget 的问题。

## 允许范围

- 允许变更路径：`services/agent-service/tests/integration/model_stub.py, services/agent-service/tests/architecture/test_protected_model_stub_contract.py`
- 新增抽象记录：无

## 禁止范围

不得放宽 `requested_effect` 正式要求，不得修改产品 Runtime、Capability MatchProof、事务权威、Business Service、中央 Quality Policy 或 Loop Budget。

## 验收条件

- 最低质量模式：quick
- 验收 ID：`V20-16-LIFECYCLE-FIXTURE-V2-001`

原始隔离 HTTP Canary 必须真实复现 `GOAL_DECLARATION_INVALID / requested_effect.required_for_new_turn`；修复后同一 Canary 完成登录、聊天、Draft、输入、授权、提交、Receipt 与 SSE。

## 基线

红基线保存在 `.quality/manual-continuation/lifecycle-diagnostic`，包含失败响应、完整 graph trace、服务日志和临时数据库副本。

## 修复轮次

- 最大轮次：2
- 当前轮次：1
- 失败后：只修复确定性模型协议夹具，不改变产品语义验证合同。
