# B30 Authoritative Runtime Architecture

## Purpose

B30 freezes the project-wide authority model before Codex performs broad bug repair or feature expansion. It does not claim production certification. It removes architectural ambiguity so later work packages can be small, independently reviewed and reversible.

Machine-readable contracts:

- `governance/architecture/b30-authority-map.json`
- `governance/architecture/b30-legacy-retirement.json`
- `governance/architecture/b30-runtime-entrypoints.json`
- `governance/architecture/b30-semantic-context-authority.json`

Validation commands:

```bash
python3 -B scripts/validate_b30_architecture.py
python3 -B scripts/validate_b30_entrypoints.py
python3 -B scripts/validate_b30_semantic_context.py
```

## One authoritative runtime chain

```text
external request
  -> TurnRequestLedger
  -> ContextEvidenceProjection
  -> TurnSemanticContract
  -> CapabilitySurface + MatchProof
  -> PlanRun
  -> BusinessService + TransactionRepository
  -> RuntimeOutcome
  -> ResponseProjector
```

Context evidence is built before semantic freeze because the semantic compiler needs bounded, scoped history and verified references. `ContextEvidenceProjection` never chooses meaning or a target. It may be rebuilt later, but after freeze it cannot replace or mutate the `TurnSemanticContract`.

A later stage may reject or project evidence from an earlier stage; it may not reinterpret ownership that belongs to an earlier stage.

## Authority boundaries

| Concern | Sole authority | Forbidden alternatives |
|---|---|---|
| Request identity and deduplication | `TurnRequestLedger` | retry handler, graph node, client state |
| Eligible semantic evidence | `ContextEvidenceProjection` | model memory, free-text history scan, tool error, audit target selection |
| User meaning and typed goals | `TurnSemanticContract` | keyword router, per-intent parser, tool choice, ContextBundle |
| Dialogue references and sets | `TurnSemanticContract.TypedTargetSet` | ResultRef recency, referent-set projection, latest-object guess |
| Capability support | `CapabilitySurface + MatchProof` | similarity match, model-selected tool, legacy intent map |
| Executable plan | `PlanRun` | `grounded_execution_plan`, tool-loop scratch plan |
| Business facts | `BusinessService` | checkpoint, model answer, UI state |
| Transaction lifecycle | `TransactionRepository` | conversation frame, pending card, model memory |
| Public result semantics | `RuntimeOutcome` | HTTP adapter, SSE adapter, frontend component |

## Terminal decisions

A turn must end in one typed outcome: execute, read-only answer, clarify, unsupported, rejected, retryable failure, final failure or submission unknown. `UNSUPPORTED` is a normal explicit outcome. It must never be replaced by a similar capability. `CLARIFY` must not expose a write surface.

## Context model

Pronouns, corrections, subsets and references such as “它们”, “第二个”, “坏的” and “刚才那个” are resolved into typed targets inside the frozen semantic contract. `VisibleResultRef`, `SourceEffect` and visible referent sets provide scoped provenance only. They are not target selectors and are not dispatchable.

Individual intents and tools do not own independent history resolution. Focus is a revisioned UI/orchestration projection, not semantic authority.

## Transaction model

Agent code may propose a command, but `BusinessService` owns business legality and current facts. `TransactionRepository` owns Draft, Grant, Attempt, Receipt, idempotency, unknown submission and recovery. UI cards and conversation state are projections, not transaction authorities.

## Legacy retirement

Legacy fields may be read only by explicit migration adapters. Once canonical evidence exists, legacy fields cannot affect new decisions. Silent fallback is forbidden. Every retirement target requires discovery evidence, replacement evidence, a deletion condition and a rollback that does not recreate dual authority.

## Codex work packages

B30 is executed through top-level WP-01 to WP-08 from `b30-legacy-retirement.json`. Every work package has explicit allowed paths and exit conditions. Codex implementers may not widen scope, edit governance evidence, weaken tests or preserve a second authority for compatibility.

`WP-02` is a parent architecture boundary rather than one implementation task:

```text
WP-02 Request, semantic and target authority convergence
├── WP-02A Request identity and TurnRequestLedger
└── WP-02B Context evidence, semantic contract and typed target authority
```

`WP-02A` owns only durable external request identity, replay and one-Turn creation. `WP-02B` owns eligible semantic evidence, meaning, goals and typed references. The taxonomy is deliberately split so a request-idempotency implementation cannot reinterpret user meaning, and semantic code cannot create a second durable request authority.

## B30 exit criteria

B30 closes only when:

1. every HTTP, SSE and interaction entrypoint reaches the same authoritative chain;
2. every authority boundary has one runtime owner;
3. context evidence precedes semantic freeze and remains non-selecting;
4. unsupported, clarification, multi-goal, typed-reference and transaction counterexamples pass;
5. active legacy paths cannot bypass semantic, capability, plan or transaction authority;
6. HTTP, SSE and frontend are projections of the same RuntimeOutcome;
7. Skill, multi-agent, static, quick and architecture acceptance gates pass.

Real PostgreSQL/pgvector, configured model/RAG, dual-service and browser certification remain WP-08 evidence and are required before the later source-delivery candidate can claim production readiness.
