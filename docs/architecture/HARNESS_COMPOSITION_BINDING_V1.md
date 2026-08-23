# Harness Composition and Extension Binding v1

## Purpose

Composition v1 makes Skill extension points operational without making them an
execution authority. A composition overlay selects compatible Skill contracts,
binds them to explicit graph anchors, and deterministically derives a new
`harness-workflow@1` declaration. The base Skill and Workflow files are never
modified.

The derived declaration is compiled by the existing Authoring Core and the
existing `WorkflowSpec` / `workflow_graph_contract` parser. The Composer does
not execute a Skill, choose a Provider, authorize a write, mark a TaskRun
complete, or interpret LangGraph state.

## Ownership

| Decision | Owner |
| --- | --- |
| Accepted extension types at a named hook | Host Skill contract |
| Exact graph location | Composition anchor |
| Order of multiple bindings | Composer (`order`, then binding ID) |
| Runtime graph validity | Existing Workflow graph contract |
| Capability Provider | Activation / Resolver |
| Mutating execution permission | Existing Write Authority Guard |
| Overall completion | TaskRun completion policy |

Capability propagation is a requirement declaration, not an authorization
grant. A mutating extension can only compose into a base Workflow that is
already `WRITE` and `write_governed: true`; the runtime Guard must still approve
the actual write.

## Three files, no base rewrite

The base Skill advertises hooks:

```yaml
schema: harness-skill-contract@1
skill: customer-agent-audit
version: 1.0.0
mode: read_only
inputs: [request-context@1]
outputs: [finding-set@1]
extension_type: procedure
extension_points:
  before-analysis: [context-provider, audit-lens]
  finding-enrichment: [finding-enricher]
  before-validation: [gate, audit-lens]
```

An independent extension Skill describes its contract:

```yaml
schema: harness-skill-contract@1
skill: customer-agent-policy-gate
version: 1.0.0
mode: read_only
inputs: [finding-set@1]
capabilities: [policy.evaluate]
outputs: [policy-verdict@1]
extension_type: gate
extension_points: {}
```

The overlay binds the extension. The hook name validates compatibility; the
anchor defines topology explicitly:

```yaml
schema: harness-composition@1
id: audit-customer-agent-with-policy
version: 1.1.0
base_workflow: audit-customer-agent
bindings:
  - id: customer-policy-gate
    host_skill: customer-agent-audit
    extension_skill: customer-agent-policy-gate
    at: before-validation
    anchor:
      kind: before_step
      step: quality
    order: 100
    routes:
      continue: $CONTINUE
      blocked: BLOCKED_UNRECOVERABLE
```

`before-validation` is not parsed as an instruction. Renaming it does not move
a graph node. Only `anchor.kind` and `anchor.step` select the insertion edge.

## Anchor semantics

### `before_step`

All current incoming routes to the named base step, including graph start, are
redirected through the ordered extension chain. Each `$CONTINUE` route advances
to the next extension and finally to the original step.

Use it for:

- context acquisition before the first analysis step;
- a standards or security gate before validation;
- an approval check before an existing mutation step.

### `after_route`

One named outcome edge from a base step is redirected through the ordered
extension chain. `$CONTINUE` reaches the edge's current destination. This
preserves composition with a `before_step` chain on that destination.

```yaml
anchor:
  kind: after_route
  step: inspect
  outcome: issues
```

Use it for finding enrichment, report annotation, or a specialized check that
should run only for one outcome.

## Deterministic multi-Skill composition

Bindings sharing one anchor are sorted by ascending `order`, then lexical
binding ID. Declaration order is not authority. The composed plan records the
resolved order, base digest, overlay digest, every used Skill-contract digest,
and one aggregate `provenance_sha256`.

The following conflict classes fail closed:

- duplicate binding IDs or duplicate binding identities;
- duplicate Skill contracts;
- binding ID collision with a base graph step;
- missing host or extension contract;
- host Skill absent from the base Workflow;
- undeclared hook or incompatible extension type;
- missing anchor step or route outcome;
- extension route without `$CONTINUE`;
- artifact incompatibility;
- mutating extension applied to a read-only Workflow;
- Provider-specific capability token rejected by the canonical compiler.

## Artifact compatibility

The rules are intentionally small and deterministic:

- a `context-provider` must produce at least one artifact listed in the host
  Skill inputs;
- other extension inputs must be included in the host Skill inputs or outputs;
- a `finding-enricher` must consume at least one host output.

These checks prove interface compatibility; they do not claim that the Skill
was executed or that its output is valid. Runtime receipts and Quality evidence
remain required.

## CLI

Validate all four open declaration types:

```bash
python3 -B skillctl.py authoring validate \
  customer-agent-audit.skill.yaml \
  customer-agent-policy-gate.skill.yaml \
  audit-customer-agent.workflow.yaml \
  customer-agent-policy.composition.yaml
```

Compose and print a provenance-bound plan:

```bash
python3 -B skillctl.py authoring compose \
  --workflow audit-customer-agent.workflow.yaml \
  --composition customer-agent-policy.composition.yaml \
  --skill-contract customer-agent-audit.skill.yaml \
  --skill-contract customer-agent-policy-gate.skill.yaml
```

Persist both the review manifest and the portable derived Workflow:

```bash
python3 -B skillctl.py authoring compose \
  --workflow audit-customer-agent.workflow.yaml \
  --composition customer-agent-policy.composition.yaml \
  --skill-contract customer-agent-audit.skill.yaml \
  --skill-contract customer-agent-policy-gate.skill.yaml \
  --output build/customer-agent-policy.composed-plan.json \
  --derived-workflow-output build/audit-customer-agent-with-policy.workflow.json
```

The derived Workflow is ordinary `harness-workflow@1`. It can be versioned,
reviewed, compiled, and later run without ChatGPT or Codex. The customer Agent
produced with Harness remains a standalone application; the Harness and these
authoring files are development tooling, not a required application runtime.

## Customer Agent examples

### Overall audit plus project standards

Base Workflow discovers general architecture, context, Tool, transaction, test,
and observability findings. `customer-agent-policy-gate` is inserted before the
existing Quality step. Its findings are additive. It cannot remove or replace
the general audit findings because it receives artifacts through a separate
Skill invocation and has no authority over the base audit step.

### Module audit plus context specialist

A `customer-agent-context-audit` Skill can bind at `finding-enrichment` on the
`inspect.issues` edge. The base audit still discovers unrelated defects. The
extension enriches the finding set with referent resolution, collection
filtering, stale-context, and cross-turn counterexamples.

### Repair and prove

A mutating repair Skill declares `workspace.write`. It may bind only to a base
repair Workflow already declared as `WRITE` and `write_governed`. Composition
adds the Skill and capability requirement, while the existing Guard still
requires the TaskRun's bounded write authority. Local tests, Quality evaluation,
and optional CI wait remain normal downstream graph nodes.

### Adding another check later

Create one new Skill contract and add one overlay binding. Do not edit the
original Skill implementation. Do not copy the base Workflow. Re-run `compose`,
review the changed provenance and derived graph, then compile and test. Existing
bindings remain byte-for-semantics stable unless their inputs, order, or source
contracts changed.

## Current boundary and next layer

Composition v1 consumes explicit declaration files. A future natural-language
Composer may generate the same overlay from `/harness add-skill ...` or a ChatGPT
request, but generated output must pass this deterministic boundary. Natural
language is convenience; `harness-composition@1` remains the reviewable,
version-controlled intermediate artifact.
