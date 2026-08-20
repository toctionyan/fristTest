# Harness Workflow Orchestration V1

## Purpose

This stage is an incremental orchestration refactor only. It preserves the existing TaskRun, Skill Invocation, Quality, change-scope and GitHub authorities and changes only how an explicit development command reaches one or more Skills.

Previous route:

```text
Explicit Command -> Skill(s)
```

V1 route:

```text
Explicit Command
  -> Workflow Registry
  -> Workflow
  -> Skill Invocation
  -> existing executor / integrations
  -> existing TaskRun + Quality + completion authorities
```

## Boundaries

- Explicit commands remain opt-in. Free-form analysis is not forced through this registry.
- The command selects a Workflow, not a Skill directly.
- The Workflow selects the required canonical Skills.
- Natural-language payload is never reclassified by keyword rules.
- Workflow definitions are target-independent. They cannot embed `task_id`, `change_id`, repository, branch, GitHub repository or target identifiers.
- Skill Invocation receipts remain the authority proving Skill selection/load/response binding.
- `change-scope` remains the write gate for governed repository mutations.
- TaskRun remains the whole-task execution-state authority.
- Quality remains the acceptance/closure authority.
- GitHub remains an integration/execution surface, not the orchestration authority.

## Registry

The canonical registry is:

```text
skill-system/registry/dev-workflows.json
```

Schema:

```text
dev-workflow-registry@1
```

Each Workflow declares only orchestration metadata:

- `workflow_id`
- `request_class`
- `skills`
- `mode`
- `status_first`
- `deterministic_response`
- `write_governed`

Target binding and runtime subject identifiers are deliberately excluded.

## Current explicit routes

```text
/status      -> status-project
/continue    -> continue-project
/diagnose    -> diagnose-product
/arch        -> architecture-review
/agent-arch  -> customer-agent-architecture-review
/oracle      -> oracle-review
/repair      -> governed-repair
/review      -> adversarial-review
/cert        -> release-certification
```

## What this stage does not add

This stage does not add a semantic router, multi-intent planner, DAG runtime, second TaskRun, second Quality system, second completion authority, GitHub-specific Workflow logic, or any replacement for existing Skill Invocation receipts.

Later orchestration stages may add typed Workflow steps and executor/integration adapters, but only behind the same authority boundaries.
