# Harness LangGraph Runtime V1

## Purpose

LangGraph is the Workflow runtime for sequence, branch, bounded loop, yield and resume. It is deliberately **not** a new lifecycle, Quality, business or completion authority.

```text
Workflow Definition
      -> Workflow Registry / Graph Contract
      -> Capability Activation
      -> LangGraph Runtime
          -> Skill dispatcher
          -> Executor / Gate dispatcher
          -> External wait / Human gate
      -> durable evidence
      -> TaskRun bridge
      -> Completion Policy
```

## Declarative topology

Workflow topology is declared in the version-controlled Workflow registry. The model does not dynamically invent arbitrary graphs.

A graph declares:

- `start`
- typed `steps`
- explicit `routes`
- bounded `max_attempts`

Supported step types:

```text
skill
executor
gate
external_wait
human_gate
```

Supported runtime terminal targets:

```text
END
WAITING_EXTERNAL
HUMAN_GATE
BLOCKED_UNRECOVERABLE
```

Internal `__workflow_*` node identifiers and terminal target names are reserved and cannot be used as user step IDs.

`END` means the graph has no more orchestration steps. It does **not** mean the TaskRun is completed.

## Skill and capability dispatch

A Skill node may only reference a Skill declared by the Workflow.

Executor, Gate and External Wait nodes may only reference a **required provider-neutral capability**. Runtime graph nodes cannot depend on an optional provider because optional provider absence must not make a previously compiled execution path invalid.

The runtime receives an injected `WorkflowStepDispatcher`. LangGraph therefore does not contain Skill implementation logic, GitHub API logic, Quality implementation logic, or repository write logic.

## Provider binding

Capability activation happens before graph execution:

```text
Workflow requirement
      -> Capability Resolver
      -> active Executor / Integration provider
```

A graph sees capability contracts such as:

```text
test.run
quality.evaluate
ci.run.wait
```

It never needs to know whether CI is GitHub Actions, GitLab CI or Jenkins.

## Repair Loop

The first reusable graph is `repair-and-prove`:

```text
repair
  -> focused-test
       RED   -> repair
       GREEN -> adversarial
                   findings -> repair
                   clean    -> quality
                                   RED   -> repair
                                   GREEN -> END
```

Every step has a hard attempt budget. Exceeding the budget transitions to `BLOCKED_UNRECOVERABLE`; it cannot silently spin forever.

Every executed step must return durable evidence references. Attempt history is retained rather than overwritten when a later attempt succeeds.

## Problem Ledger

LangGraph state can carry only `problem_ledger_ref`. The Problem Ledger remains the problem-closure authority.

A graph stage can say "currently adversarial testing" while the Problem Ledger independently says how many required findings remain open. The runtime must not convert graph position into problem closure.

## External wait and resume

`external_wait` is a yield boundary, not a polling loop.

A dispatcher returns a durable wait handle such as:

```json
{
  "provider": "github.actions",
  "correlation_ref": "run-123",
  "resume_event": "ci.completed"
}
```

The runtime transitions to `WAITING_EXTERNAL`, records the exact `resume_stage`, and exits the current graph invocation. The TaskRun bridge persists `WAITING_EXTERNAL_RESULT`.

When the scheduler/event adapter receives one matching external event, it records durable event evidence, transitions TaskRun back to `RUNNING`, and resumes the same declarative step with the event payload. That step can then return an already-declared outcome such as `green` or `red`.

```text
wait-ci
  -> pending -> WAITING_EXTERNAL
                  |
             ci.completed event
                  |
             resume wait-ci
                  |
          green / red / blocked
```

This is event-driven re-entry, not `while True + sleep + poll`. One event causes one resume invocation. Resume cannot invent a new step or route.

## Human Gate and resume

A Human Gate must return an explicit durable gate contract. The TaskRun bridge maps it to `BLOCKED` with `human_required=true` and records the exact resume stage.

A human decision must itself have durable evidence before TaskRun can transition back to `RUNNING`. The same gate step is then re-entered with the decision payload and must return one of its declared outcomes.

Ordinary RED test results, recoverable CI failures, or missing optional providers are not Human Gates.

## TaskRun bridge

Runtime state is projected into TaskRun lifecycle checkpoints:

```text
RUNNING               -> TaskRun RUNNING
WAITING_EXTERNAL      -> TaskRun WAITING_EXTERNAL_RESULT
external event resume -> TaskRun RUNNING
HUMAN_GATE            -> TaskRun BLOCKED / human_required
human decision resume -> TaskRun RUNNING
BLOCKED_UNRECOVERABLE -> TaskRun BLOCKED
WORKFLOW_END           -> TaskRun VALIDATING
```

The bridge never marks a TaskRun `COMPLETED` and never marks completion conditions.

At `WORKFLOW_END`, `next_action` is `EVALUATE_COMPLETION_POLICY`.

## Authority invariants

```text
LangGraph END          != TaskRun COMPLETED
Quality GREEN          != TaskRun COMPLETED
CI GREEN               != TaskRun COMPLETED
PR merged              != TaskRun COMPLETED
Skill success          != TaskRun COMPLETED
Capability binding     != write permit
Workflow state         != ProblemLedger authority
External event         != completion authority
Human decision         != completion authority
```

Completion remains a separate decision based on TaskRun required conditions and durable evidence.

## Future adapter work

The runtime intentionally does not fake an external ChatGPT host or fabricate Skill execution evidence. Future adapters can implement:

- Skill dispatcher backed by the canonical Skill Invocation contract
- local deterministic Executor dispatcher
- Integration adapters for GitHub/GitLab/Jenkins
- durable checkpointer storage and scheduler/event wake-up adapters

Those adapters must preserve the same authority boundaries.
