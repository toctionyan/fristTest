# B30 WP-02A TurnRequestLedger Implementation Plan

## Bound inputs

This plan is valid only for B30 baseline `f9d3f63ddf68ec1e12c0258187a971720383716b` and the WP-02A TurnRequestLedger contract blob `1aeb3a7ac6ce10d6e421d96c8b06b7e818db50a1`. Moving the baseline, contract, taxonomy or blob invalidates review and any ChangePermit.

`WP-02A` owns only durable external request identity and replay. It cannot interpret user meaning, goals or dialogue references; those remain under sibling `WP-02B` through `TurnSemanticContract + TypedTargetSet + VisibleResultRef + SourceEffect`.

## Proven implementation seams

The current code establishes the failure mechanism directly:

- `ChatRequest` has no `client_request_id`, although structured transaction requests already require one.
- HTTP and SSE both append the user message inside `ConversationTurnService` before any durable request claim.
- `MessageRepository.add_message` and the concrete SQLite `MessageStore` plus SQLAlchemy message tables have no stable public `message_id` contract.
- `StoreProvider` has no `TurnRequestRepository`.
- `ChatPanel` already generates an optimistic user-row ID, but sends only `thread_id` and `message` to `/chat/turn`.

This is not a generic retry wrapper. It requires a first-class persistence authority before message append and graph invocation.

## Required scope amendment

The initial contract omitted two concrete files necessary to satisfy its own invariants:

- `app/services/agent_service.py` must receive the repository from the composed StoreProvider; it must not construct a private second authority.
- `persistence/message_store.py` contains `MessageStore`; it must accept the ledger-owned stable message identity and enforce idempotent user-message persistence.

The Repair Plan Reviewer must explicitly approve this amendment. The implementer cannot infer approval from this document alone.

## Eight implementation phases

### P1 — Repository types and digest

Add immutable scope/record/claim types, claim decisions and deterministic digest construction. The repository layer remains independent of FastAPI, LangGraph, model clients and WP-02B semantic contracts.

### P2 — SQLite authority

Create the composite unique scope key, atomic claim, monotonically increasing fencing token and owner-bound compare-and-set transitions. An expired RUNNING record returns recovery-required; it is not silently reclaimed.

### P3 — SQLAlchemy/PostgreSQL parity

Add the same table and repository semantics. Production schema-present mode must report a missing ledger schema rather than create it implicitly.

### P4 — Stable message identity

Extend `MessageStore` and SQLAlchemy message persistence with an optional stable `message_id`, preserving historical rows. Repeated insertion of the same identity and payload is idempotent; the same identity with different content is a conflict.

### P5 — API, composition and frontend

Require `ChatRequest.client_request_id`. Wire the StoreProvider repository into AgentService. `ChatPanel` generates one ID and uses it for both the optimistic row and request payload. No server fallback is permitted.

### P6 — HTTP/SSE integration

Claim under authenticated scope and conversation lease before message persistence or graph access. Only NEW_CLAIM may append or run. SUCCEEDED replays the stored response; conflict, in-progress and recovery-required states fail closed. HTTP and SSE share the same transport-neutral identity.

### P7 — Crash recovery and durability

Persist the canonical public response before ledger success and before returning success. Test crashes after claim, message append, graph completion and response persistence. Unknown completion enters reconciliation and never auto-runs the graph.

### P8 — Independent closure

Run focused, counterexample, concurrency, crash, backend parity, HTTP/SSE and complete regression suites. An independent Diff Reviewer verifies that no message/graph path bypasses the ledger or crosses into WP-02B. Product-source baseline refresh is performed by a separate control-plane actor only after approval.

## Prohibited shortcuts

- Reusing action `IdempotencyStore` as Turn authority.
- Generating a request ID on the server.
- Treating ConversationLease, TurnFence, checkpoint or process memory as request identity.
- Adding a compatibility route that bypasses the ledger.
- Automatically retrying an expired RUNNING or SUBMISSION_UNKNOWN record.
- Returning success before response persistence and fenced ledger completion.
- Letting WP-02A interpret user meaning or mutate `dialogue_runtime.py`/`context_runtime.py`.
- Allowing the product implementer to refresh its own product-source baseline.

## Exit rule

WP-02A closes only when the 13 frozen contract tests, all eight mandatory test dimensions, authority-separation counterexamples, independent diff review, protected-source baseline refresh and complete Quick regression pass. A partial backend, optional API field, HTTP-only implementation or semantic authority crossover is not closure.
