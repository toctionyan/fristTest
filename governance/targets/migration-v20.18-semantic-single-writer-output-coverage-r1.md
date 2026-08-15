# 目标

- 目标 ID：migration-v20.18-semantic-single-writer-output-coverage-r1
- 变更标识：portable-migration-v20.18-semantic-single-writer-output-coverage-r1
- 执行上下文：local-change
- 目标类型：migration

Recover deterministic governance closure for the already-implemented V20.18 A2/B single-writer migration without changing its product scope.

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/modules/contracts.py`, `services/agent-service/src/agent_core/modules/registry.py`, `services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py`, `services/agent-service/src/agent_core/lifecycle/protocol.py`, `services/agent-service/src/agent_core/lifecycle/semantic_contract.py`, `services/agent-service/src/agent_core/kernel/semantic_contract.py`, `services/agent-service/src/agent_core/lifecycle/goal_planning.py`, `services/agent-service/src/agent_core/lifecycle/goal_granularity.py`, `services/agent-service/src/agent_core/lifecycle/goal_outputs.py`, `services/agent-service/src/agent_core/lifecycle/goal_capability_coverage.py`, `services/agent-service/src/agent_core/runtime/capability_effects.py`, `services/agent-service/src/agent_modules/ecommerce/semantic_vocabulary.py`, `services/agent-service/src/agent_modules/ecommerce/module.py`, `services/agent-service/src/agent_modules/ecommerce/capabilities/get_order_logistics.py`, `services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py`, `services/agent-service/tests/runtime/test_semantic_output_coverage.py`, `services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py`
- 新增抽象记录：docs/architecture/UNIFIED_SEMANTIC_PLANNING_MIGRATION.md

## 禁止范围

Do not modify predecessor Target/Claim/Baseline, Quality/Judge/Skill, Business Service, transaction authority, frontend, Phase C, WP-08, or production activation to obtain a pass.

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.18-semantic-single-writer-output-coverage-r1.json`
- 验收 ID：`V2018.A2B.SINGLE_WRITER_EXACT_OUTPUT`

The immutable PR #551 RED oracle must fail on exact baseline aeb9a445001d4922e13a032e4cccc12f8ff34e9a and pass on historical A2/B candidate 55403a01f957257fbbefead32bcde21b7d866001; full Quick must converge without scope expansion.

## 基线

Baseline is exact original permit pre-write source aeb9a445001d4922e13a032e4cccc12f8ff34e9a plus the immutable PR #551 RED oracle overlay. This successor uses a new Target/Claim/baseline identity and does not reuse the invalid predecessor baseline identity.

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
