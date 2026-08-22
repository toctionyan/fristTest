# Harness Child Workflow Delegation V1

## Goal

Connect `harness-full-dev` child Workflow steps to the already-validated runtime stack without creating a parent-owned replacement graph engine.

```text
full-development manifest current step
        |
        v
exact child Workflow activation
        |
        v
build_langgraph_workflow
        |
        +--> injected WorkflowAdapterDispatcher
        +--> required durable checkpointer
        |
        v
workflow_taskrun_bridge
        |
        v
Harness parent cursor
```

The delegator never interprets a child's internal steps. Each child keeps its registered declarative graph, Capability activation, provider bindings, dispatcher behavior, wait/resume routes, and attempt budgets.

## Exact parent/child boundary

The parent `HarnessRuntimeState.current_step` must name the exact manifest step being executed. A caller cannot skip from one child to another by passing a different Workflow ID. The child activation is derived from the selected parent Composition's available providers and provider preferences.

The injected checkpointer must be durable. SQLite and PostgreSQL LangGraph savers are accepted; `InMemorySaver` is rejected. One stable thread ID binds the TaskRun, parent Workflow, and manifest step, so an external event resumes the same child graph and declared stage.

## TaskRun projection

Nested and parent Graph END are intentionally different:

```text
child Graph END + parent has next step
        -> TaskRun RUNNING / CHILD_WORKFLOW_ENDED

final child Graph END + parent END
        -> TaskRun VALIDATING / WORKFLOW_GRAPH_ENDED_AWAITING_COMPLETION_POLICY
```

Neither transition satisfies TaskRun completion conditions or writes `COMPLETED`. External wait and Human Gate states continue to use the existing bridge and require durable resume evidence.

## Current boundary

This stage executes registered child Workflows through the existing runtime stack and advances the guarded parent cursor. Direct parent Skill steps still require real Host execution and canonical Skill receipts before they may advance. Scheduler/event adapters must call the explicit resume entry with the durable child state and matching event evidence; no polling loop is introduced here.
