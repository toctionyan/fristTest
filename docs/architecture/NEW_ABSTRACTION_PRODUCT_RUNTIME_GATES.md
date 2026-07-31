# Product Runtime Gates

## Problem

Unit、jsdom、静态合同和目标声明 smoke 都无法证明产品能由真实浏览器穿过公开 HTTP 边界，也无法证明模型在完整 Lifecycle Graph 的 tool observation 边界上仍保持协议正确。

## Owners

- `full-lifecycle-canary`：隔离确定性模型、Agent、Business 和临时数据的公开生命周期。
- `product-browser-journey`：Chromium 桌面/移动用户旅程与可访问性。
- `preproduction-real-model-certification-bundle`：同一现场 session 下统一执行真实 provider base smoke、语义原型与完整 Graph 只读生命周期认证。

三个 Gate 互不替代。Quick 的确定性 canary 用于高频回归；release 的真实模型 canary 用于 provider/protocol 风险；PostgreSQL integration 仍由既有独立 Gate 负责。

## 新抽象替换记录

- 新增项：`full-lifecycle-canary`、`product-browser-journey`、`preproduction-real-model-certification-bundle` 三个 Gate，以及既有 Context/CapabilityGate/model_calls Owner 内的 RuntimeResultRef、Target 判别联合和职责预算协议。
- 唯一职责：分别证明公开 API 完整事务链、用户可操作浏览器旅程、真实 provider 完整只读 Graph；Runtime 协议只负责让这些合法链路在不放宽安全边界的前提下执行。
- 替换或删除项：替换“HTTP smoke 等于产品可用”“目标声明 smoke 等于真实模型闭环”“所有 ResultRef 必须已最终展示”“Target 任意字段可组合”“所有模型调用竞争一个无保留总额度”等旧假设；没有增加第二条 Agent、事务、Ledger 或展示主链。
- 为什么不能并入现有 Owner：三个 Gate 属于不同证据层，不能互相替代；运行协议本身分别并入现有 `context/`、`runtime/CapabilityGate` 和 `model_calls/`，没有新建平行 Facade/Registry。
- 迁移顺序：先冻结 Gate 与记录红基线，再实现隔离服务和 Chromium；由真实模型逐步暴露 ResultRef、Target、预算缺陷，每次先加入通用反例，再修改唯一 Owner，最后重跑完整 Quick 和真实模型 canary。
- 删除证据：release real-model Gate 不再读取既有 `AGENT_TEST_URL`；Capability Target 不再接受 mode/field 矛盾组合；同轮 ResultRef 不再因未最终展示而误拒绝，也不能仅凭 Ledger 存在放行；Planner 不再消耗 verifier 保留额度。
- 验证：`test_runtime_result_ref_pipeline.py`、`test_capability_target_schema.py`、`test_command_and_model_governance.py`、确定性 full lifecycle、真实 Chromium 桌面/移动旅程，以及真实 provider 两轮订单→最贵项→物流完整 Graph。

## Runtime protocols uncovered by the Gates

- `RuntimeResultRef` consumption：跨轮引用必须来自最终发布；同轮中间结果只能是本轮更早成功 Observation 的直接输出或其已验证成员，且 MatchProof/ExecutionPermit/effect/scope 必须一致。它替换“所有可消费引用都必须已经最终展示”的过宽假设，没有创建第二个 Ledger。
- Target algebra：模块 Target Schema 是以 mode/operator 判别的联合；`entity_match + left_handle`、缺失 limit 的 take 等矛盾组合在 CapabilityGate 前失败，不留给领域 resolver 猜测。
- Model-call role budgets：`model_calls` 仍是唯一入口和总 Trace，但 Planner、Verifier、Support 分别有硬上限；Planner 不能挤占目标、能力或 Answer Release 的安全校验额度。
- Real-model identity：`preproduction-real-model-certification-bundle` 总是拥有同一 session 的真实 provider 三组件进程，不复用 deterministic `AGENT_TEST_URL`。

上述协议均由真实 Gate 发现并由通用反例保护，不针对某个商品、订单句式或模型输出写分支。
