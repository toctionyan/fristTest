# Harness Composition Bootstrap V1

## Goal

Assemble validated workflow runtime components without creating a new authority layer.

## Boundary

```
Composition Registry
        |
        v
Composition Bootstrap
        |
        +--> Workflow Definition
        +--> Capability Provider Binding
        +--> Adapter Runtime
        +--> TaskRun Bridge
```

## Invariants

- Composition selects components, it does not authorize writes.
- Provider selection does not imply provider activation.
- Adapter success does not complete TaskRun.
- Quality remains the acceptance authority.
- TaskRun remains the lifecycle/completion authority.
- Bootstrap cannot bypass existing guards.

## Next stage

After this layer, add durable runtime assembly and end-to-end replay tests.
