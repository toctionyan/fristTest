# Harness Composition Bootstrap V1

## Goal

Assemble validated Workflow runtime components without creating a new authority layer.

The composition layer is intentionally thin. It binds an exact Workflow to a bounded set of configured providers and local execution profile allow-lists, then delegates capability resolution back to the existing Workflow activation/preflight path.

## Boundary

```text
Composition Registry
        |
        v
Composition Bootstrap
        |
        +--> exact Workflow Definition
        +--> available provider set
        +--> capability -> preferred provider binding
        +--> local profile allow-lists
        +--> Workflow activation / capability preflight
        |
        v
Adapter Runtime / LangGraph Runtime / TaskRun Bridge
```

## Registry shape

A composition names providers per capability rather than assuming one universal executor or one universal integration provider. This is required because one Workflow may legitimately need multiple executor providers, for example:

```text
workspace.write   -> local.workspace
 test.run         -> local.process
 quality.evaluate -> local.process
```

Likewise a later publication Workflow may bind CI waiting to `github.actions`, `gitlab.ci`, or `jenkins.ci` without changing Workflow topology.

Local process profiles are allow-listed per capability. The target may select only one of those allowed profile IDs at runtime; composition never accepts arbitrary shell commands.

## Invariants

- Composition selection does not authorize writes.
- Provider preference does not activate a provider.
- Provider availability does not grant capability authority beyond Workflow activation.
- Adapter success does not complete TaskRun.
- LangGraph END does not complete TaskRun.
- Quality remains the acceptance authority.
- TaskRun remains the lifecycle/completion authority.
- Completion authority is fixed to `TaskRun` in the composition registry.
- Unknown composition IDs fail closed; there is no fuzzy or similar-name fallback.
- A write-governed Workflow cannot be assembled unless the composition explicitly preserves the requirement for an external write authority.

## Current composition

`repair-and-prove-local` assembles the existing `repair-and-prove` Workflow with:

- `local.workspace` for `workspace.write` capability binding;
- `local.process` for deterministic `test.run` and `quality.evaluate` execution;
- repository-owned profile allow-lists for test and Quality execution.

The composition itself does not issue a Change Permit or any other write grant.

## Next stage

After this bootstrap is merged, the next bounded stage is publication capability composition:

```text
vcs commit / PR create / CI wait / governed merge / post-merge validation
```

Those capabilities should reuse existing repository publication governance and be exposed through provider adapters rather than reimplemented inside LangGraph or Skills. After that boundary is stable, `customer-agent-full-dev` can compose the end-to-end development lifecycle.
