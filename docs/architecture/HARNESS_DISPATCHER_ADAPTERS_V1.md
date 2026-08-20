# Harness Dispatcher / Adapter Layer V1

## Purpose

This layer connects the declarative LangGraph Workflow Runtime to real Skills,
deterministic Executors, external Integrations and Human Gates without moving any
existing authority into LangGraph or CapabilityResolver.

```text
Workflow graph step
      -> WorkflowAdapterDispatcher
          -> Skill adapter
          -> Provider adapter
          -> Human Gate adapter
      -> durable evidence
      -> LangGraph runtime
      -> TaskRun bridge
```

## Boundary

The dispatcher is not an authority. It routes already-validated steps to an
adapter that can actually execute them.

```text
Skill Invocation ledger     = proves canonical Skill execution
CapabilityResolver          = selects active provider
Provider adapter            = executes one provider capability
Change Contract / Permit    = write authority
TaskRun                      = lifecycle authority
ProblemLedger               = problem-closure authority
Quality                      = acceptance authority
LangGraph                    = orchestration only
```

A provider binding never grants write permission.

## Skill execution

A `skill` graph node uses `CanonicalSkillInvocationAdapter`.

The adapter delegates real execution to an injected host bridge. Only after that
host returns a materialized output does the adapter create the existing
`skill-invocation-receipt@1` record.

This prevents the Workflow Runtime from fabricating a Skill PASS merely because a
graph node was reached.

```text
LangGraph skill node
      -> real host Skill execution
      -> host output + evidence
      -> canonical Skill Invocation receipt
      -> StepDispatchResult
```

The adapter does not mark the output as a deterministic final user response and
does not change TaskRun or write authority.

## Executor and Integration dispatch

Executor, Gate and External Wait nodes receive a provider-neutral
`CapabilityBinding` produced during Workflow activation.

The dispatcher then requires one runtime adapter whose `provider_id` and
`provider_type` exactly match that binding.

```text
test.run
  -> CapabilityResolver
  -> local.process
  -> local.process runtime adapter

ci.run.wait
  -> CapabilityResolver
  -> github.actions
  -> github.actions runtime adapter
```

A registered provider is still not automatically active. The adapter table only
makes an implementation available for a provider already selected by activation.

## Mutating capabilities

For a capability with `mutates=true`, dispatch fails closed unless an injected
`WriteAuthorityGuard` confirms the existing write authority before the provider
adapter is called.

```text
workspace.write binding
      != write permit

workspace.write
      -> existing Change Contract / ChangePermit guard
      -> provider adapter
```

The dispatcher never creates or synthesizes a ChangePermit.

## External wait

An `external_wait` step may only receive a capability whose contract has
`external_wait=true`. A normal executor/gate step cannot silently consume a wait
capability.

The provider adapter returns a durable wait handle and the LangGraph runtime
performs the existing yield/resume behavior.

## Human Gate

Human Gate execution is injected separately and is not modeled as an Integration
provider. This keeps business/human decisions distinct from external system
capabilities.

## Fail-closed rules

Dispatch fails when any of the following is true:

- a Skill step has no real Skill execution adapter;
- a capability step has no activation binding;
- step capability and binding capability differ;
- the activated provider has no runtime adapter;
- provider adapter type differs from the binding;
- a mutating capability lacks the existing write-authority guard;
- an external-wait step receives a non-wait capability;
- a wait capability is used as a normal executor/gate step;
- a Human Gate has no Human Gate adapter.

## Next concrete adapters

The generic dispatcher intentionally does not hard-code GitHub or shell commands.
Concrete adapters can now be added independently:

```text
SkillHostAdapter
  -> ChatGPT/Codex host Skill bridge

CapabilityProviderAdapter
  -> local.workspace
  -> local.process
  -> local.git
  -> github.code_review
  -> github.actions
  -> gitlab.*
  -> jenkins.ci

HumanGateAdapter
  -> durable interaction contract
```

That means future GitLab/Jenkins support does not require changing the Workflow
Runtime, and a new Skill does not require changing provider adapters.
