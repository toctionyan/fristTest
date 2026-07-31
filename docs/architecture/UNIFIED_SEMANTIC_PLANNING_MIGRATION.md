# Unified Semantic Planning Migration

## Scope

This migration changes the semantic authority and cross-turn goal lifecycle without changing Business Service authority, the exact-match CapabilityGate, transaction authority, public API, or customer-visible receipt rules.

## Authority chain

1. The model proposes open-language semantics in `semantic_proposal`.
2. Runtime validates evidence, references, dependencies and explicit state operations.
3. `frozen_semantic_contract` is the sole formal semantic owner for the turn.
4. `turn_goal_plan` is generated from that contract only and remains a compatibility projection.
5. `grounded_execution_plan` is a validated execution view and never rewrites the frozen requested effect.
6. Business facts remain owned by verified observations, Assessment, Business Service and Receipt.

## Context trust partition

`ContextProjection v2` keeps these channels separate:

- public dialogue and visible result references;
- verified execution observations;
- execution diagnostics that are explicitly not user intent, target authority or business facts;
- active semantic Goal records and Goal Blockers;
- transaction state projected from durable authority.

Failed or rejected tool calls may guide recovery but cannot become semantic evidence.

## Goal lifecycle

`goal_records` maintain only semantic work lifecycle:

- `OPEN`, `ACTIVE`, `BLOCKED`, `PAUSED`, `COMPLETED`, `CANCELLED`, `SUPERSEDED`.

The lifecycle changes only through explicit frozen-contract operations or verified execution-plan progress. It does not copy order state, eligibility, authorization, Attempt or Receipt state.

`goal_blockers` replace the singleton clarification authority. Multiple blockers can remain active and can be resolved independently in the same turn that creates, pauses or supersedes other goals.

## No program language interpretation

Runtime no longer uses keyword or regular-expression rules to infer correction, continuation, pronoun scope, consultation, query or action. Continuation capability hints require an explicit `continuation_of` relation or a verified legacy checkpoint mapping. A structured target/scope gap may be rejected against a unique visible member, but Runtime does not inspect the user utterance to decide whether a pronoun exists.

## Atomic semantic state publication

A declaration publishes the frozen contract, compatibility projection, Goal records, blockers and focus atomically only after all deterministic checks pass. A stale Goal or Blocker reference rejects the entire transition and cannot leave a partially accepted legacy plan.

## Compatibility and removal path

During this migration:

- `turn_goal_plan`, `workflow_plan` and `pending_clarification` remain compatibility projections;
- new production execution reads the frozen semantic contract and grounded execution plan first;
- old fields cannot independently alter semantics;
- later capability-grounding migration will replace `goal_type`-based capability discovery with exact requested-effect identities;
- after all production readers and tests migrate, obsolete regular-expression helpers and legacy write paths must be removed.

## Verification

The red baseline and regression tests cover:

- invalid legacy categories are rejected rather than rewritten;
- open requested effects freeze without a GoalType;
- verified observations and diagnostics remain separated;
- multiple blockers resolve independently;
- explicit Goal lifecycle transitions persist across turns;
- continuation tools are never inferred from language keywords.

## 新抽象替换记录

- 新增项：`semantic_contract`、`goal_lifecycle`、`goal_blockers`、`context.projection`、`continuation_runtime`，以及现有 Workflow 上的 grounded execution projection。
- 唯一职责：分别拥有本轮正式语义、跨轮语义 Goal 生命周期、按 Goal 独立阻断、可信上下文分区、显式 continuation capability hint；它们不拥有业务事实、目标解析、能力存在、授权或事务结果。
- 替换或删除项：替换 `TurnGoalPlan` 同时承担模型候选和正式语义、`pending_clarification` 单例承担所有跨轮任务、失败 Tool 观察混入语义上下文、Runtime 正则推断纠正/继续/代词引用等旧职责。`turn_goal_plan`、`workflow_plan`、`pending_clarification` 暂时仅保留兼容投影，后续读路径迁移完成后删除其正式权力和无用辅助函数。
- 为什么不能并入现有 Owner：实现放在既有 `context/` 与 `lifecycle/` Owner 内，没有新建根级平行主链；拆分出的模块对应不同权威对象，若继续混在 `goal_planning.py`/`dialogue_runtime.py` 会再次形成候选、状态、执行和语言启发式的 God File。
- 迁移顺序：先记录反例红基线，再新增纯合同和可信投影；接入 State/Context/Workflow Shadow；切换冻结合同为唯一正式语义；引入 GoalRecord/Blocker；删除正则语义推断；随后由独立 capability-grounding 迁移替换旧 GoalType 能力发现。
- 删除证据：正式 Workflow Goal 只能由冻结合同产生；旧计划标记 `compatibility_projection_only`；失败 Tool 只出现在 diagnostics；continuation hint 只来自 `continuation_of` 或验证过的旧 checkpoint；生产路径不再调用 GoalType 正则纠错函数。
- 验证：`test_unified_semantic_planning_contract.py`、`test_state_contracts.py`、`test_clarification_continuation_protocol.py`，以及后续完整 Quick/Integration 回归。

The static strong-context catalog remains a compatibility oracle during this phase; its GoalType patterns are not imported by production routing.
The compatibility oracle must be removed only under a separate governed test-oracle migration.

## Exact capability grounding implemented in this migration

The earlier plan to defer capability grounding was retired after scope review. The implementation now keeps effect identities with each module-owned `ToolCapabilityContract`:

- `completion_effects` declares effects the capability can complete;
- `support_effects` declares effects for which it is only a prerequisite;
- Runtime builds a bounded exact index and never derives identity from Tool names, descriptions or similarity;
- an unknown requested effect remains `absent_proven` and may only expose the unsupported reporter;
- one semantic Goal may expand to several support/completion steps without being split into artificial user Goals.

## Grounded execution plan validation

`grounded_execution_plan@2` is the execution authority for newly frozen turns. It records the semantic contract ID and digest, then validates:

- unique Goal, Step and Effect identifiers;
- exactly one formal Goal binding for every business Effect;
- exact capability role: completion, support or proven unsupported report;
- known Goal and Effect dependencies;
- no self-dependency or dependency cycle;
- a completion or unsupported path for every required Goal;
- a structural digest that excludes mutable execution progress but protects Tool/capability/Goal/dependency structure.

Tool dispatch recomputes this validation and rejects stale semantic bindings or structural tampering before CapabilityGate issues a permit.

## Formal-reader cutover

The following readers now prefer formal objects:

- capability semantic verification reads `frozen_semantic_contract` goals;
- answer-release alignment reads frozen Goals and `grounded_execution_plan`;
- clarification and Goal Blocker creation read frozen Goals and grounded progress;
- pending write-lane serialization is derived from exact capability contracts, not `goal_type=action`;
- failure replay projects grounded execution progress.

Legacy fields remain only for historical checkpoints, UI compatibility and tests that explicitly exercise migration behavior. They do not become a second semantic or execution authority.

## Current certification boundary

The deterministic and module-level tests can run under isolated test-only message/graph import stubs. Those stubs are not product files and are excluded from delivery. Because the current container lacks the real LangGraph/LangChain packages, graph execution, checkpoint resume behavior, real-model behavior, PostgreSQL and browser paths remain unverified. The migration must remain `PENDING` and must not promote a new Architecture Baseline until those environments pass.
