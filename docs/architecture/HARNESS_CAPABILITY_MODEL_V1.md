# Harness Capability Model V1

## Purpose

This stage separates Workflow intent from concrete execution providers.

A Workflow must say **what capability it needs**. It must not hard-bind GitHub, GitLab, Jenkins or another concrete provider.

```text
Workflow
  -> capability requirement
  -> Capability Resolver
      -> Executor provider
      -> Integration provider
```

## Boundary definitions

### Skill

A Skill is an AI/method capability: analysis, diagnosis, design, repair guidance, adversarial review, release review.

### Executor

An Executor is a deterministic action provider: workspace I/O, process execution, tests, Quality invocation, local VCS actions.

### Integration

An Integration is an external-system provider: hosted code review, CI systems, issue trackers, deployment systems.

GitHub is an Integration provider. GitHub itself is not a Skill, Workflow, TaskRun or completion authority.

### Capability

A Capability is the provider-neutral action contract requested by a Workflow, for example:

```text
test.run
quality.evaluate
vcs.diff.read
code_review.pull_request.create
ci.run.wait
```

The same capability can be implemented by different providers. `ci.run.wait` may resolve to GitHub Actions, GitLab CI or Jenkins without changing the Workflow definition.

## Activation

Registry presence does **not** mean a provider is configured or usable.

At activation time the caller supplies the actually available provider IDs. Capability preflight resolves required and optional capabilities against that activation set.

Missing required capability:

```text
BLOCKED_CONFIGURATION
```

Missing optional capability:

```text
PASS + missing_optional
```

This lets a local-only Workflow continue without GitHub while a Workflow that truly requires hosted code review can fail before execution starts.

## Write authority

Capability resolution never grants write authority.

A Workflow that requests any mutating capability must declare `write_governed=true`. Existing Change Contract / ChangePermit / `change-scope` controls remain authoritative for actual repository mutation.

## External wait

Capabilities such as `ci.run.wait` are marked `external_wait=true`.

This means a future Workflow runtime must create a durable external-wait checkpoint and yield execution. It must not keep a LangGraph node alive with an unbounded polling loop.

TaskRun remains the lifecycle authority and already supports `WAITING_EXTERNAL_RESULT`.

## Authority invariants

```text
Capability binding != write permit
Provider available != Workflow completed
CI green != TaskRun completed
LangGraph END != TaskRun completed
Skill success != TaskRun completed
```

TaskRun, ProblemLedger, Quality, durable Evidence and Completion Policy retain their existing authority boundaries.

## Current registries

```text
skill-system/registry/capabilities.json
skill-system/registry/executors.json
skill-system/registry/integrations.json
skill-system/registry/dev-workflows.json
```

Workflow requirements are provider-neutral. Concrete provider selection only occurs at activation/runtime.
