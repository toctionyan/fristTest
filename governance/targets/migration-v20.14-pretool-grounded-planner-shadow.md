# 目标

- 目标 ID：migration-v20.14-pretool-grounded-planner-shadow
- 变更标识：portable-migration-v20.14-pretool-grounded-planner-shadow
- 执行上下文：local-change
- 目标类型：migration

在模型发出具体业务 Tool Call 之前，读取冻结语义 Goal、精确 Capability Surface 与 Capability Contract v2，生成局部 Grounded Shadow Plan。Shadow 必须表达候选能力路径、输入来源、步骤输出、前置条件、授权、完成证明和 Goal 依赖；本阶段仅对照，不接管正式 Tool 选择、Permit 或执行。

## 允许范围

- 新增抽象记录：docs/architecture/V20_14_PRETOOL_GROUNDED_PLANNER_SHADOW.md
- 允许变更路径：services/agent-service/src/agent_core/lifecycle/pretool_planner.py, services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py, services/agent-service/src/agent_core/lifecycle/state.py, services/agent-service/src/agent_core/lifecycle/state_contracts.py, services/agent-service/src/agent_core/lifecycle/context_runtime.py, services/agent-service/src/agent_core/utils/flow_debug.py, services/agent-service/tests/runtime/test_pretool_shadow_planner.py, services/agent-service/tests/runtime/test_goal_binding_counterexamples.py, docs/architecture/**
- `services/agent-service/src/agent_core/lifecycle/pretool_planner.py`
- `services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py`
- `services/agent-service/src/agent_core/lifecycle/state.py`
- `services/agent-service/src/agent_core/lifecycle/state_contracts.py`
- `services/agent-service/src/agent_core/lifecycle/context_runtime.py`
- `services/agent-service/src/agent_core/utils/flow_debug.py`
- `services/agent-service/tests/runtime/test_pretool_shadow_planner.py`
- `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`
- `docs/architecture/**`

## 禁止范围

不得修改 Skill、Quality Policy、Judge、Business Service、事务状态机、Capability Contract v2 业务定义、正式 Tool 执行顺序、ExecutionPermit、Presentation 或正式 React 页面。Shadow Plan 不得进入模型提示、不得创建 Tool Call、不得阻止执行、不得修改冻结语义，也不得在通用 Runtime 写死退款、发票或物流步骤。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.14-pretool-grounded-planner-shadow.json`
- 验收 ID：PRETOOL-PLAN-BEFORE-TOOL-001, CONTRACT-PATH-CLOSURE-001, SHADOW-NON-AUTHORITY-001
- Shadow Plan 必须只依赖冻结语义合同与模块 Capability Contract v2，不读取已有 Tool Calls 作为计划来源。
- `capability_output` 类型输入必须绑定到本地合同中声明相同输出类型的生产步骤；不存在生产者时必须保留 unresolved，不得猜测业务步骤。
- 多条候选完成路径可并存，并以确定性规则选择 preferred path；Goal 间顺序只能来自冻结语义 `depends_on`。
- 模型 Tool Calls 与 Shadow 偏差必须可观察，但 Shadow 不能阻止执行、创建 Permit 或改变语义。
- 原 V20.12 Goal Evidence、V20.13 Capability Contract v2、强上下文、多意图、事务和浏览器代表链不得回归。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后只修改 Shadow Planner、Shadow State/Observability 接入和对应测试；不得提前让 Planner 接管正式执行。

## 基线

在 V20.13.0 Capability Contract v2 候选源码上仅加入本阶段目标、Decision、Claims 和反例，记录 Pre-Tool Shadow Planner 缺失的真实红基线。
