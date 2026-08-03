# 目标

- 目标 ID：repair-v20.17-b23-stage5-nonenvironment-regression
- 变更标识：portable-repair-v20.17-b23-stage5-nonenvironment-regression
- 执行上下文：local-change
- 目标类型：repair

Close Stage-5 non-environment regressions exposed by broad dependency-aware validation without weakening environment gates.

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/runtime/answer_release_alignment.py`, `services/agent-service/src/agent_core/ledger/ledger.py`, `services/agent-service/src/agent_core/ledger/__init__.py`, `services/agent-service/src/agent_core/runtime/capability_gate.py`, `services/agent-service/src/agent_core/context/visible_result_refs.py`, `services/agent-service/tests/architecture/test_command_and_model_governance.py`, `services/agent-service/tests/architecture/test_productization_boundary.py`, `services/agent-service/tests/architecture/test_quality_loop_governance.py`, `services/agent-service/tests/context/test_context_bundle_runtime.py`, `services/agent-service/tests/context/test_dialogue_counterexamples.py`, `services/agent-service/tests/context/test_parameterized_capability_alignment.py`, `services/agent-service/tests/context/test_runtime_result_ref_pipeline.py`, `services/agent-service/tests/context/test_strong_context_case_execution.py`, `services/agent-service/tests/runtime/test_semantic_grounding_read.py`, `services/agent-service/tests/runtime/test_workflow_runtime.py`
- 新增抽象记录：无

## 禁止范围

Business Service, frontend, production credentials, environment requirements and unrelated product modules are forbidden.

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b23-stage5-nonenvironment-regression.json`
- 验收 ID：`V20-17-B23-STAGE5-NONENV-001`

Reproduced tests pass; result-ref negative scopes remain rejected; narrative goals skip model judging; current protected-release and semantic authority test fixtures are used.

## 基线

红基线（baseline）：B22 package plus the reproduced Stage-5 focused failures captured before implementation.

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
