# 目标

- 目标 ID：migration-v20.18-semantic-single-writer-output-coverage
- 变更标识：migration-v20.18-semantic-single-writer-output-coverage
- 执行上下文：architecture-closure-issue-550
- 目标类型：migration

恢复 V20.11.1 已选定的单一语义作者边界：Semantic Writer 在 freeze 前只看到 capability-independent domain semantic vocabulary；validator 仅能给 violation；FrozenSemanticContract 冻结 requested outputs；freeze 后 Runtime 仅按 Capability 声明的 exact semantic-output coverage 建立执行前沿。A2/B 作为一个原子 correctness unit，Phase C 历史引用 target compiler 不在本批。

## 允许范围

允许变更路径：services/agent-service/src/agent_core/modules/contracts.py, services/agent-service/src/agent_core/modules/registry.py, services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py, services/agent-service/src/agent_core/lifecycle/protocol.py, services/agent-service/src/agent_core/lifecycle/semantic_contract.py, services/agent-service/src/agent_core/kernel/semantic_contract.py, services/agent-service/src/agent_core/lifecycle/goal_planning.py, services/agent-service/src/agent_core/lifecycle/goal_granularity.py, services/agent-service/src/agent_core/lifecycle/goal_outputs.py, services/agent-service/src/agent_core/lifecycle/goal_capability_coverage.py, services/agent-service/src/agent_core/runtime/capability_effects.py, services/agent-service/src/agent_modules/ecommerce/semantic_vocabulary.py, services/agent-service/src/agent_modules/ecommerce/module.py, services/agent-service/src/agent_modules/ecommerce/capabilities/get_order_logistics.py, services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py, services/agent-service/tests/runtime/test_semantic_output_coverage.py, services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py

## 禁止范围

不得修改 Business Service、事务状态机、前端、Quality/Judge/Skill、ReleaseRun 协调器或 Phase C target compiler；不得新增电商关键词分类器；不得用 similarity/embedding/LLM mapper 颁发执行权；不得把 validator replacement semantic values 回灌给 Writer；不得启动新的 WP-08 ReleaseRun。

## 验收条件

- 最低质量模式：quick
- Pre-freeze prompt/schema capability-blind。
- Module semantic vocabulary 独立于 CapabilityRegistry availability，可存在 zero-capability semantic output。
- New-turn requested outputs freeze/integrity bound。
- Validator writer-facing feedback violation-only。
- Exact requested-output coverage 是 new turn 唯一 capability completion authority。
- 物流 status/ETA 正例通过；courier contact phone 负例 deterministic unsupported。
- supported + unsupported sibling 不塌缩。
- historical frozen checkpoint compatibility 不得变成 new-turn parallel authority。
- 全量 Agent Quick、Business、Skill control-plane 不回归。
- protected live-model adversarial matrix 在合并前的后续 certification gate 必须通过。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 无真实进展时停止并重新规划；不得扩大到 Phase C 或 phrase patch。

## 基线

RED evidence: `governance/repair-cases/migration-v20.18-semantic-single-writer-output-coverage/evidence/red-baseline.md`。
Base candidate: `ffd29dde28c87e86e3f59f2bca88a0134a86273d`。
产品初始 governed-source fingerprint: `18323ce037401c2e523fcd2b614865a307bd1deea12ce609da5e3ae5f787f108` (14 existing files; 3 approved new paths absent at baseline)。
