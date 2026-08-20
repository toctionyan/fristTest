# Harness Provider Adapters V1

## Purpose

This stage supplies concrete execution adapters behind the generic
`WorkflowAdapterDispatcher` without putting provider-specific logic inside the
LangGraph runtime.

```text
LangGraph
  -> WorkflowAdapterDispatcher
      -> local.process adapter
      -> event-driven CI adapter
```

The Workflow still declares provider-neutral capabilities such as `test.run`,
`quality.evaluate`, and `ci.run.wait`.

## local.process

`LocalProcessProviderAdapter` implements deterministic profile-backed execution
for:

```text
test.run
quality.evaluate
```

It does not accept arbitrary shell from Workflow state. Runtime composition
supplies an allow-list of permitted profile IDs for each capability, while the
active target selects a profile through:

```json
{
  "execution_profiles": {
    "focused-test": "focused-tests",
    "quality.evaluate": "quality-quick"
  }
}
```

The step ID can select a more specific profile than the generic capability.
Every invocation receives a unique attempt-scoped profile state path and writes a
provider result artifact under `.quality/workflow-provider-runs/`.

Outcome projection is deliberately narrow:

```text
profile PASS                    -> green
profile FAIL                    -> red
runner/configuration exception  -> blocked
```

The adapter does not change Quality authority or TaskRun completion conditions.

## Event-driven CI

`EventDrivenCIProviderAdapter` implements `ci.run.wait` for any configured CI
provider ID such as:

```text
github.actions
gitlab.ci
jenkins.ci
```

The Workflow topology does not change when the provider changes.

The active target supplies a durable, provider-neutral external handle:

```json
{
  "external_handles": {
    "ci.run.wait": {
      "correlation_ref": "run-123",
      "resume_event": "ci.completed"
    }
  }
}
```

On the first invocation the adapter writes wait evidence and returns:

```text
pending -> WAITING_EXTERNAL
```

It performs no polling.

A scheduler/integration listener later supplies one durable event:

```json
{
  "provider": "github.actions",
  "correlation_ref": "run-123",
  "event": "ci.completed",
  "conclusion": "success",
  "evidence_refs": ["github-run:123"]
}
```

The resumed adapter validates provider, correlation, event name, and durable event
evidence before returning `green`, `red`, or `blocked`.

## Authority invariants

```text
profile PASS        != TaskRun COMPLETED
profile FAIL        != ProblemLedger truth
Quality profile PASS!= completion authority
CI event GREEN      != TaskRun COMPLETED
provider adapter    != provider activation
provider binding    != write permit
```

The concrete adapters only execute and materialize evidence. Existing TaskRun,
ProblemLedger, Quality, Change Contract and completion authorities remain outside
this layer.

## Next composition work

The remaining composition layer can now wire:

```text
Workflow activation
  -> CanonicalSkillInvocationAdapter
  -> LocalProcessProviderAdapter
  -> EventDrivenCIProviderAdapter(provider_id=active CI)
  -> existing write-authority guard
  -> LangGraph runtime
  -> TaskRun bridge
```

After that, target-specific development workflows can be assembled without
adding GitHub branches or shell commands directly into LangGraph nodes.
