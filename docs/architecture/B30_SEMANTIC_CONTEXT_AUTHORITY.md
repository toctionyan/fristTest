# B30 WP-02B Semantic and Context Authority

## Position in the authoritative chain

`ContextEvidenceProjection` must run before semantic freeze because it supplies bounded, verified evidence for open-language understanding. It is not an authority for meaning or target selection.

```text
TurnRequestLedger
  -> ContextEvidenceProjection
  -> model semantic proposal
  -> deterministic validation
  -> frozen TurnSemanticContract
  -> CapabilitySurface + MatchProof
```

The context projection may be rebuilt for later model calls, but after the semantic contract is frozen it cannot rewrite the contract.

## Sole semantic authority

`TurnSemanticContract` is the only formal owner of current-turn meaning, business-effect Goals, corrections, dependencies and typed target relations. The model is the open-language compiler candidate; deterministic runtime validates literal evidence, scope, shape, revision and integrity before freeze.

Every `evidence_span` and domain-neutral `*_span` must be a contiguous literal substring of the current user message. Historical answers, tool diagnostics, audit summaries and model paraphrases cannot masquerade as current-turn text.

Capability absence, tool failure, plan failure or business rejection may change progress and outcome, but cannot rewrite `requested_effect` or user meaning.

## Typed target authority

A Goal carries one formal `TypedTargetSet` through its target candidate or input candidates inside the frozen semantic contract and revisioned GoalRecord. Supported relations include explicit entities, visible ResultRefs and members, sets, subsets, union, intersection, difference, filter, sort, position and continuation.

Ambiguity is fail-closed:

- one exact, scoped interpretation may proceed;
- multiple latest-visible refs from the same verified `source_effect_id` may represent one discourse scope;
- latest refs from different source effects remain distinct;
- a singular phrase with multiple valid scopes must produce Goal-scoped clarification;
- runtime cannot choose the newest, nearest or most similar object.

Changing `requested_effect` creates a new Goal and supersedes the previous Goal. `PATCH_GOAL` cannot silently change the business effect.

## Context evidence components

The bounded context projection may contain:

- provider-safe recent conversation exchanges;
- verified tool observations;
- execution diagnostics isolated from semantic facts;
- customer-visible scoped ResultRefs;
- non-dispatchable visible referent sets;
- active transaction projections;
- verified fact summaries;
- GoalRecord and GoalBlocker projections;
- an audit index available only through explicit audit inspection.

It must expose these hard properties:

```text
runtime_auto_select_target = false
runtime_auto_switch_target = false
referent_sets.dispatchable = false
audit metadata is not a target selector
tool failures are not business facts
```

## VisibleResultRef and SourceEffect

A `VisibleResultRef` proves that a scoped ledger result crossed the customer-visible release boundary. It is not focus, a business fact owner, permission to execute or an automatic target.

Runtime validates tenant, user, thread, active status, TTL, shape, version and presentation provenance. An exact member handle is usable only when membership in a released collection is proven. Structural lineage may support typed set operations but cannot select an unrelated topic.

`SourceEffect` groups references by the verified effect that produced them. It is provenance, not semantic classification.

## Visible referent sets

Visible referent sets are read-only discourse projections built from customer-visible refs. Only the latest visible turn and bounded contiguous recency prefixes are projected. They are never dispatchable. The model must propose exact refs or members, and runtime revalidates them.

## Goal, blocker and focus lifecycle

Goal lifecycle is semantic orchestration state, not business state. The only model-proposed changes are:

- `SET_GOAL_LIFECYCLE`
- `PATCH_GOAL`
- `SUPERSEDE_GOAL`

Each change binds the current revision and literal current-turn evidence. Clarification blockers are Goal-scoped and may coexist.

`focus_state` is a revisioned UI/orchestration projection only. It cannot select a business target or override the frozen semantic contract.

## Work-package separation

- `WP-02A` owns durable request identity and replay. WP-02B cannot create or complete a TurnRequestLedger record.
- `WP-03` owns capability support. WP-02B preserves unsupported requested effects for MatchProof absence.
- `WP-04` owns executable planning. PlanRun derives from the frozen contract and cannot reinterpret it.
- `WP-05` owns business facts and transaction state.
- `WP-06` projects `RuntimeOutcome` and cannot synthesize new semantics.

## Legacy retirement

Allowed compatibility is limited to explicit migration adapters and non-authoritative metadata such as legacy `goal_type`. Newly frozen turns must not use keyword routing, intent-map authority, per-intent pronoun resolution, per-tool target memory, latest-object guessing or free-text history scans as targets.

## Closure evidence

WP-02B requires direct, pronoun, set, subset, difference, position, correction, interruption, resumption, stale-reference, source-effect ambiguity, non-dispatchable referent-set, immutable semantic-contract and capability-failure counterexamples. The machine-readable contract is `governance/architecture/b30-semantic-context-authority.json`.
