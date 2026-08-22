# Harness Invoke and Activation V1

## Goal

Expose one repository-local entry for explicit OPEN, Skill, and Workflow selection while preserving the existing runtime and authority boundaries.

```text
skillctl invoke
      |
      +--> no selector ----------> OPEN
      +--> --skill <exact-id> ---> canonical Skill context
      +--> --workflow <exact-id>
               + --composition-id <exact-id>
               |
               v
        CompositionBootstrap
               |
               v
        Capability activation
```

Selection is structural. Request text is never used to guess a Skill, Workflow, or similar capability.

## `harness-full-dev`

The full-development manifest owns only the ordered composition of already registered units:

```text
product-code-governance
        -> architecture-options
        -> repair-and-prove
        -> publication-e2e
        -> END
```

`publication-e2e` already owns its post-merge validation request and wait. The parent does not duplicate that stage.

`skill-system/registry/dev-workflows.json` carries the provider-neutral activation projection for `harness-full-dev`. Its direct Skills and required capabilities must equal the full-development manifest plus the exact requirements of its child Workflows. The loader fails closed when those projections drift.

`harness-full-dev-github` is the target composition. It binds the combined capability set to existing local and GitHub providers, including the existing local profile allow-lists. The Workflow manifest remains provider-neutral.

## Authority boundaries

- OPEN selection cannot silently activate a Skill or Workflow.
- Exact Skill selection loads context but creates no execution receipt. The real host must execute the Skill before `CanonicalSkillInvocationAdapter` may write the canonical receipt.
- Exact Workflow selection requires an exact Composition ID.
- Composition activation does not grant write authority.
- Provider binding does not activate a provider or permit a mutation.
- `FLOW_ENDED`, LangGraph END, CI GREEN, and Quality GREEN do not complete TaskRun.
- TaskRun remains the only lifecycle and completion authority.

## Current boundary

Invocation and activation remain selection-only boundaries. Nested child execution is provided separately by `full_development_child_runtime`: it delegates to the existing `langgraph_workflow_runtime`, injected `WorkflowAdapterDispatcher`, durable checkpointer, and `workflow_taskrun_bridge`. Direct parent Skill steps still require real Host execution and canonical receipts; selection alone cannot advance them.
